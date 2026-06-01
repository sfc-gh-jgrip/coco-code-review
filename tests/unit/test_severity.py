"""Tests for `coco_pr_review.severity` — the single source of severity vocabulary."""
from __future__ import annotations


def test_severities_are_ordered_most_to_least_severe() -> None:
    from coco_pr_review.severity import SEVERITIES

    assert SEVERITIES == ("blocker", "warning", "nit")


def test_emoji_for_known_severities() -> None:
    from coco_pr_review.severity import emoji_for

    assert emoji_for("blocker") == "🔴"
    assert emoji_for("warning") == "🟡"
    assert emoji_for("nit") == "⚪"


def test_emoji_for_unknown_severity_falls_back() -> None:
    from coco_pr_review.severity import UNKNOWN_SEVERITY_EMOJI, emoji_for

    assert emoji_for("catastrophic") == UNKNOWN_SEVERITY_EMOJI


def test_severity_rank_orders_blocker_before_nit() -> None:
    from coco_pr_review.severity import severity_rank

    assert severity_rank("blocker") < severity_rank("warning") < severity_rank("nit")


def test_severity_rank_sorts_unknown_last() -> None:
    from coco_pr_review.severity import SEVERITIES, severity_rank

    assert severity_rank("mystery") == len(SEVERITIES)
    assert severity_rank("mystery") > severity_rank("nit")


def test_schema_severity_enum_uses_canonical_vocabulary() -> None:
    """The JSON-schema severity enum must come from the canonical SEVERITIES."""
    from coco_pr_review.schema import FINDING_SCHEMA
    from coco_pr_review.severity import SEVERITIES

    assert FINDING_SCHEMA["properties"]["severity"]["enum"] == list(SEVERITIES)
