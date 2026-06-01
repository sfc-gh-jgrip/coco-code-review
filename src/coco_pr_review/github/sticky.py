"""Sticky summary comment: find/edit/render.

The sticky comment is a single issue comment on the PR that contains
a marker (``<!-- coco-pr-review:summary -->``) so it can be located on
subsequent runs and edited in place rather than creating new comments.

Concurrency note: if two runs update the sticky simultaneously, this is
a benign last-writer-wins race — both writers produce a valid final state.
"""
from __future__ import annotations

from typing import Any, Callable

from coco_pr_review.severity import emoji_for, severity_rank

SUMMARY_MARKER = "<!-- coco-pr-review:summary -->"


def find_sticky_comment(pr: Any, bot_login: str | None = None) -> Any | None:
    """Find the existing sticky summary comment on a PR.

    Scans issue comments for one whose body contains the summary marker
    AND (when *bot_login* is provided) was authored by the bot.  The
    bot_login filter prevents us from accidentally editing a user comment
    that happens to contain the marker text (e.g., via quote-reply).

    Returns the first matching comment, or None if not found.
    """
    for comment in pr.get_issue_comments():
        if SUMMARY_MARKER not in (comment.body or ""):
            continue
        if bot_login is not None:
            try:
                if comment.user.login != bot_login:
                    continue
            except (AttributeError, TypeError):
                pass
        return comment
    return None


def upsert_sticky_comment(
    pr: Any,
    body: str,
    sanitize_fn: Callable[[str], str],
    bot_login: str | None = None,
) -> Any:
    """Create or update the sticky summary comment.

    If an existing comment with the summary marker is found, it is edited
    in place.  Otherwise, a new issue comment is created.

    The body is passed through sanitize_fn before any GitHub API call.
    Returns the comment object (created or edited).
    """
    sanitized_body = sanitize_fn(body)
    existing = find_sticky_comment(pr, bot_login=bot_login)

    if existing is not None:
        existing.edit(body=sanitized_body)
        return existing

    return pr.create_issue_comment(body=sanitized_body)


def render_sticky_progress(
    *,
    phase: str,
    run_result: Any,
) -> str:
    """Render the sticky comment body showing current progress.

    Includes the summary marker, phase information, and finding count.
    """
    count = len(run_result.findings) if run_result.findings else 0
    lines = [
        f"## 🤖 Coco PR Review — {phase}",
        SUMMARY_MARKER,
        "",
        f"Found {count} findings so far.",
        "",
        f"Phase: {phase}",
    ]
    return "\n".join(lines)


def render_sticky_final(
    *,
    findings: list[Any],
    posted: int,
    skipped: int,
    reviewer_failures: int = 0,
) -> str:
    """Render the final sticky comment body after publishing.

    Includes the summary marker, a severity breakdown table, and a per-finding
    list (title, location, category) so reviewers can see what was flagged
    without scrolling through inline comments.

    When ``reviewer_failures`` is non-zero the run completed but some reviewer
    replicas failed (e.g. unparseable output), so results may be incomplete; a
    warning note is added to make that visible rather than implying a clean run.
    """
    blocker_count = sum(1 for f in findings if f.severity == "blocker")
    warning_count = sum(1 for f in findings if f.severity == "warning")
    nit_count = sum(1 for f in findings if f.severity == "nit")
    total = len(findings)

    lines = [
        "## 🤖 Coco PR Review",
        SUMMARY_MARKER,
        "",
        f"✅ Review complete — posted {posted} findings"
        f" ({blocker_count} 🔴, {warning_count} 🟡, {nit_count} ⚪)",
        "",
        "| Severity | Count |",
        "|----------|-------|",
        f"| 🔴 blocker | {blocker_count} |",
        f"| 🟡 warning | {warning_count} |",
        f"| ⚪ nit | {nit_count} |",
        "",
        f"{total} total findings · {posted} posted · {skipped} skipped (duplicates)",
    ]

    if reviewer_failures:
        replica_word = "replica" if reviewer_failures == 1 else "replicas"
        lines.extend(
            [
                "",
                f"⚠️ {reviewer_failures} reviewer {replica_word} failed; "
                "results may be incomplete.",
            ]
        )

    if findings:
        # Most-severe first; severity ordering is owned by the severity module.
        ordered = sorted(findings, key=lambda f: severity_rank(f.severity))
        lines.extend(["", "### Findings"])
        for finding in ordered:
            location = f"`{finding.file}:{finding.start_line}`"
            lines.append(
                f"- {emoji_for(finding.severity)} **{finding.title}** — {location} ({finding.category})"
            )

    return "\n".join(lines)


def render_sticky_skipped(*, reason: str, details: str | None = None) -> str:
    """Render an early-exit sticky comment body."""
    lines = [
        "## 🤖 Coco PR Review",
        SUMMARY_MARKER,
        "",
        f"⏭️ Review skipped: {reason}",
    ]
    if details:
        lines.extend(["", details])
    return "\n".join(lines)


def render_sticky_diff_too_large(*, max_diff_lines: int) -> str:
    """Render the sticky body for PRs that exceed the review size limit."""
    return render_sticky_skipped(
        reason="pull request diff is too large for a reliable automated review",
        details=(
            f"This review only runs on PRs with at most {max_diff_lines} changed lines. "
            "Consider splitting this change into smaller PRs so the review can focus on the relevant code paths."
        ),
    )


def render_sticky_failed(*, reason: str, details: str | None = None) -> str:
    """Render the sticky body for a run that aborted before publishing.

    Distinct from ``render_sticky_skipped`` (a deliberate, benign no-review):
    this announces that the review attempted to run but failed (e.g. every
    reviewer replica errored, or the budget tripped), so the absence of inline
    comments must NOT be read as "no issues found". The user should re-run.
    """
    lines = [
        "## 🤖 Coco PR Review",
        SUMMARY_MARKER,
        "",
        f"❌ Review failed: {reason}",
        "",
        "This is an infrastructure failure, **not** a clean bill of health — "
        "no findings were published. Please re-run the review.",
    ]
    if details:
        lines.extend(["", details])
    return "\n".join(lines)
