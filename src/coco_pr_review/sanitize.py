"""Secret redaction for comment bodies before posting to GitHub.

LLMs reading code can hallucinate or quote real-looking secrets from `.env`
files, fixtures, or doc strings. This module scrubs anything matching a known
secret pattern from any string that's about to leave the orchestrator.
"""
from __future__ import annotations

import re

_REDACTED = "[REDACTED]"

# Built-in secret patterns. Conservative: each pattern targets a known
# fixed-shape token format, not "looks suspicious."
_BUILTIN_PATTERNS: tuple[str, ...] = (
    r"AKIA[0-9A-Z]{16}",                        # AWS access key ID
    r"ghp_[A-Za-z0-9]{36}",                     # GitHub classic PAT
    r"github_pat_[A-Za-z0-9_]{82,}",            # GitHub fine-grained PAT
    r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",  # JWT-shaped (header.payload.sig)
    # Env-var assignments where the KEY name signals secrecy. Conservative —
    # only matches keys containing KEY/TOKEN/SECRET/PASSWORD (or PASSWORD itself),
    # so PATH=, LANG=, etc. are not affected.
    r"\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*=\S+",
)


def redact(text: str, extra_patterns: list[str] | None = None) -> str:
    """Scrub known secret patterns from `text`. Each match becomes `[REDACTED]`.

    `extra_patterns` (regex strings) are applied additively after the built-ins.
    """
    patterns = list(_BUILTIN_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)
    out = text
    for pat in patterns:
        out = re.sub(pat, _REDACTED, out)
    return out
