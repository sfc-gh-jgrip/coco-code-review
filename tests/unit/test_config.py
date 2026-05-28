"""Tests for `coco_pr_review.config`.

Contract: the dataclass field names are pinned 1:1 to the YAML keys documented
in design doc §9. These tests guard the contract — if a field is renamed
defensively without the doc being updated, a test here will fail.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_load_config_with_no_path_returns_documented_defaults() -> None:
    """`load_config(None)` returns the design-doc §9 defaults verbatim."""
    from coco_pr_review.config import DEFAULT_CONFIG, load_config

    cfg = load_config(None)

    # Object equality with the module-level constant (sanity).
    assert cfg == DEFAULT_CONFIG

    # Spot-check every documented default value.
    assert cfg.orchestration.mode == "python-fanout"

    assert cfg.defaults.model == "claude-sonnet-4-6"
    assert cfg.defaults.effort == "medium"
    assert cfg.defaults.max_turns == 15

    assert cfg.limits.max_usd_per_pr == 2.00
    assert cfg.limits.job_timeout_sec == 600
    assert cfg.limits.max_findings_per_reviewer == 20

    assert cfg.verifier.enabled is True
    assert cfg.verifier.model == "claude-opus-4-6"
    assert cfg.verifier.effort == "high"
    assert cfg.verifier.confidence_threshold == 80

    # Default reviewer set: 4 entries with style-and-conventions @ replicas=2.
    by_name = {r.name: r for r in cfg.reviewers}
    assert set(by_name) == {
        "bugs-and-security",
        "tests-coverage",
        "style-and-conventions",
        "performance-and-cost",
    }
    assert by_name["style-and-conventions"].replicas == 2
    assert by_name["bugs-and-security"].replicas == 1
    assert by_name["performance-and-cost"].tool_tier == "read-sql"

    assert cfg.sanitize.enabled is True
    assert cfg.sanitize.extra_patterns == []

    assert cfg.telemetry.snowflake_table is None

    assert cfg.paths_ignore == []
    assert cfg.max_diff_lines == 2000
    assert cfg.review_bot_prs is False


# ---------------------------------------------------------------------------
# Field-name contract pins (catch defensive renames)
# ---------------------------------------------------------------------------


def test_dataclass_field_names_match_yaml_spec_exactly() -> None:
    """Pin the dataclass field names against the design-doc §9 YAML keys."""
    from coco_pr_review.config import (
        CocoPRReviewConfig,
        DefaultsConfig,
        LimitsConfig,
        OrchestrationConfig,
        ReviewerOverride,
        SanitizeConfig,
        TelemetryConfig,
        VerifierConfig,
    )

    assert set(OrchestrationConfig.__dataclass_fields__) == {"mode"}
    assert set(DefaultsConfig.__dataclass_fields__) == {"model", "effort", "max_turns"}
    assert set(LimitsConfig.__dataclass_fields__) == {
        "max_usd_per_pr",
        "job_timeout_sec",
        "max_findings_per_reviewer",
    }
    assert set(VerifierConfig.__dataclass_fields__) == {
        "enabled",
        "model",
        "effort",
        "confidence_threshold",
    }
    assert set(ReviewerOverride.__dataclass_fields__) == {
        "name",
        "tool_tier",
        "replicas",
        "enabled",
        "prompt_extra",
    }
    assert set(SanitizeConfig.__dataclass_fields__) == {"enabled", "extra_patterns"}
    assert set(TelemetryConfig.__dataclass_fields__) == {"snowflake_table"}

    # Top-level skip-filter keys are NOT nested — they live at the root.
    assert set(CocoPRReviewConfig.__dataclass_fields__) == {
        "orchestration",
        "defaults",
        "limits",
        "verifier",
        "reviewers",
        "sanitize",
        "telemetry",
        "paths_ignore",
        "max_diff_lines",
        "review_bot_prs",
    }


# ---------------------------------------------------------------------------
# Happy path — full YAML
# ---------------------------------------------------------------------------


_FULL_YAML = """\
orchestration:
  mode: python-fanout
defaults:
  model: claude-sonnet-4-6
  effort: medium
  max_turns: 12
limits:
  max_usd_per_pr: 1.50
  job_timeout_sec: 450
  max_findings_per_reviewer: 15
verifier:
  enabled: true
  model: claude-opus-4-6
  effort: high
  confidence_threshold: 85
reviewers:
  - name: bugs-and-security
    tool_tier: read-only
    replicas: 1
  - name: style-and-conventions
    tool_tier: read-only
    replicas: 3
    prompt_extra: "Pay extra attention to docstrings."
  - name: custom-reviewer
    tool_tier: read-sql-bash
    replicas: 1
    enabled: false
sanitize:
  enabled: true
  extra_patterns:
    - "INTERNAL-[A-Z0-9]{8}"
telemetry:
  snowflake_table: ANALYTICS.PUBLIC.CODE_REVIEW
