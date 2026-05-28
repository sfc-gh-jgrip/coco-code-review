"""Reaction attachment with per-reaction isolation.

Attaches thumbsup and thumbsdown reactions to a review comment.
Each reaction call is wrapped independently — one failure must not
affect the other.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReactionReport:
    """Result of attempting to attach reactions to a comment."""

    thumbsup: bool
    thumbsdown: bool

    def __getitem__(self, key: str) -> bool:
        if key == "thumbsup":
            return self.thumbsup
        if key == "thumbsdown":
            return self.thumbsdown
        raise KeyError(key)


def attach_reactions(comment: Any) -> ReactionReport:
    """Attach 👍 and 👎 reactions to a comment.

    Each reaction is attempted independently — failure of one does not
    prevent the other from being tried.  Never raises; returns a report
    indicating which reactions succeeded.
    """
    thumbsup_ok = False
    thumbsdown_ok = False

    try:
        comment.create_reaction("+1")
        thumbsup_ok = True
    except Exception:
        pass

    try:
        comment.create_reaction("-1")
        thumbsdown_ok = True
    except Exception:
        pass

    return ReactionReport(thumbsup=thumbsup_ok, thumbsdown=thumbsdown_ok)
