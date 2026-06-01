"""Tests for `coco_pr_review.github.checks` — Checks API formatter and publisher."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, call


def _make_finding(
    *,
    file: str = "src/foo.py",
    start_line: int = 10,
    end_line: int = 12,
    severity: str = "blocker",
    title: str = "Bug found",
    comment: str = "Fix this bug",
    evidence: str = "x = 1 / 0",
    pre_existing: bool = False,
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
    f.pre_existing = pre_existing
    return f


# ---------------------------------------------------------------------------
# render_checks_output_text — severity table
# ---------------------------------------------------------------------------


def test_render_checks_output_text_contains_severity_table() -> None:
    """Output text contains a markdown severity table with emoji per row."""
    from coco_pr_review.github.checks import render_checks_output_text

    findings = [
        _make_finding(severity="blocker", title="Race condition", file="src/a.ts", start_line=1, end_line=2),
        _make_finding(severity="warning", title="Missing null check", file="src/b.ts", start_line=5, end_line=6),
        _make_finding(severity="nit", title="Unused import", file="src/c.ts", start_line=10, end_line=10),
    ]

    text = render_checks_output_text(findings)

    assert "🔴" in text
    assert "🟡" in text
    assert "⚪" in text


def test_render_checks_output_text_has_valid_json_tag() -> None:
    """The trailing JSON tag contains valid JSON with correct severity counts."""
    from coco_pr_review.github.checks import render_checks_output_text

    findings = [
        _make_finding(severity="blocker"),
        _make_finding(severity="blocker"),
        _make_finding(severity="warning"),
        _make_finding(severity="nit"),
        _make_finding(severity="nit"),
        _make_finding(severity="nit"),
    ]

    text = render_checks_output_text(findings)

    # Extract JSON from the tag
    import re

    match = re.search(
        r"<!--\s*coco-pr-review-severity:\s*(\{.*?\})\s*-->", text
    )
    assert match is not None, "JSON severity tag not found in output text"

    data = json.loads(match.group(1))
    assert data == {"blocker": 2, "warning": 1, "nit": 3}


def test_render_checks_output_text_json_counts_match_findings() -> None:
    """JSON tag counts accurately reflect the actual finding counts by severity."""
    from coco_pr_review.github.checks import render_checks_output_text

    findings = [
        _make_finding(severity="warning"),
        _make_finding(severity="warning"),
    ]

    text = render_checks_output_text(findings)

    import re

    match = re.search(
        r"<!--\s*coco-pr-review-severity:\s*(\{.*?\})\s*-->", text
    )
    assert match is not None
    data = json.loads(match.group(1))
    assert data["blocker"] == 0
    assert data["warning"] == 2
    assert data["nit"] == 0


# ---------------------------------------------------------------------------
# render_checks_output_text — severity emoji per row
# ---------------------------------------------------------------------------


def test_severity_table_uses_correct_emoji_per_severity() -> None:
    """🔴 for blocker, 🟡 for warning, ⚪ for nit."""
    from coco_pr_review.github.checks import render_checks_output_text

    findings = [
        _make_finding(severity="blocker", title="Blocker issue"),
        _make_finding(severity="warning", title="Warning issue"),
        _make_finding(severity="nit", title="Nit issue"),
    ]

    text = render_checks_output_text(findings)

    lines = text.split("\n")
    # Find lines containing each title and verify their emoji
    for line in lines:
        if "Blocker issue" in line:
            assert "🔴" in line
        if "Warning issue" in line:
            assert "🟡" in line
        if "Nit issue" in line:
            assert "⚪" in line


# ---------------------------------------------------------------------------
# findings_to_annotations
# ---------------------------------------------------------------------------


def test_findings_to_annotations_returns_correct_dict_shape() -> None:
    """Each annotation dict has: path, start_line, end_line, annotation_level, message, title."""
    from coco_pr_review.github.checks import findings_to_annotations

    findings = [_make_finding()]
    annotations = findings_to_annotations(findings)

    assert len(annotations) == 1
    ann = annotations[0]
    assert set(ann.keys()) >= {"path", "start_line", "end_line", "annotation_level", "message", "title"}
    assert ann["path"] == "src/foo.py"
    assert ann["start_line"] == 10
    assert ann["end_line"] == 12
    assert ann["title"] == "Bug found"
    assert ann["message"] == "Fix this bug"


def test_findings_to_annotations_prefixes_pre_existing_title() -> None:
    """Pre-existing findings get a [Pre-existing] title prefix in annotations."""
    from coco_pr_review.github.checks import findings_to_annotations

    findings = [_make_finding(title="Latent bug", pre_existing=True)]
    annotations = findings_to_annotations(findings)

    assert annotations[0]["title"] == "[Pre-existing] Latent bug"


def test_findings_to_annotations_maps_severity_to_annotation_level() -> None:
    """blocker→failure, warning→warning, nit→notice."""
    from coco_pr_review.github.checks import findings_to_annotations

    findings = [
        _make_finding(severity="blocker"),
        _make_finding(severity="warning"),
        _make_finding(severity="nit"),
    ]

    annotations = findings_to_annotations(findings)

    levels = [a["annotation_level"] for a in annotations]
    assert levels == ["failure", "warning", "notice"]


# ---------------------------------------------------------------------------
# publish_check_run
# ---------------------------------------------------------------------------


def test_publish_check_run_calls_create_check_run_with_correct_args() -> None:
    """publish_check_run creates a check run with the expected parameters."""
    from coco_pr_review.github.checks import publish_check_run

    repo_mock = MagicMock()
    check_run_mock = MagicMock()
    check_run_mock.id = 12345
    repo_mock.create_check_run.return_value = check_run_mock

    findings = [_make_finding(), _make_finding(severity="nit")]
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    publish_check_run(repo_mock, "abc123" * 7 + "ab", findings, sanitize_fn)

    repo_mock.create_check_run.assert_called_once()
    call_kwargs = repo_mock.create_check_run.call_args[1]
    assert call_kwargs["name"] == "Coco PR Review"
    assert call_kwargs["head_sha"] == "abc123" * 7 + "ab"
    assert call_kwargs["status"] == "completed"
    assert call_kwargs["conclusion"] == "neutral"
    assert "output" in call_kwargs
    assert "annotations" in call_kwargs["output"]


def test_publish_check_run_sanitizes_output_text() -> None:
    """sanitize_fn is called on the output text before posting."""
    from coco_pr_review.github.checks import publish_check_run

    repo_mock = MagicMock()
    repo_mock.create_check_run.return_value = MagicMock(id=1)

    findings = [_make_finding()]
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    publish_check_run(repo_mock, "a" * 40, findings, sanitize_fn)

    # sanitize_fn must be called at least once (for the output text)
    assert sanitize_fn.call_count >= 1


# ---------------------------------------------------------------------------
# publish_check_run — 50-annotation batching
# ---------------------------------------------------------------------------


def test_publish_check_run_batches_annotations_over_50() -> None:
    """With >50 findings, first 50 go in create_check_run, rest in edit calls with cumulative list."""
    from coco_pr_review.github.checks import publish_check_run

    repo_mock = MagicMock()
    check_run_mock = MagicMock()
    check_run_mock.id = 999
    repo_mock.create_check_run.return_value = check_run_mock

    # 75 findings total
    findings = [_make_finding(start_line=i, end_line=i + 1) for i in range(75)]
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    publish_check_run(repo_mock, "b" * 40, findings, sanitize_fn)

    # create_check_run called once with at most 50 annotations
    repo_mock.create_check_run.assert_called_once()
    create_output = repo_mock.create_check_run.call_args[1]["output"]
    assert len(create_output["annotations"]) == 50

    # edit called at least once; final edit has all 75 annotations (cumulative)
    assert check_run_mock.edit.call_count >= 1
    last_edit_output = check_run_mock.edit.call_args[1]["output"]
    assert len(last_edit_output["annotations"]) == 75
