"""Tests for Publisher inline comment body rendering."""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_finding(
    *,
    file: str = "src/foo.py",
    start_line: int = 10,
    end_line: int = 12,
    severity: str = "blocker",
    title: str = "Token refresh races with logout",
    comment: str = "The token refresh call may execute concurrently with the logout handler.",
    evidence: str = "    await this.refresh()",
    confidence: int = 92,
    verifier_reasoning: str | None = "Trace analysis confirms race window between refresh and logout.",
    suggested_fix: str | None = None,
    category: str = "correctness",
) -> MagicMock:
    """Create a mock finding with the standard shape."""
    f = MagicMock()
    f.file = file
    f.start_line = start_line
    f.end_line = end_line
    f.severity = severity
    f.title = title
    f.comment = comment
    f.evidence = evidence
    f.confidence = confidence
    f.verifier_reasoning = verifier_reasoning
    f.suggested_fix = suggested_fix
    f.category = category
    return f


# ---------------------------------------------------------------------------
# render_inline_comment_body — structure
# ---------------------------------------------------------------------------


def test_render_inline_comment_body_contains_title_with_severity_emoji() -> None:
    """The rendered body has a title line with the severity emoji and confidence percentage."""
    from coco_pr_review.github.publisher import render_inline_comment_body

    finding = _make_finding(severity="blocker", confidence=92)
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    body = render_inline_comment_body(finding, sanitize_fn)

    assert "🔴" in body
    assert "92%" in body or "92" in body
    assert finding.title in body


def test_render_inline_comment_body_contains_comment_text() -> None:
    """The rendered body includes the finding's comment/body text."""
    from coco_pr_review.github.publisher import render_inline_comment_body

    finding = _make_finding(comment="This is a critical bug in the auth flow.")
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    body = render_inline_comment_body(finding, sanitize_fn)

    assert "This is a critical bug in the auth flow." in body


def test_render_inline_comment_body_has_collapsible_verifier_reasoning() -> None:
    """When verifier_reasoning is present, it's wrapped in <details>."""
    from coco_pr_review.github.publisher import render_inline_comment_body

    finding = _make_finding(verifier_reasoning="Trace shows race window of 50ms.")
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    body = render_inline_comment_body(finding, sanitize_fn)

    assert "<details>" in body
    assert "<summary>" in body
    assert "Verifier reasoning" in body
    assert "Trace shows race window of 50ms." in body
    assert "</details>" in body


def test_render_inline_comment_body_omits_details_when_no_reasoning() -> None:
    """When verifier_reasoning is None, no <details> block appears."""
    from coco_pr_review.github.publisher import render_inline_comment_body

    finding = _make_finding(verifier_reasoning=None)
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    body = render_inline_comment_body(finding, sanitize_fn)

    assert "<details>" not in body


def test_render_inline_comment_body_has_fingerprint_marker_as_last_line() -> None:
    """The fingerprint marker is the LAST line of the rendered body."""
    from coco_pr_review.github.publisher import render_inline_comment_body

    finding = _make_finding()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    body = render_inline_comment_body(finding, sanitize_fn)

    lines = body.rstrip().split("\n")
    last_line = lines[-1].strip()
    assert last_line.startswith("<!-- coco-pr-review:fp=")
    assert last_line.endswith("-->")


# ---------------------------------------------------------------------------
# render_inline_comment_body — sanitize_fn
# ---------------------------------------------------------------------------


def test_render_inline_comment_body_calls_sanitize_fn() -> None:
    """sanitize_fn is called on the assembled body before returning."""
    from coco_pr_review.github.publisher import render_inline_comment_body

    finding = _make_finding()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    render_inline_comment_body(finding, sanitize_fn)

    sanitize_fn.assert_called_once()


# ---------------------------------------------------------------------------
# render_inline_comment_body — severity emoji mapping
# ---------------------------------------------------------------------------


def test_render_inline_comment_body_uses_warning_emoji() -> None:
    """Warning findings get the 🟡 emoji."""
    from coco_pr_review.github.publisher import render_inline_comment_body

    finding = _make_finding(severity="warning")
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    body = render_inline_comment_body(finding, sanitize_fn)

    assert "🟡" in body


def test_render_inline_comment_body_uses_nit_emoji() -> None:
    """Nit findings get the ⚪ emoji."""
    from coco_pr_review.github.publisher import render_inline_comment_body

    finding = _make_finding(severity="nit")
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    body = render_inline_comment_body(finding, sanitize_fn)

    assert "⚪" in body