paths_ignore:
  - "vendor/**"
  - "**/*.lock"
max_diff_lines: 1500
review_bot_prs: true
"""


def test_load_config_parses_full_yaml(tmp_path: Path) -> None:
    from coco_pr_review.config import load_config

    cfg_path = tmp_path / ".coco-pr-review.yml"
    cfg_path.write_text(_FULL_YAML)

    cfg = load_config(cfg_path)

    assert cfg.defaults.max_turns == 12
    assert cfg.limits.max_usd_per_pr == 1.50
    assert cfg.verifier.confidence_threshold == 85
    assert cfg.sanitize.extra_patterns == ["INTERNAL-[A-Z0-9]{8}"]
    assert cfg.telemetry.snowflake_table == "ANALYTICS.PUBLIC.CODE_REVIEW"
    assert cfg.paths_ignore == ["vendor/**", "**/*.lock"]
    assert cfg.max_diff_lines == 1500
    assert cfg.review_bot_prs is True

    by_name = {r.name: r for r in cfg.reviewers}
    # File overrode style-and-conventions.replicas from 2 → 3.
    assert by_name["style-and-conventions"].replicas == 3
    assert by_name["style-and-conventions"].prompt_extra == "Pay extra attention to docstrings."
    # Reviewers not mentioned in the file keep defaults.
    assert by_name["tests-coverage"].replicas == 1
    # New reviewer name introduced via the file is appended.
    assert by_name["custom-reviewer"].tool_tier == "read-sql-bash"
    assert by_name["custom-reviewer"].enabled is False


# ---------------------------------------------------------------------------
# Reviewer overrides (consumer wins)
# ---------------------------------------------------------------------------


def test_reviewer_override_per_name_consumer_values_win(tmp_path: Path) -> None:
    from coco_pr_review.config import load_config

    yaml_text = """\
reviewers:
  - name: bugs-and-security
    replicas: 4
    enabled: false
    prompt_extra: "Focus on auth."
