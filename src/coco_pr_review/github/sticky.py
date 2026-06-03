"""Sticky summary comment: find/edit/render.

The sticky comment is a single issue comment on the PR that contains
a marker (``<!-- coco-pr-review:summary -->``) so it can be located on
subsequent runs and edited in place rather than creating new comments.

Concurrency note: if two runs update the sticky simultaneously, this is
a benign last-writer-wins race — both writers produce a valid final state.
"""
from __future__ import annotations

from typing import Any, Callable

from coco_pr_review.severity import (
    PRE_EXISTING_EMOJI,
    emoji_for,
    severity_rank,
)

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


def _render_analysis_summary(stats: Any) -> list[str]:
    """Render the analysis funnel as a collapsible section.

    Surfaces the full pipeline funnel (reviewers → candidates → dedupe →
    verifier filtering → verified) so a zero-finding result is legible as
    "everything was analyzed and nothing survived the filters" rather than
    "the pipeline silently did nothing".
    """
    reviewers = ", ".join(stats.reviewer_names) if stats.reviewer_names else "none"
    dropped_total = (
        stats.dropped_verifier_error
        + stats.dropped_unparseable
        + stats.dropped_low_confidence
        + stats.dropped_evidence_mismatch
        + stats.dropped_not_in_pr
    )
    pre_existing = getattr(stats, "pre_existing", 0)
    files_read = getattr(stats, "files_read", 0)
    return [
        "",
        "<details>",
        "<summary>Analysis summary</summary>",
        "",
        f"- **Reviewers** ({len(stats.reviewer_names)}): {reviewers}",
        f"- **Replicas**: {stats.replicas_dispatched} dispatched · "
        f"{stats.replicas_succeeded} succeeded · {stats.replicas_failed} failed",
        f"- **Context**: {files_read} file(s) read by reviewers",
        f"- **Candidates**: {stats.raw_candidates} raw → "
        f"{stats.deduped_candidates} after dedupe",
        f"- **Verified**: {stats.verified} (survived all filters) · "
        f"{pre_existing} pre-existing surfaced",
        f"- **Filtered out** ({dropped_total}): "
        f"low confidence {stats.dropped_low_confidence} · "
        f"evidence mismatch {stats.dropped_evidence_mismatch} · "
        f"out-of-diff dropped {stats.dropped_not_in_pr} · "
        f"verifier error {stats.dropped_verifier_error} · "
        f"unparseable {stats.dropped_unparseable} "
        f"(confidence threshold ≥ {stats.confidence_threshold})",
        "",
        "</details>",
    ]


def render_sticky_final(
    *,
    findings: list[Any],
    posted: int,
    skipped: int,
    reviewer_failures: int = 0,
    stats: Any = None,
) -> str:
    """Render the final sticky comment body after publishing.

    Includes the summary marker, a severity breakdown table, and a per-finding
    list (title, location, category) so reviewers can see what was flagged
    without scrolling through inline comments.

    When ``reviewer_failures`` is non-zero the run completed but some reviewer
    replicas failed (e.g. unparseable output), so results may be incomplete; a
    warning note is added to make that visible rather than implying a clean run.

    When ``stats`` is provided, a collapsible "Analysis summary" funnel is
    appended so a zero-finding result is distinguishable from a broken run.

    Pre-existing findings (real defects outside the PR's changed lines) are
    listed in their own section and excluded from the in-diff severity table,
    since they were not introduced by this PR.
    """
    in_diff = [f for f in findings if not getattr(f, "pre_existing", False)]
    pre_existing = [f for f in findings if getattr(f, "pre_existing", False)]

    blocker_count = sum(1 for f in in_diff if f.severity == "blocker")
    warning_count = sum(1 for f in in_diff if f.severity == "warning")
    nit_count = sum(1 for f in in_diff if f.severity == "nit")
    total = len(in_diff)

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

    if in_diff:
        # Most-severe first; severity ordering is owned by the severity module.
        ordered = sorted(in_diff, key=lambda f: severity_rank(f.severity))
        lines.extend(["", "### Findings"])
        for finding in ordered:
            location = f"`{finding.file}:{finding.start_line}`"
            lines.append(
                f"- {emoji_for(finding.severity)} **{finding.title}** — {location} ({finding.category})"
            )

    if pre_existing:
        ordered_pre = sorted(pre_existing, key=lambda f: severity_rank(f.severity))
        lines.extend(
            [
                "",
                f"### {PRE_EXISTING_EMOJI} Pre-existing issues "
                f"(not introduced by this PR)",
                "",
                f"{len(pre_existing)} real defect(s) found outside this PR's changed "
                "lines. These are reported here and in the check run, but not as "
                "inline comments (GitHub only allows inline comments on changed lines).",
            ]
        )
        for finding in ordered_pre:
            location = f"`{finding.file}:{finding.start_line}`"
            lines.append(
                f"- {PRE_EXISTING_EMOJI} {emoji_for(finding.severity)} "
                f"**{finding.title}** — {location} ({finding.category})"
            )

    if stats is not None:
        lines.extend(_render_analysis_summary(stats))

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


def render_sticky_unverified(*, candidate_count: int, stats: Any = None) -> str:
    """Render the sticky body when candidates were detected but none verified.

    This is the "smells like a wrong source tree" case: reviewers proposed
    ``candidate_count`` findings, but the verifier confirmed zero. The most
    common cause is the review running against the wrong commit (e.g. a comment
    trigger that checked out the default branch instead of the PR head), so the
    verifier couldn't confirm the findings against files that aren't present.

    Posting an honest diagnostic here — rather than a bare "0 findings" — keeps
    a transient/mismatched run from silently overwriting a prior good review
    sticky with a misleading clean bill of health.
    """
    candidate_word = "candidate" if candidate_count == 1 else "candidates"
    lines = [
        "## 🤖 Coco PR Review",
        SUMMARY_MARKER,
        "",
        f"⚠️ {candidate_count} {candidate_word} detected, but none could be verified.",
        "",
        "This is not a clean review. It usually means the review ran against "
        "the wrong source tree (e.g. the wrong commit was checked out), so the "
        "verifier could not confirm the findings against the changed files. "
        "Please re-run the review.",
    ]
    if stats is not None:
        lines.extend(_render_analysis_summary(stats))
    return "\n".join(lines)


def choose_sticky_body(run_result: Any, *, posted: int = 0, skipped: int = 0) -> str:
    """Pick the right sticky body for a completed run (the WS-A diagnostic guard).

    Single source of truth shared by the PR publisher and the branch (commit)
    publisher: when candidates survived dedupe but *none* verified, post the
    honest "could not verify" diagnostic instead of a misleading "0 findings"
    (which would also clobber a prior good sticky). Otherwise render the final
    summary. ``posted``/``skipped`` are inline-comment counts (always 0 for the
    branch path, which posts no inline comments).
    """
    if not run_result.findings and getattr(run_result, "deduped_count", 0):
        return render_sticky_unverified(
            candidate_count=run_result.deduped_count,
            stats=getattr(run_result, "stats", None),
        )
    return render_sticky_final(
        findings=run_result.findings,
        posted=posted,
        skipped=skipped,
        reviewer_failures=getattr(run_result, "reviewer_failures", 0) or 0,
        stats=getattr(run_result, "stats", None),
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
