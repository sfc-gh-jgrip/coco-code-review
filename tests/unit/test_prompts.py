"""Tests for `coco_pr_review.prompts` — prompt-injection markers + conventions discovery."""
from __future__ import annotations

from pathlib import Path


def test_wrap_untrusted_wraps_content_in_markers() -> None:
    """Tracer bullet: content gets wrapped in clear UNTRUSTED_USER_INPUT markers."""
    from coco_pr_review.prompts import wrap_untrusted

    content = "Here is some user-supplied text."
    out = wrap_untrusted(content)

    assert "<UNTRUSTED_USER_INPUT>" in out
    assert "</UNTRUSTED_USER_INPUT>" in out
    assert content in out
    # Markers wrap the content (open before close)
    assert out.index("<UNTRUSTED_USER_INPUT>") < out.index(content) < out.index("</UNTRUSTED_USER_INPUT>")


def test_discover_conventions_returns_highest_priority_file_when_present(tmp_path: Path) -> None:
    """`.coco-pr-review/conventions.md` outranks AGENTS.md and CLAUDE.md."""
    from coco_pr_review.prompts import discover_conventions

    (tmp_path / ".coco-pr-review").mkdir()
    primary = tmp_path / ".coco-pr-review" / "conventions.md"
    primary.write_text("# Coco-specific conventions")
    (tmp_path / "AGENTS.md").write_text("# Generic AGENTS conventions")
    (tmp_path / "CLAUDE.md").write_text("# Generic CLAUDE conventions")

    found = discover_conventions(tmp_path)

    assert found == primary


def test_discover_conventions_falls_through_to_AGENTS_md(tmp_path: Path) -> None:
    """When the primary file is absent, AGENTS.md is the next choice."""
    from coco_pr_review.prompts import discover_conventions

    (tmp_path / "AGENTS.md").write_text("# AGENTS")
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE")  # lower priority

    found = discover_conventions(tmp_path)

    assert found == tmp_path / "AGENTS.md"


def test_discover_conventions_last_resort_is_CLAUDE_md(tmp_path: Path) -> None:
    """When primary and AGENTS.md are absent, CLAUDE.md is the last resort."""
    from coco_pr_review.prompts import discover_conventions

    (tmp_path / "CLAUDE.md").write_text("# CLAUDE")

    found = discover_conventions(tmp_path)

    assert found == tmp_path / "CLAUDE.md"


def test_discover_conventions_returns_none_when_nothing_present(tmp_path: Path) -> None:
    """An unconventional repo has no conventions file — return None."""
    from coco_pr_review.prompts import discover_conventions

    found = discover_conventions(tmp_path)

    assert found is None


def test_build_reviewer_system_prompt_no_appendices_returns_base() -> None:
    from coco_pr_review.prompts import build_reviewer_system_prompt

    assert build_reviewer_system_prompt("BASE") == "BASE"


def test_build_reviewer_system_prompt_threads_skill() -> None:
    from coco_pr_review.prompts import build_reviewer_system_prompt

    out = build_reviewer_system_prompt("BASE", skill="sql-author")
    assert "BASE" in out
    assert "## Required skill" in out
    assert "`skill` tool" in out
    assert "`sql-author`" in out


def test_build_reviewer_system_prompt_threads_skill_and_prompt_extra_in_order() -> None:
    from coco_pr_review.prompts import build_reviewer_system_prompt

    out = build_reviewer_system_prompt(
        "BASE", skill="data-governance", prompt_extra="Focus on grants."
    )
    assert out.index("BASE") < out.index("## Required skill") < out.index("## Additional instructions")
    assert "Focus on grants." in out


def test_build_reviewer_system_prompt_prompt_extra_only() -> None:
    from coco_pr_review.prompts import build_reviewer_system_prompt

    out = build_reviewer_system_prompt("BASE", prompt_extra="X")
    assert "## Required skill" not in out
    assert "## Additional instructions\nX" in out
