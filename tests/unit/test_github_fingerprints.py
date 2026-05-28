"""Tests for `coco_pr_review.github.fingerprints` — fingerprint hashing, marker parsing, and formatting."""
from __future__ import annotations


# ---------------------------------------------------------------------------
# hash_finding_fingerprint: determinism
# ---------------------------------------------------------------------------


def test_hash_finding_fingerprint_returns_stable_hex() -> None:
    """Identical inputs always yield the same 64-char lowercase hex digest."""
    from coco_pr_review.github.fingerprints import hash_finding_fingerprint

    fp_a = hash_finding_fingerprint(
        file="src/auth/session.ts",
        start_line=142,
        end_line=145,
        title="Token refresh races with logout",
        evidence="    await this.refresh()",
    )
    fp_b = hash_finding_fingerprint(
        file="src/auth/session.ts",
        start_line=142,
        end_line=145,
        title="Token refresh races with logout",
        evidence="    await this.refresh()",
    )

    assert fp_a == fp_b
    assert len(fp_a) == 64
    assert all(c in "0123456789abcdef" for c in fp_a)


# ---------------------------------------------------------------------------
# hash_finding_fingerprint: collision sanity
# ---------------------------------------------------------------------------


def test_hash_finding_fingerprint_differs_when_file_changes() -> None:
    """Different file paths produce different fingerprints."""
    from coco_pr_review.github.fingerprints import hash_finding_fingerprint

    base = dict(
        file="src/foo.py",
        start_line=10,
        end_line=12,
        title="Bug",
        evidence="x = 1",
    )
    assert hash_finding_fingerprint(**base) != hash_finding_fingerprint(
        **{**base, "file": "src/bar.py"}
    )


def test_hash_finding_fingerprint_differs_when_line_changes() -> None:
    """Different line ranges produce different fingerprints."""
    from coco_pr_review.github.fingerprints import hash_finding_fingerprint

    base = dict(
        file="src/foo.py",
        start_line=10,
        end_line=12,
        title="Bug",
        evidence="x = 1",
    )
    assert hash_finding_fingerprint(**base) != hash_finding_fingerprint(
        **{**base, "start_line": 11}
    )
    assert hash_finding_fingerprint(**base) != hash_finding_fingerprint(
        **{**base, "end_line": 13}
    )


def test_hash_finding_fingerprint_differs_when_title_changes() -> None:
    """Different titles produce different fingerprints."""
    from coco_pr_review.github.fingerprints import hash_finding_fingerprint

    base = dict(
        file="src/foo.py",
        start_line=10,
        end_line=12,
        title="Bug",
        evidence="x = 1",
    )
    assert hash_finding_fingerprint(**base) != hash_finding_fingerprint(
        **{**base, "title": "Different bug"}
    )


def test_hash_finding_fingerprint_differs_when_evidence_changes() -> None:
    """Different evidence strings produce different fingerprints."""
    from coco_pr_review.github.fingerprints import hash_finding_fingerprint

    base = dict(
        file="src/foo.py",
        start_line=10,
        end_line=12,
        title="Bug",
        evidence="x = 1",
    )
    assert hash_finding_fingerprint(**base) != hash_finding_fingerprint(
        **{**base, "evidence": "y = 2"}
    )


# ---------------------------------------------------------------------------
# parse_fingerprint_from_comment
# ---------------------------------------------------------------------------


def test_parse_fingerprint_from_comment_extracts_hex_from_body() -> None:
    """A comment body containing the coco-pr-review marker yields the 64-char hex."""
    from coco_pr_review.github.fingerprints import parse_fingerprint_from_comment

    hex_str = "a" * 64
    body = (
        "**Bug title** (🔴 blocker, 92% confidence)\n\n"
        "Some comment.\n\n"
        f"<!-- coco-pr-review:fp={hex_str} -->"
    )
    assert parse_fingerprint_from_comment(body) == hex_str


