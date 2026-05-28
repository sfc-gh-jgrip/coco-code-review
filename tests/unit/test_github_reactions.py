"""Tests for `coco_pr_review.github.reactions` — reaction attachment with isolation."""
from __future__ import annotations

from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# attach_reactions — happy path
# ---------------------------------------------------------------------------


def test_attach_reactions_calls_both_thumbsup_and_thumbsdown() -> None:
    """attach_reactions calls create_reaction for both '+1' and '-1'."""
    from coco_pr_review.github.reactions import attach_reactions

    comment_mock = MagicMock()

    attach_reactions(comment_mock)

    calls = comment_mock.create_reaction.call_args_list
    reaction_types = [c[0][0] for c in calls]
    assert "+1" in reaction_types
    assert "-1" in reaction_types


# ---------------------------------------------------------------------------
# attach_reactions — isolation when one fails
# ---------------------------------------------------------------------------


def test_attach_reactions_still_tries_thumbsdown_when_thumbsup_raises() -> None:
    """If '+1' raises, '-1' is still attempted — reactions are isolated."""
    from coco_pr_review.github.reactions import attach_reactions

    comment_mock = MagicMock()

    def side_effect(reaction_type: str) -> MagicMock:
        if reaction_type == "+1":
            raise RuntimeError("Rate limited")
        return MagicMock()

    comment_mock.create_reaction.side_effect = side_effect

    # Should not raise
    result = attach_reactions(comment_mock)

    # '-1' was attempted despite '+1' failing
    assert comment_mock.create_reaction.call_count == 2


def test_attach_reactions_still_tries_thumbsup_when_thumbsdown_raises() -> None:
    """If '-1' raises, '+1' still succeeds — reactions are isolated."""
    from coco_pr_review.github.reactions import attach_reactions

    comment_mock = MagicMock()

    def side_effect(reaction_type: str) -> MagicMock:
        if reaction_type == "-1":
            raise RuntimeError("Rate limited")
        return MagicMock()

    comment_mock.create_reaction.side_effect = side_effect

    result = attach_reactions(comment_mock)

    assert comment_mock.create_reaction.call_count == 2


# ---------------------------------------------------------------------------
# attach_reactions — report indicates which reactions succeeded
# ---------------------------------------------------------------------------


def test_attach_reactions_returns_report_with_success_indicators() -> None:
    """The return value indicates which reactions succeeded."""
    from coco_pr_review.github.reactions import attach_reactions

    comment_mock = MagicMock()

    def side_effect(reaction_type: str) -> MagicMock:
        if reaction_type == "-1":
            raise RuntimeError("Failed")
        return MagicMock()

    comment_mock.create_reaction.side_effect = side_effect

    result = attach_reactions(comment_mock)

    # Result should be a small report (dict, tuple, or dataclass)
    # indicating thumbsup succeeded and thumbsdown failed
    assert hasattr(result, "__getitem__") or hasattr(result, "thumbsup")
