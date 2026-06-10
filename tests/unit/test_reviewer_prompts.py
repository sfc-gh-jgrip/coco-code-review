"""Tests for the four reviewer prompt .md files under `src/coco_pr_review/agents/`.

Each reviewer prompt is parsed via `coco_pr_review.reviewer_spec.parse_agent_md`
and validated for structural correctness: frontmatter fields, required sections,
defensive instructions, attribution footer, and UNTRUSTED_USER_INPUT reference.

These tests MUST fail with FileNotFoundError or similar until the .md files are authored.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# All reviewer names with a bundled prompt .md — kebab-case, matching filenames.
REVIEWER_NAMES = [
    "bugs-and-security",
    "tests-coverage",
    "style-and-conventions",
    "performance-and-cost",
    "snowflake-governance-security",
    "sql-correctness",
    "dbt-transformation",
]

# Root of the agents directory (relative to this test file).
_AGENTS_DIR = Path(__file__).resolve().parents[2] / "src" / "coco_pr_review" / "agents"


@pytest.fixture(params=REVIEWER_NAMES)
def reviewer_name(request: pytest.FixtureRequest) -> str:
    """Parametrize over all four reviewer names."""
    return request.param


@pytest.fixture
def reviewer_path(reviewer_name: str) -> Path:
    """Absolute path to the reviewer .md file."""
    return _AGENTS_DIR / f"{reviewer_name}.md"


def test_reviewer_file_exists(reviewer_path: Path) -> None:
    """The reviewer .md file must exist on disk."""
    assert reviewer_path.exists(), f"Missing reviewer prompt: {reviewer_path}"


def test_reviewer_parses_cleanly(reviewer_path: Path) -> None:
    """parse_agent_md succeeds without raising."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    parse_agent_md(reviewer_path)


def test_reviewer_name_matches_filename(reviewer_path: Path, reviewer_name: str) -> None:
    """Frontmatter `name` field matches the file stem (kebab-case convention)."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    spec = parse_agent_md(reviewer_path)
    assert spec.name == reviewer_name


def test_reviewer_model_is_sonnet(reviewer_path: Path) -> None:
    """All four reviewers use claude-sonnet-4-6 (per plan table)."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    spec = parse_agent_md(reviewer_path)
    assert spec.model == "claude-sonnet-4-6"


def test_reviewer_tools_are_read_glob_grep(reviewer_path: Path) -> None:
    """All four reviewers have the same read-only tool surface."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    spec = parse_agent_md(reviewer_path)
    assert spec.tools == ["Read", "Glob", "Grep"]


def test_reviewer_has_high_signal_section(reviewer_path: Path) -> None:
    """System prompt contains a HIGH-SIGNAL section with ACCEPT and REJECT criteria."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    spec = parse_agent_md(reviewer_path)
    assert "HIGH-SIGNAL" in spec.system_prompt
    # Must have both polarity anchors
    assert "ACCEPT" in spec.system_prompt
    assert "REJECT" in spec.system_prompt


def test_reviewer_has_defensive_instructions(reviewer_path: Path) -> None:
    """System prompt contains prompt-injection hardening section."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    spec = parse_agent_md(reviewer_path)
    prompt_lower = spec.system_prompt.lower()
    assert "defensive" in prompt_lower or "prompt-injection" in prompt_lower


def test_reviewer_references_untrusted_user_input_marker(reviewer_path: Path) -> None:
    """System prompt references the <UNTRUSTED_USER_INPUT> delimiter."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    spec = parse_agent_md(reviewer_path)
    assert "<UNTRUSTED_USER_INPUT>" in spec.system_prompt


def test_reviewer_excludes_pre_existing_issues(reviewer_path: Path) -> None:
    """System prompt explicitly states pre-existing issues are out of scope."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    spec = parse_agent_md(reviewer_path)
    prompt_lower = spec.system_prompt.lower()
    assert "pre-existing" in prompt_lower


def test_reviewer_has_attribution_footer(reviewer_path: Path) -> None:
    """System prompt credits Anthropic's code-review work (matching verifier.md)."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    spec = parse_agent_md(reviewer_path)
    assert "Anthropic" in spec.system_prompt


def test_reviewer_has_output_schema_section(reviewer_path: Path) -> None:
    """System prompt references FINDING_SCHEMA or output_format for structured output."""
    from coco_pr_review.reviewer_spec import parse_agent_md

    spec = parse_agent_md(reviewer_path)
    prompt_lower = spec.system_prompt.lower()
    assert "output" in prompt_lower and "schema" in prompt_lower
