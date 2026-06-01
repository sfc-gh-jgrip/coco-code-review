"""Top-level PR review publisher: orchestrates inline comments, sticky, checks, reactions.

Publisher.publish() executes the full post sequence:
1. Collect existing fingerprints (bot-authored only) for deduplication.
2. Partition findings into new vs. skipped.
3. Render and post inline review comments via create_review (batched, single notification).
4. Fetch back review comments → attach reactions (best-effort, isolated).
5. Upsert sticky summary comment.
6. Create Checks API run with severity table + annotations.
7. Return PublishReport reflecting what succeeded.

All LLM-derived text passes through sanitize_fn before leaving the system.
The hardcoded ``output.title = "Coco PR Review Results"`` is the only documented exemption.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from github import GithubException

from coco_pr_review.github.checks import publish_check_run
from coco_pr_review.github.fingerprints import (
    collect_existing_fingerprints,
    format_fingerprint_marker,
    hash_finding_fingerprint,
    parse_marker,
)
from coco_pr_review.github.reactions import attach_reactions
from coco_pr_review.github.sticky import (
    render_sticky_final,
    upsert_sticky_comment,
)
from coco_pr_review.severity import emoji_for

# ---------------------------------------------------------------------------
# Default bot login
# ---------------------------------------------------------------------------

_DEFAULT_BOT_LOGIN = "github-actions[bot]"


# ---------------------------------------------------------------------------
# PublishReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublishReport:
    """Summary of what the publisher did (or failed to do)."""

    comments_posted: int
    comments_skipped: int  # fingerprint-matched duplicates
    check_run_id: int  # 0 when checks API unreachable
    sticky_comment_id: int  # 0 when sticky write failed
    reactions_attached: int
    reactions_failed: int
    skipped_reason: str | None = None  # "fork-pr-no-write" | "pat-no-checks" | None


# ---------------------------------------------------------------------------
# Inline comment body rendering
# ---------------------------------------------------------------------------


def render_inline_comment_body(
    finding: Any,
    sanitize_fn: Callable[[str], str],
) -> str:
    """Render the markdown body for an inline review comment.

    Structure:
    - Title line with severity emoji and confidence
    - Comment text
    - Optional suggestion block (when suggested_fix is not None)
    - Optional collapsible verifier reasoning (when verifier_reasoning is not None)
    - Fingerprint marker as the last line
    """
    emoji = emoji_for(finding.severity)
    confidence_str = f"{finding.confidence}%" if finding.confidence is not None else "?%"

    parts: list[str] = []

    # Title line
    parts.append(f"**{finding.title}** ({emoji} {finding.severity}, {confidence_str} confidence)")
    parts.append("")

    # Comment body
    parts.append(finding.comment)

    # Suggested fix (optional)
    if finding.suggested_fix is not None:
        parts.append("")
        parts.append("```suggestion")
        parts.append(finding.suggested_fix)
        parts.append("```")

    # Verifier reasoning (optional, collapsible)
    if finding.verifier_reasoning is not None:
        parts.append("")
        parts.append("<details><summary>Verifier reasoning</summary>")
        parts.append("")
        parts.append(finding.verifier_reasoning)
        parts.append("")
        parts.append("</details>")

    # Fingerprint marker (always last)
    fp = hash_finding_fingerprint(
        file=finding.file,
        start_line=finding.start_line,
        end_line=finding.end_line,
        title=finding.title,
        evidence=finding.evidence,
    )
    parts.append("")
    parts.append(format_fingerprint_marker(fp))

    body = "\n".join(parts)
    return sanitize_fn(body)


# ---------------------------------------------------------------------------
# Publisher class
# ---------------------------------------------------------------------------


class Publisher:
    """Orchestrates posting review findings to GitHub.

    Constructor takes a pre-built Github instance — no auth resolution here.
    """

    def __init__(
        self,
        *,
        github: Any,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        sanitize_fn: Callable[[str], str],
        bot_login: str = _DEFAULT_BOT_LOGIN,
    ) -> None:
        self._github = github
        self._repo_full_name = repo_full_name
        self._pr_number = pr_number
        self._head_sha = head_sha
        self._sanitize_fn = sanitize_fn
        self._bot_login = bot_login

    def publish(self, run_result: Any, phase: str = "final") -> PublishReport:
        """Execute the full post sequence. Idempotent on re-run via fingerprints."""
        repo = self._github.get_repo(self._repo_full_name)
        pr = repo.get_pull(self._pr_number)

        # 1. Collect existing fingerprints from review comments.
        # When bot_login is set, filter to bot-authored comments only (prevents
        # user quote-replies from poisoning dedup). When comments lack user info
        # (e.g. in tests with bare mocks), parse markers from all comments.
        existing_comments = list(pr.get_review_comments())
        seen_fps: set[str] = set()
        for comment in existing_comments:
            # Best-effort bot-login filter: if the comment has a user.login
            # that is a real string and doesn't match bot_login, skip it.
            try:
                login = comment.user.login
                if isinstance(login, str) and login != self._bot_login:
                    continue
            except (AttributeError, TypeError):
                pass
            fp = parse_marker(comment.body or "")
            if fp is not None:
                seen_fps.add(fp)

        # 2. Partition findings into new vs. skipped.
        #
        # Pre-existing findings (real defects outside the PR's changed lines)
        # are NEVER posted inline — GitHub rejects inline comments on lines that
        # are not part of the diff. They still flow to the check-run annotations
        # and the sticky summary via ``run_result.findings`` (unfiltered below).
        new_findings: list[Any] = []
        skipped_findings: list[Any] = []

        for finding in run_result.findings:
            if getattr(finding, "pre_existing", False):
                # Routed to check-run + sticky only; never an inline comment.
                continue
            fp = hash_finding_fingerprint(
                file=finding.file,
                start_line=finding.start_line,
                end_line=finding.end_line,
                title=finding.title,
                evidence=finding.evidence,
            )
            if fp in seen_fps:
                skipped_findings.append(finding)
            else:
                new_findings.append(finding)

        # Track results
        comments_posted = 0
        reactions_attached = 0
        reactions_failed = 0
        skipped_reason: str | None = None
        sticky_comment_id: int = 0
        check_run_id: int = 0

        # 3. Post inline comments via create_review (if any new findings)
        if new_findings:
            review_comments = []
            for finding in new_findings:
                body = render_inline_comment_body(finding, self._sanitize_fn)
                comment_dict: dict[str, Any] = {
                    "path": finding.file,
                    "line": finding.end_line,
                    "side": "RIGHT",
                    "body": body,
                }
                # Multi-line support
                if finding.start_line != finding.end_line:
                    comment_dict["start_line"] = finding.start_line
                    comment_dict["start_side"] = "RIGHT"
                review_comments.append(comment_dict)

            try:
                commit = repo.get_commit(self._head_sha)
                review = pr.create_review(
                    commit=commit,
                    body="",
                    event="COMMENT",
                    comments=review_comments,
                )
                comments_posted = len(new_findings)

                # 4. Attach reactions to posted comments (best-effort)
                try:
                    review_comment_objs = pr.get_single_review_comments(review.id)
                    for comment_obj in review_comment_objs:
                        report = attach_reactions(comment_obj)
                        if report.thumbsup:
                            reactions_attached += 1
                        else:
                            reactions_failed += 1
                except Exception:
                    # Reaction fetch failure is non-fatal; all attempted count as failed
                    reactions_failed += comments_posted

            except GithubException as exc:
                if exc.status in (403, 404):
                    skipped_reason = "fork-pr-no-write"
                elif exc.status == 422:
                    # GitHub rejected the inline batch — typically a comment
                    # targets a line not in the diff (e.g. the line moved since
                    # the verifier judged it in-diff). This is non-fatal: every
                    # finding still surfaces via the check-run annotations and
                    # the sticky summary below. Record the degradation; do not
                    # raise and lose the run.
                    if skipped_reason is None:
                        skipped_reason = "inline-rejected"
                else:
                    raise

        # 5. Upsert sticky summary comment
        try:
            sticky_body = render_sticky_final(
                findings=run_result.findings,
                posted=comments_posted,
                skipped=len(skipped_findings),
                reviewer_failures=getattr(run_result, "reviewer_failures", 0) or 0,
                stats=getattr(run_result, "stats", None),
            )
            sticky_comment = upsert_sticky_comment(pr, sticky_body, self._sanitize_fn, bot_login=self._bot_login)
            sticky_comment_id = getattr(sticky_comment, "id", None) or 0
        except Exception:
            pass

        # 6. Create Checks API run
        try:
            check_run_id = publish_check_run(
                repo, self._head_sha, run_result.findings, self._sanitize_fn
            )
            if check_run_id == 0:
                if skipped_reason is None:
                    skipped_reason = "pat-no-checks"
        except GithubException as exc:
            if exc.status in (403, 404):
                if skipped_reason is None:
                    skipped_reason = "pat-no-checks"
            else:
                raise

        return PublishReport(
            comments_posted=comments_posted,
            comments_skipped=len(skipped_findings),
            check_run_id=check_run_id,
            sticky_comment_id=sticky_comment_id,
            reactions_attached=reactions_attached,
            reactions_failed=reactions_failed,
            skipped_reason=skipped_reason,
        )
