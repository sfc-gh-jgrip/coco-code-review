"""Tests for `coco_pr_review.reviewer_spec`.

A `ReviewerSpec` is a parsed `.cortex/agents/<name>.md` file with YAML frontmatter
declaring (at minimum) `name`, `description`, `model`, and `tools`, followed by a
markdown body that becomes the system prompt.
"""
from pathlib import Path

import pytest


VALID_AGENT_MD = """\
---
name: bugs-and-security
description: Reviews PRs for bugs, logic flaws, and security vulnerabilities.
model: claude-sonnet-4-6
tools:
  - Read
  - Glob
  - Grep
---

You are a code review subagent specialized in finding bugs and security issues.

## CRITICAL: HIGH-SIGNAL ONLY

Flag only definite defects.
"""


def test_parse_agent_md_returns_all_fields_from_valid_file(tmp_path: Path) -> None:
    """Tracer bullet: a complete valid agent markdown file parses into a fully-populated ReviewerSpec."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    agent_file = tmp_path / "bugs-and-security.md"
    agent_file.write_text(VALID_AGENT_MD)

    spec = parse_agent_md(agent_file)

    assert spec.name == "bugs-and-security"
    assert spec.description == "Reviews PRs for bugs, logic flaws, and security vulnerabilities."
    assert spec.model == "claude-sonnet-4-6"
    assert spec.tools == ["Read", "Glob", "Grep"]
    assert spec.system_prompt.startswith("You are a code review subagent")
    assert "HIGH-SIGNAL ONLY" in spec.system_prompt
    # body is stripped (no leading/trailing whitespace from the parser)
    assert spec.system_prompt == spec.system_prompt.strip()


def test_parse_agent_md_rejects_file_without_frontmatter(tmp_path: Path) -> None:
    """A file with no YAML frontmatter is unambiguously malformed."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    agent_file = tmp_path / "no-frontmatter.md"
    agent_file.write_text("# Just a markdown heading\n\nNo frontmatter at all.\n")

    with pytest.raises(ValueError, match="frontmatter"):
        parse_agent_md(agent_file)


def test_parse_agent_md_rejects_missing_required_field(tmp_path: Path) -> None:
    """Missing a required field (e.g., `name`) fails with a message that names the field and the file."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    # frontmatter has description/model/tools but no `name`
    incomplete = (
        "---\n"
        "description: missing the name field\n"
        "model: claude-sonnet-4-6\n"
        "tools:\n  - Read\n"
        "---\n\nbody.\n"
    )
    agent_file = tmp_path / "incomplete.md"
    agent_file.write_text(incomplete)

    with pytest.raises(ValueError, match="name"):
        parse_agent_md(agent_file)


def test_parse_agent_md_handles_real_verifier_definition() -> None:
    """The verifier.md we ship with the package must parse cleanly.

    Belt-and-suspenders: this guards us from publishing a bundled agent file
    we accidentally broke.
    """
    from coco_pr_review.reviewer_spec import parse_agent_md

    package_root = Path(__file__).resolve().parents[2] / "src" / "coco_pr_review"
    verifier_path = package_root / "agents" / "verifier.md"

    spec = parse_agent_md(verifier_path)

    assert spec.name == "verifier"
    assert spec.model == "claude-opus-4-6"
    assert spec.tools == ["Read", "Glob", "Grep"]
    assert "HIGH-SIGNAL" in spec.system_prompt
    assert "confidence" in spec.system_prompt.lower()
