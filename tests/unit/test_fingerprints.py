"""Tests for `coco_pr_review.github.fingerprints` — finding fingerprints + comment-marker parsing."""
from __future__ import annotations


def test_fingerprint_is_deterministic_for_identical_inputs() -> None:
    """Tracer bullet: same finding inputs yield the same fingerprint."""
    from coco_pr_review.github.fingerprints import fingerprint

    fp_a = fingerprint(
        file="src/foo.py",
        start_line=42,
        end_line=44,
        title="Division by zero",
        evidence="    return a / b",
    )
    fp_b = fingerprint(
        file="src/foo.py",
        start_line=42,
        end_line=44,
        title="Division by zero",
        evidence="    return a / b",
    )

    assert fp_a == fp_b
    # sha256 hex is 64 chars
    assert len(fp_a) == 64


def test_fingerprint_differs_when_any_field_changes() -> None:
    """Each fingerprint-input field affects the output — single-field flips collide if not."""
    from coco_pr_review.github.fingerprints import fingerprint

    base = dict(
        file="src/foo.py",
        start_line=42,
        end_line=44,
        title="Division by zero",
        evidence="    return a / b",
    )
    fp_base = fingerprint(**base)

    assert fingerprint(**{**base, "file": "src/bar.py"}) != fp_base
    assert fingerprint(**{**base, "start_line": 43}) != fp_base
    assert fingerprint(**{**base, "end_line": 45}) != fp_base
    assert fingerprint(**{**base, "title": "Different title"}) != fp_base
    assert fingerprint(**{**base, "evidence": "    return b / a"}) != fp_base


def test_parse_marker_extracts_hash_from_typical_comment() -> None:
    """A typical bot comment body containing the marker yields the hash."""
    from coco_pr_review.github.fingerprints import parse_marker

    body = (
        "**Token refresh races with logout** (🔴 blocker, 92% confidence)\n\n"
        "Comment body here.\n\n"
        "<!-- coco-pr-review:fp=a" + "0" * 63 + " -->"
    )

    assert parse_marker(body) == "a" + "0" * 63


def test_parse_marker_returns_none_when_marker_absent() -> None:
    """Unmarked comments (e.g., user replies, other bots) yield None."""
    from coco_pr_review.github.fingerprints import parse_marker

    assert parse_marker("Just a regular comment, no marker.") is None
    assert parse_marker("") is None


def test_parse_marker_returns_none_for_malformed_marker() -> None:
    """Marker with wrong key or short hash yields None — strict format only."""
    from coco_pr_review.github.fingerprints import parse_marker

    # wrong key
    assert parse_marker("<!-- some-other-bot:fp=" + "a" * 64 + " -->") is None
    # short hash (62 chars)
    assert parse_marker("<!-- coco-pr-review:fp=" + "a" * 62 + " -->") is None
    # hash with non-hex chars
    assert parse_marker("<!-- coco-pr-review:fp=ZZZ" + "0" * 61 + " -->") is None
