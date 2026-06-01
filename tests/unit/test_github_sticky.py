"""Tests for `coco_pr_review.github.sticky` — sticky summary comment find/edit/render."""
from __future__ import annotations

from unittest.mock import MagicMock, call


# ---------------------------------------------------------------------------
# find_sticky_comment
# ---------------------------------------------------------------------------


def test_find_sticky_comment_returns_matching_comment() -> None:
    """find_sticky_comment returns the IssueComment whose body contains the summary marker."""
    from coco_pr_review.github.sticky import find_sticky_comment

    marker = "<!-- coco-pr-review:summary -->"
    matching_comment = MagicMock()
    matching_comment.body = f"## 🤖 Coco PR Review\n{marker}\n\nSome content"

    other_comment = MagicMock()
    other_comment.body = "Just a regular comment"

    pr_mock = MagicMock()
    pr_mock.get_issue_comments.return_value = [other_comment, matching_comment]

    result = find_sticky_comment(pr_mock)
    assert result is matching_comment


def test_find_sticky_comment_returns_none_when_no_marker() -> None:
    """When no comment contains the marker, returns None."""
    from coco_pr_review.github.sticky import find_sticky_comment

    comment_a = MagicMock()
    comment_a.body = "Looks good to me!"

    comment_b = MagicMock()
    comment_b.body = "LGTM, ship it"

    pr_mock = MagicMock()
    pr_mock.get_issue_comments.return_value = [comment_a, comment_b]

    result = find_sticky_comment(pr_mock)
    assert result is None


def test_find_sticky_comment_ignores_non_bot_marker_when_bot_login_provided() -> None:
    """Bot login filtering ignores human comments that contain the sticky marker."""
    from coco_pr_review.github.sticky import find_sticky_comment

    marker = "<!-- coco-pr-review:summary -->"

    human_comment = MagicMock()
    human_comment.body = f"Quoting the bot\n{marker}"
    human_comment.user.login = "some-human-user"

    bot_comment = MagicMock()
    bot_comment.body = f"## Coco PR Review\n{marker}\n\nActual sticky"
    bot_comment.user.login = "github-actions[bot]"

    pr_mock = MagicMock()
    pr_mock.get_issue_comments.return_value = [human_comment, bot_comment]

    result = find_sticky_comment(pr_mock, bot_login="github-actions[bot]")

    assert result is bot_comment


# ---------------------------------------------------------------------------
# upsert_sticky_comment — edit path
# ---------------------------------------------------------------------------


def test_upsert_sticky_comment_edits_existing_when_found() -> None:
    """When a sticky comment already exists, upsert calls comment.edit with sanitized body."""
    from coco_pr_review.github.sticky import upsert_sticky_comment

    marker = "<!-- coco-pr-review:summary -->"
    existing_comment = MagicMock()
    existing_comment.body = f"Old content\n{marker}"

    pr_mock = MagicMock()
    pr_mock.get_issue_comments.return_value = [existing_comment]

    sanitize_fn = MagicMock(side_effect=lambda x: x)
    new_body = "New content with marker"

    upsert_sticky_comment(pr_mock, new_body, sanitize_fn)

    existing_comment.edit.assert_called_once()
    pr_mock.create_issue_comment.assert_not_called()


# ---------------------------------------------------------------------------
# upsert_sticky_comment — create path
# ---------------------------------------------------------------------------


def test_upsert_sticky_comment_creates_when_not_found() -> None:
    """When no sticky comment exists, upsert creates a new issue comment."""
    from coco_pr_review.github.sticky import upsert_sticky_comment

    pr_mock = MagicMock()
    pr_mock.get_issue_comments.return_value = []

    sanitize_fn = MagicMock(side_effect=lambda x: x)
    new_body = "Brand new sticky"

    upsert_sticky_comment(pr_mock, new_body, sanitize_fn)

    pr_mock.create_issue_comment.assert_called_once()


def test_upsert_sticky_comment_edits_bot_comment_when_human_marker_exists() -> None:
    """Upsert targets the bot-authored sticky even if a human comment contains the marker."""
    from coco_pr_review.github.sticky import upsert_sticky_comment

    marker = "<!-- coco-pr-review:summary -->"

    human_comment = MagicMock()
    human_comment.body = f"Quoted summary\n{marker}"
    human_comment.user.login = "some-human-user"

    bot_comment = MagicMock()
    bot_comment.body = f"Old sticky\n{marker}"
    bot_comment.user.login = "github-actions[bot]"

    pr_mock = MagicMock()
    pr_mock.get_issue_comments.return_value = [human_comment, bot_comment]

    sanitize_fn = MagicMock(side_effect=lambda body: body)

    upsert_sticky_comment(
        pr_mock,
        "Updated sticky body",
        sanitize_fn,
        bot_login="github-actions[bot]",
    )

    bot_comment.edit.assert_called_once_with(body="Updated sticky body")
    human_comment.edit.assert_not_called()
    pr_mock.create_issue_comment.assert_not_called()


# ---------------------------------------------------------------------------
# upsert_sticky_comment — sanitize_fn invoked BEFORE post
# ---------------------------------------------------------------------------


def test_upsert_sticky_comment_sanitizes_before_posting() -> None:
    """sanitize_fn is called on the body before either edit or create."""
    from coco_pr_review.github.sticky import upsert_sticky_comment

    pr_mock = MagicMock()
    pr_mock.get_issue_comments.return_value = []  # create path

    sanitize_fn = MagicMock(return_value="SANITIZED")
    new_body = "Unsanitized body with AKIAIOSFODNN7EXAMPLE"

    upsert_sticky_comment(pr_mock, new_body, sanitize_fn)

    sanitize_fn.assert_called_once_with(new_body)
    # The create call should receive the sanitized version
    pr_mock.create_issue_comment.assert_called_once_with(body="SANITIZED")


