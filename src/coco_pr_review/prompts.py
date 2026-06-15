"""Prompt-shape utilities: untrusted-input wrapping + repo-conventions discovery.

The orchestrator wraps every diff, comment body, or other user-controlled text
in `<UNTRUSTED_USER_INPUT>` markers before passing them to a reviewer subagent.
The reviewer's system prompt explicitly forbids following instructions found
inside these markers — this is our prompt-injection defense.

Conventions discovery walks a fixed priority list to find a repo-specific
conventions document. The contents get appended to each reviewer's system
prompt so the reviewer learns the consumer's local rules.
"""
from __future__ import annotations

from pathlib import Path

# Open/close tags used by all reviewer subagents to delimit untrusted text.
_OPEN = "<UNTRUSTED_USER_INPUT>"
_CLOSE = "</UNTRUSTED_USER_INPUT>"

# First match wins. Order matches the spec: a coco-specific file beats the
# generic AGENTS/CLAUDE conventions a repo may already maintain for other tools.
_CONVENTIONS_PRIORITY: tuple[str, ...] = (
    ".coco-pr-review/conventions.md",
    "AGENTS.md",
    "CLAUDE.md",
)


def wrap_untrusted(content: str) -> str:
    """Wrap untrusted text in markers a reviewer prompt is taught to ignore-as-instructions."""
    return f"{_OPEN}\n{content}\n{_CLOSE}"


def build_reviewer_system_prompt(
    base_prompt: str,
    *,
    skill: str | None = None,
    prompt_extra: str | None = None,
) -> str:
    """Assemble a reviewer's full system prompt from its base + optional appendices.

    Appends, in order:
      1. A ``## Required skill`` block instructing the reviewer to load its one
         bundled Cortex skill via the `skill` tool and use it as the review
         checklist (maintainer-controlled, trusted).
      2. A ``## Additional instructions`` block carrying the config
         ``prompt_extra`` (maintainer-controlled, trusted).

    Both are optional; with neither set the base prompt is returned unchanged.
    """
    parts = [base_prompt]
    if skill:
        parts.append(
            "## Required skill\n"
            f"Before reviewing, invoke the `skill` tool to load the `{skill}` "
            "skill. Treat its guidance as the authoritative checklist for this "
            "review. Load it once at the start; do not re-load it per finding."
        )
    if prompt_extra:
        parts.append(f"## Additional instructions\n{prompt_extra}")
    return "\n\n".join(parts)


def discover_conventions(repo_root: Path) -> Path | None:
    """Find the highest-priority conventions file in `repo_root`, or None."""
    for relative in _CONVENTIONS_PRIORITY:
        candidate = repo_root / relative
        if candidate.is_file():
            return candidate
    return None
