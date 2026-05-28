"""Parse `.cortex/agents/<name>.md` files into `ReviewerSpec` dataclasses.

The agent markdown file format is:

    ---
    name: <subagent_type>
    description: <short description>
    model: <claude-sonnet-4-6 | claude-opus-4-6 | ...>
    tools:
      - Read
      - Glob
      ...
    ---

    <markdown body — the system prompt>

Valid files are also valid Cortex Code subagent definitions; consumers can drop
the same files into `.cortex/agents/` for interactive use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ReviewerSpec:
    """Parsed reviewer or verifier agent definition."""

    name: str
    description: str
    model: str
    tools: list[str]
    system_prompt: str


_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_REQUIRED_FIELDS = ("name", "description", "model", "tools")


def parse_agent_md(path: str | Path) -> ReviewerSpec:
    """Parse an agent markdown file and return a `ReviewerSpec`."""
    text = Path(path).read_text()
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise ValueError(f"{path}: missing YAML frontmatter (file must start with `---`)")

    frontmatter_text, body = match.group(1), match.group(2)
    frontmatter = yaml.safe_load(frontmatter_text) or {}

    missing = [f for f in _REQUIRED_FIELDS if f not in frontmatter]
    if missing:
        raise ValueError(
            f"{path}: missing required frontmatter field(s): {', '.join(missing)}"
        )

    return ReviewerSpec(
        name=frontmatter["name"],
        description=frontmatter["description"],
        model=frontmatter["model"],
        tools=list(frontmatter["tools"]),
        system_prompt=body.strip(),
    )
