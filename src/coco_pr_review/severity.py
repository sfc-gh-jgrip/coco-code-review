"""Canonical severity vocabulary, ordering, and display for review findings.

Single source of truth shared by the JSON schema enum (`schema.py`), the
orchestrator, and every GitHub presentation surface — inline comments, the
sticky summary, and the Checks API. Anything that needs to know which
severities exist, how to order them, or which emoji to show must import from
here rather than redeclaring its own map.
"""
from __future__ import annotations

# Ordered most- to least-severe. The tuple index doubles as the canonical
# sort rank, so there is exactly one place that defines severity ordering.
SEVERITIES: tuple[str, ...] = ("blocker", "warning", "nit")

SEVERITY_EMOJI: dict[str, str] = {
    "blocker": "🔴",
    "warning": "🟡",
    "nit": "⚪",
}

# Display emoji for a severity outside the known set (defensive; the schema
# constrains real findings to SEVERITIES).
UNKNOWN_SEVERITY_EMOJI = "⚪"


def emoji_for(severity: str) -> str:
    """Display emoji for a severity, falling back for unknown values."""
    return SEVERITY_EMOJI.get(severity, UNKNOWN_SEVERITY_EMOJI)


def severity_rank(severity: str) -> int:
    """Sort rank for a severity: lower is more severe; unknown values sort last."""
    try:
        return SEVERITIES.index(severity)
    except ValueError:
        return len(SEVERITIES)