# ---------------------------------------------------------------------------
# render_sticky_progress
# ---------------------------------------------------------------------------


def test_render_sticky_progress_includes_marker_and_phase() -> None:
    """render_sticky_progress returns markdown starting with the marker and showing phase info."""
    from coco_pr_review.github.sticky import render_sticky_progress

    run_result = MagicMock()
    run_result.findings = [MagicMock(), MagicMock(), MagicMock()]

    body = render_sticky_progress(phase="reviewing", run_result=run_result)

    assert "<!-- coco-pr-review:summary -->" in body
    assert "reviewing" in body


def test_render_sticky_progress_contains_finding_count() -> None:
    """The progress render includes the number of findings from run_result."""
    from coco_pr_review.github.sticky import render_sticky_progress

    run_result = MagicMock()
    run_result.findings = [MagicMock() for _ in range(5)]

    body = render_sticky_progress(phase="verifying", run_result=run_result)

    assert "5" in body


def _make_finding(
    *,
    severity: str,
    title: str,
    file: str,
    start_line: int,
    category: str,
):
    """Build a minimal Finding for sticky-render tests."""
    from coco_pr_review.orchestration.base import Finding

    return Finding(
        file=file,
        start_line=start_line,
        end_line=start_line,
        severity=severity,
        category=category,
        title=title,
        evidence="evidence",
        comment="comment",
    )


def test_render_sticky_final_lists_findings_with_title_and_location() -> None:
    """The final sticky lists each finding's title, file:line, and category."""
    from coco_pr_review.github.sticky import render_sticky_final

    findings = [
        _make_finding(
            severity="blocker",
            title="Null deref on empty list",
            file="demo_bug.py",
            start_line=10,
            category="correctness",
        ),
        _make_finding(
            severity="nit",
            title="Prefer f-string",
            file="util.py",
            start_line=3,
            category="style",
        ),
    ]

    body = render_sticky_final(findings=findings, posted=2, skipped=0)

    assert "### Findings" in body
    assert "Null deref on empty list" in body
    assert "`demo_bug.py:10`" in body
    assert "(correctness)" in body
    assert "Prefer f-string" in body
    assert "`util.py:3`" in body
    # Severity table still present.
    assert "| 🔴 blocker | 1 |" in body


def test_render_sticky_final_orders_blocker_before_nit() -> None:
    """Findings are listed blocker → warning → nit regardless of input order."""
    from coco_pr_review.github.sticky import render_sticky_final

    findings = [
        _make_finding(
            severity="nit", title="Nit one", file="a.py", start_line=1, category="style"
        ),
        _make_finding(
            severity="blocker", title="Blocker one", file="b.py", start_line=2, category="security"
        ),
    ]

    body = render_sticky_final(findings=findings, posted=2, skipped=0)

    assert body.index("Blocker one") < body.index("Nit one")


def test_render_sticky_final_omits_findings_section_when_empty() -> None:
    """With zero findings, no Findings list is appended."""
    from coco_pr_review.github.sticky import render_sticky_final

    body = render_sticky_final(findings=[], posted=0, skipped=0)

    assert "### Findings" not in body
    assert "0 total findings" in body


def test_render_sticky_skipped_mentions_reason() -> None:
    """Skip sticky rendering includes the shared marker and reason text."""
    from coco_pr_review.github.sticky import render_sticky_skipped

    body = render_sticky_skipped(reason="pull request author is a bot account")

    assert "<!-- coco-pr-review:summary -->" in body
    assert "pull request author is a bot account" in body


def test_render_sticky_diff_too_large_mentions_split_guidance() -> None:
    """Large-diff sticky rendering asks the author to split the PR."""
    from coco_pr_review.github.sticky import render_sticky_diff_too_large

    body = render_sticky_diff_too_large(max_diff_lines=2000)

    assert "2000 changed lines" in body
    assert "Consider splitting this change into smaller PRs" in body


def test_render_sticky_failed_includes_marker_and_disclaims_clean_review() -> None:
    """Failed sticky carries the marker, the reason, and a 'not clean' disclaimer."""
    from coco_pr_review.github.sticky import SUMMARY_MARKER, render_sticky_failed

    body = render_sticky_failed(reason="all reviewer replicas failed")

    assert SUMMARY_MARKER in body
    assert "Review failed" in body
    assert "all reviewer replicas failed" in body
    # Must NOT read as a clean bill of health.
    assert "not" in body.lower()
    assert "re-run" in body.lower()


def test_render_sticky_final_adds_degraded_note_when_replicas_failed() -> None:
    """A non-zero reviewer_failures count surfaces a partial-degradation warning."""
    from coco_pr_review.github.sticky import render_sticky_final

    body = render_sticky_final(findings=[], posted=0, skipped=0, reviewer_failures=2)

    assert "2 reviewer replicas failed" in body
    assert "results may be incomplete" in body


def test_render_sticky_final_omits_degraded_note_when_no_failures() -> None:
    """Default (zero failures) renders no degradation warning."""
    from coco_pr_review.github.sticky import render_sticky_final

    body = render_sticky_final(findings=[], posted=0, skipped=0)

    assert "results may be incomplete" not in body