"""
    cfg_path = tmp_path / ".coco-pr-review.yml"
    cfg_path.write_text(yaml_text)

    cfg = load_config(cfg_path)
    by_name = {r.name: r for r in cfg.reviewers}

    bugs = by_name["bugs-and-security"]
    assert bugs.replicas == 4
    assert bugs.enabled is False
    assert bugs.prompt_extra == "Focus on auth."
    # tool_tier was not overridden → falls back to default.
    assert bugs.tool_tier == "read-only"

    # Other defaults are untouched.
    assert by_name["style-and-conventions"].replicas == 2
    assert by_name["performance-and-cost"].tool_tier == "read-sql"


# ---------------------------------------------------------------------------
# CLI overrides
# ---------------------------------------------------------------------------


def test_cli_overrides_beat_file_for_scalars(tmp_path: Path) -> None:
    from coco_pr_review.config import load_config

    cfg_path = tmp_path / ".coco-pr-review.yml"
    cfg_path.write_text("max_diff_lines: 1500\n")

    cfg = load_config(cfg_path, cli_overrides={"max_diff_lines": 999})
    assert cfg.max_diff_lines == 999


def test_cli_overrides_beat_file_for_nested_keys(tmp_path: Path) -> None:
    from coco_pr_review.config import load_config

    cfg_path = tmp_path / ".coco-pr-review.yml"
    cfg_path.write_text(
        "verifier:\n"
        "  enabled: true\n"
        "  model: claude-opus-4-6\n"
        "  effort: high\n"
        "  confidence_threshold: 70\n"
    )
    cfg = load_config(
        cfg_path,
        cli_overrides={"verifier": {"confidence_threshold": 95}},
    )
    assert cfg.verifier.confidence_threshold == 95
    # Other verifier keys come from the file/defaults.
    assert cfg.verifier.model == "claude-opus-4-6"
    assert cfg.verifier.effort == "high"


def test_cli_overrides_alone_apply_on_top_of_defaults() -> None:
    from coco_pr_review.config import load_config

    cfg = load_config(None, cli_overrides={"max_diff_lines": 50})
    assert cfg.max_diff_lines == 50
    # Other defaults intact.
    assert cfg.verifier.confidence_threshold == 80


# ---------------------------------------------------------------------------
# Skip filters live at the top level (regression guard)
# ---------------------------------------------------------------------------


def test_skip_filter_keys_are_top_level(tmp_path: Path) -> None:
    """`paths_ignore`, `max_diff_lines`, `review_bot_prs` are top-level keys."""
    from coco_pr_review.config import load_config

    cfg_path = tmp_path / ".coco-pr-review.yml"
    cfg_path.write_text(
        "paths_ignore:\n  - \"generated/**\"\n"
        "max_diff_lines: 800\n"
        "review_bot_prs: true\n"
    )
    cfg = load_config(cfg_path)
    assert cfg.paths_ignore == ["generated/**"]
    assert cfg.max_diff_lines == 800
    assert cfg.review_bot_prs is True


def test_skip_filter_keys_under_skip_namespace_is_rejected(tmp_path: Path) -> None:
    """Nesting under a `skip:` key is a typo — must be rejected."""
    from coco_pr_review.config import ConfigError, load_config

    cfg_path = tmp_path / ".coco-pr-review.yml"
    cfg_path.write_text(
        "skip:\n  paths_ignore:\n    - \"vendor/**\"\n  max_diff_lines: 800\n"
    )
    with pytest.raises(ConfigError, match="unknown top-level key"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# Validation rejections
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / ".coco-pr-review.yml"
    p.write_text(body)
    return p


def test_unknown_top_level_key_typo_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(tmp_path, "path_ignore:\n  - \"vendor/**\"\n")
    with pytest.raises(ConfigError, match="path_ignore"):
        load_config(p)


def test_invalid_orchestration_mode_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(tmp_path, "orchestration:\n  mode: parallel\n")
    with pytest.raises(ConfigError, match="orchestration.mode"):
        load_config(p)


def test_confidence_threshold_out_of_range_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(
        tmp_path,
        "verifier:\n"
        "  enabled: true\n"
        "  model: claude-opus-4-6\n"
        "  effort: high\n"
        "  confidence_threshold: 150\n",
    )
    with pytest.raises(ConfigError, match="confidence_threshold"):
        load_config(p)


def test_confidence_threshold_negative_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(
        tmp_path,
        "verifier:\n"
        "  enabled: true\n"
        "  model: claude-opus-4-6\n"
        "  effort: high\n"
        "  confidence_threshold: -1\n",
    )
    with pytest.raises(ConfigError, match="confidence_threshold"):
        load_config(p)


def test_invalid_effort_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(
        tmp_path,
        "defaults:\n"
        "  model: claude-sonnet-4-6\n"
        "  effort: extreme\n"
        "  max_turns: 10\n",
    )
    with pytest.raises(ConfigError, match="effort"):
        load_config(p)


def test_invalid_tool_tier_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(
        tmp_path,
        "reviewers:\n"
        "  - name: bugs-and-security\n"
        "    tool_tier: full-access\n",
    )
    with pytest.raises(ConfigError, match="tool_tier"):
        load_config(p)


def test_reviewer_missing_name_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(tmp_path, "reviewers:\n  - replicas: 2\n")
    with pytest.raises(ConfigError, match="`name`"):
        load_config(p)


def test_non_bool_where_bool_expected_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(tmp_path, "review_bot_prs: yes_please\n")
    with pytest.raises(ConfigError, match="review_bot_prs"):
        load_config(p)


def test_int_where_bool_expected_rejected(tmp_path: Path) -> None:
    """1/0 must NOT silently pass as bool."""
    from coco_pr_review.config import ConfigError, load_config

    p = _write(tmp_path, "review_bot_prs: 1\n")
    with pytest.raises(ConfigError, match="review_bot_prs"):
        load_config(p)


def test_malformed_yaml_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(tmp_path, "verifier:\n  confidence_threshold: [unterminated\n")
    with pytest.raises(ConfigError, match="malformed YAML"):
        load_config(p)


def test_extra_patterns_rejects_invalid_regex(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(
        tmp_path,
        "sanitize:\n"
        "  enabled: true\n"
        "  extra_patterns:\n"
        "    - \"[unclosed\"\n",
    )
    with pytest.raises(ConfigError, match="not a valid regex"):
        load_config(p)


def test_unknown_section_subkey_rejected(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(
        tmp_path,
        "verifier:\n"
        "  enabled: true\n"
        "  model: claude-opus-4-6\n"
        "  effort: high\n"
        "  confidence_treshold: 80\n",  # typo
    )
    with pytest.raises(ConfigError, match="confidence_treshold"):
        load_config(p)


def test_error_message_includes_file_path(tmp_path: Path) -> None:
    from coco_pr_review.config import ConfigError, load_config

    p = _write(tmp_path, "max_diff_lines: not-an-int\n")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert str(p) in str(exc.value)


# ---------------------------------------------------------------------------
# find_config
# ---------------------------------------------------------------------------


def test_find_config_returns_path_when_present(tmp_path: Path) -> None:
    from coco_pr_review.config import find_config

    cfg_path = tmp_path / ".coco-pr-review.yml"
    cfg_path.write_text("max_diff_lines: 100\n")

    found = find_config(tmp_path)
    assert found == cfg_path


def test_find_config_returns_none_when_absent(tmp_path: Path) -> None:
    from coco_pr_review.config import find_config

    assert find_config(tmp_path) is None


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------


def test_public_api_reexports_from_package_root() -> None:
    """`from coco_pr_review import load_config, ...` works."""
    import coco_pr_review

    assert hasattr(coco_pr_review, "load_config")
    assert hasattr(coco_pr_review, "find_config")
    assert hasattr(coco_pr_review, "CocoPRReviewConfig")
    assert hasattr(coco_pr_review, "ConfigError")