def test_parse_fingerprint_from_comment_returns_none_when_missing() -> None:
    """Unmarked comments yield None."""
    from coco_pr_review.github.fingerprints import parse_fingerprint_from_comment

    assert parse_fingerprint_from_comment("Just a plain comment.") is None
    assert parse_fingerprint_from_comment("") is None


def test_parse_fingerprint_from_comment_tolerates_surrounding_markdown() -> None:
    """The marker can be anywhere in the body, surrounded by markdown."""
    from coco_pr_review.github.fingerprints import parse_fingerprint_from_comment

    hex_str = "b" * 64
    body = (
        "# Big heading\n\n"
        "Lots of markdown here.\n\n"
        f"<!-- coco-pr-review:fp={hex_str} -->\n\n"
        "And trailing content after the marker."
    )
    assert parse_fingerprint_from_comment(body) == hex_str


# ---------------------------------------------------------------------------
# format_fingerprint_marker
# ---------------------------------------------------------------------------


def test_format_fingerprint_marker_produces_correct_html_comment() -> None:
    """format_fingerprint_marker returns the exact marker format."""
    from coco_pr_review.github.fingerprints import format_fingerprint_marker

    hex_str = "c" * 64
    result = format_fingerprint_marker(hex_str)
    assert result == f"<!-- coco-pr-review:fp={hex_str} -->"


# ---------------------------------------------------------------------------
# Round-trip: format → parse
# ---------------------------------------------------------------------------


def test_format_then_parse_round_trips() -> None:
    """Formatting a hex then parsing it back returns the original hex."""
    from coco_pr_review.github.fingerprints import (
        format_fingerprint_marker,
        parse_fingerprint_from_comment,
    )

    hex_str = "d1e2f3" + "0" * 58  # 64 chars total
    marker = format_fingerprint_marker(hex_str)
    body = f"Some preamble text\n\n{marker}\n\nSome trailing text"
    assert parse_fingerprint_from_comment(body) == hex_str


# ---------------------------------------------------------------------------
# Normalization: CRLF vs LF produces identical fingerprints
# ---------------------------------------------------------------------------


def test_fingerprint_normalization_crlf_and_lf_produce_same_hash() -> None:
    """CRLF and LF variants of the same finding produce identical hashes."""
    from coco_pr_review.github.fingerprints import hash_finding_fingerprint

    fp_lf = hash_finding_fingerprint(
        file="src/foo.py",
        start_line=10,
        end_line=12,
        title="Bug with\nnewline",
        evidence="line1\nline2",
    )
    fp_crlf = hash_finding_fingerprint(
        file="src/foo.py",
        start_line=10,
        end_line=12,
        title="Bug with\r\nnewline",
        evidence="line1\r\nline2",
    )

    assert fp_lf == fp_crlf


# ---------------------------------------------------------------------------
# collect_existing_fingerprints: non-bot comments are excluded
# ---------------------------------------------------------------------------


def test_fingerprint_filters_non_bot_comments() -> None:
    """A non-bot user comment with a fingerprint marker is ignored by collect_existing_fingerprints."""
    from unittest.mock import MagicMock
    from coco_pr_review.github.fingerprints import (
        collect_existing_fingerprints,
        format_fingerprint_marker,
    )

    hex_str = "a" * 64
    # A comment authored by a regular user
    user_comment = MagicMock()
    user_comment.body = f"I copied this marker: {format_fingerprint_marker(hex_str)}"
    user_comment.user.login = "some-human-user"

    # A comment authored by the bot
    bot_comment = MagicMock()
    bot_hex = "b" * 64
    bot_comment.body = f"Bot finding\n{format_fingerprint_marker(bot_hex)}"
    bot_comment.user.login = "github-actions[bot]"

    result = collect_existing_fingerprints(
        [user_comment, bot_comment],
        bot_login="github-actions[bot]",
    )

    # Only the bot comment's fingerprint should be collected
    assert hex_str not in result
    assert bot_hex in result
