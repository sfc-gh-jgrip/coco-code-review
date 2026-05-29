"""Sticky summary comment: find/edit/render.

The sticky comment is a single issue comment on the PR that contains
a marker (``<!-- coco-pr-review:summary -->``) so it can be located on
subsequent runs and edited in place rather than creating new comments.

Concurrency note: if two runs update the sticky simultaneously, this is
a benign last-writer-wins race — both writers produce a valid final state.
"""
from __future__ import annotations

from typing import Any, Callable

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
) -> str:
    """Render the final sticky comment body after publishing.

    Includes the summary marker, a severity breakdown table, and a per-finding
    list (title, location, category) so reviewers can see what was flagged
    without scrolling through inline comments.
    """
    severity_emoji = {"blocker": "🔴", "warning": "🟡", "nit": "⚪"}
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

    if findings:
        # Sort blocker → warning → nit so the most important findings lead.
        severity_rank = {"blocker": 0, "warning": 1, "nit": 2}
        ordered = sorted(findings, key=lambda f: severity_rank.get(f.severity, 3))
        lines.extend(["", "### Findings"])
        for finding in ordered:
            emoji = severity_emoji.get(finding.severity, "⚪")
            location = f"`{finding.file}:{finding.start_line}`"
            lines.append(f"- {emoji} **{finding.title}** — {location} ({finding.category})")

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
