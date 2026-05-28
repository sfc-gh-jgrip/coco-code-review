"""Loader and dataclasses for `.coco-pr-review.yml`.

The YAML schema is the single source of truth defined in the design doc §9. Field
names on the dataclasses below are pinned 1:1 to the YAML keys — do not rename
them defensively. Tests in `tests/unit/test_config.py` enforce the contract.

Loader contract:
  - `load_config(None)` returns `DEFAULT_CONFIG`.
  - `load_config(path)` reads + validates the file, layering on top of defaults.
  - `load_config(path, cli_overrides=...)` deep-merges CLI overrides last.
  - Unknown keys raise `ConfigError` (catches typos like `path_ignore`).

This module is pure: no orchestrator wiring lives here. A follow-up milestone
will plumb `CocoPRReviewConfig` into `python_fanout.py` and the publisher.
"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

# ---------------------------------------------------------------------------
# Allowed-value sets (exact, no fuzzy matching)
# ---------------------------------------------------------------------------

_ORCHESTRATION_MODES = ("python-fanout", "swarm")
_EFFORT_LEVELS = ("low", "medium", "high")
_TOOL_TIERS = ("read-only", "read-sql", "read-sql-bash")

# Top-level YAML keys this loader recognises. Anything else raises ConfigError.
_ALLOWED_TOP_LEVEL = frozenset(
    {
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
)

_ALLOWED_ORCHESTRATION = frozenset({"mode"})
_ALLOWED_DEFAULTS = frozenset({"model", "effort", "max_turns"})
_ALLOWED_LIMITS = frozenset(
    {"max_usd_per_pr", "job_timeout_sec", "max_findings_per_reviewer"}
)
_ALLOWED_VERIFIER = frozenset({"enabled", "model", "effort", "confidence_threshold"})
_ALLOWED_REVIEWER = frozenset(
    {"name", "tool_tier", "replicas", "enabled", "prompt_extra"}
)
_ALLOWED_SANITIZE = frozenset({"enabled", "extra_patterns"})
_ALLOWED_TELEMETRY = frozenset({"snowflake_table"})


class ConfigError(ValueError):
    """Raised when `.coco-pr-review.yml` is malformed or contains invalid values."""


# ---------------------------------------------------------------------------
# Dataclasses — field names pinned to the YAML schema (design doc §9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrchestrationConfig:
    mode: str = "python-fanout"


@dataclass(frozen=True)
class DefaultsConfig:
    model: str = "claude-sonnet-4-6"
    effort: str = "medium"
    max_turns: int = 15


@dataclass(frozen=True)
class LimitsConfig:
    max_usd_per_pr: float = 2.00
    job_timeout_sec: int = 600
    max_findings_per_reviewer: int = 20


@dataclass(frozen=True)
class VerifierConfig:
    enabled: bool = True
    model: str = "claude-opus-4-6"
    effort: str = "high"
    confidence_threshold: int = 80


@dataclass(frozen=True)
class ReviewerOverride:
    name: str
    tool_tier: str = "read-only"
    replicas: int = 1
    enabled: bool = True
    prompt_extra: str | None = None


@dataclass(frozen=True)
class SanitizeConfig:
    enabled: bool = True
    extra_patterns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TelemetryConfig:
    snowflake_table: str | None = None


@dataclass(frozen=True)
class CocoPRReviewConfig:
    orchestration: OrchestrationConfig
    defaults: DefaultsConfig
    limits: LimitsConfig
    verifier: VerifierConfig
    reviewers: list[ReviewerOverride]
    sanitize: SanitizeConfig
    telemetry: TelemetryConfig
    paths_ignore: list[str]
    max_diff_lines: int
    review_bot_prs: bool


# ---------------------------------------------------------------------------
# Defaults (design doc §9). Constructed via a function so the constant is
# immutable from the caller's perspective but each call yields fresh lists.
# ---------------------------------------------------------------------------


def _default_reviewers() -> list[ReviewerOverride]:
    return [
        ReviewerOverride(name="bugs-and-security", tool_tier="read-only", replicas=1),
        ReviewerOverride(name="tests-coverage", tool_tier="read-only", replicas=1),
        ReviewerOverride(name="style-and-conventions", tool_tier="read-only", replicas=2),
        ReviewerOverride(name="performance-and-cost", tool_tier="read-sql", replicas=1),
    ]


def _build_default_config() -> CocoPRReviewConfig:
    return CocoPRReviewConfig(
        orchestration=OrchestrationConfig(),
        defaults=DefaultsConfig(),
        limits=LimitsConfig(),
        verifier=VerifierConfig(),
        reviewers=_default_reviewers(),
        sanitize=SanitizeConfig(),
        telemetry=TelemetryConfig(),
        paths_ignore=[],
        max_diff_lines=2000,
        review_bot_prs=False,
    )


DEFAULT_CONFIG: CocoPRReviewConfig = _build_default_config()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def find_config(repo_root: Path | str) -> Path | None:
    """Return the path to `.coco-pr-review.yml` under `repo_root`, or None."""
    candidate = Path(repo_root) / ".coco-pr-review.yml"
    return candidate if candidate.is_file() else None


def load_config(
    path: Path | str | None = None,
    *,
    cli_overrides: Mapping[str, Any] | None = None,
) -> CocoPRReviewConfig:
    """Load a config file and layer on optional CLI overrides.

    Precedence (lowest → highest): defaults → file → CLI overrides.
    """
    if path is None and not cli_overrides:
        return _build_default_config()

    file_data: dict[str, Any] = {}
    file_label: str = "<cli-overrides>"
    if path is not None:
        path_obj = Path(path)
        file_label = str(path_obj)
        try:
            text = path_obj.read_text()
        except FileNotFoundError as exc:
            raise ConfigError(f"{path_obj}: config file not found") from exc
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path_obj}: malformed YAML: {exc}") from exc
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ConfigError(
                f"{path_obj}: top-level YAML must be a mapping, got {type(parsed).__name__}"
            )
        file_data = parsed

    merged = _deep_merge(_default_dict(), file_data, source=file_label)

    if cli_overrides:
        merged = _deep_merge(merged, dict(cli_overrides), source="<cli-overrides>")

    return _build_from_dict(merged, source=file_label)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _default_dict() -> dict[str, Any]:
    """Return the default config as a plain dict for merging."""
    return {
        "orchestration": {"mode": "python-fanout"},
        "defaults": {
            "model": "claude-sonnet-4-6",
            "effort": "medium",
            "max_turns": 15,
        },
        "limits": {
            "max_usd_per_pr": 2.00,
            "job_timeout_sec": 600,
            "max_findings_per_reviewer": 20,
        },
        "verifier": {
            "enabled": True,
            "model": "claude-opus-4-6",
            "effort": "high",
            "confidence_threshold": 80,
        },
        "reviewers": [
            {"name": "bugs-and-security", "tool_tier": "read-only", "replicas": 1},
            {"name": "tests-coverage", "tool_tier": "read-only", "replicas": 1},
            {"name": "style-and-conventions", "tool_tier": "read-only", "replicas": 2},
            {"name": "performance-and-cost", "tool_tier": "read-sql", "replicas": 1},
        ],
        "sanitize": {"enabled": True, "extra_patterns": []},
        "telemetry": {"snowflake_table": None},
        "paths_ignore": [],
        "max_diff_lines": 2000,
        "review_bot_prs": False,
    }


def _deep_merge(
    base: dict[str, Any], overlay: Mapping[str, Any], *, source: str
) -> dict[str, Any]:
    """Deep-merge `overlay` into a copy of `base`.

    Rules:
      - Nested dicts are merged recursively.
      - Scalars (incl. None) are replaced wholesale.
      - The `reviewers` list is merged by `name`: an overlay entry whose `name`
        matches a base entry merges into that entry; new names are appended.
      - All other lists (e.g., `paths_ignore`, `extra_patterns`) are replaced
        wholesale.
    Unknown top-level keys in `overlay` raise `ConfigError`.
    """
    result = deepcopy(base)
    if not isinstance(overlay, Mapping):
        raise ConfigError(f"{source}: expected a mapping at top level")

    unknown = set(overlay.keys()) - _ALLOWED_TOP_LEVEL
    if unknown:
        raise ConfigError(
            f"{source}: unknown top-level key(s): {sorted(unknown)}. "
            f"Allowed: {sorted(_ALLOWED_TOP_LEVEL)}"
        )

    for key, overlay_val in overlay.items():
        if key == "reviewers":
            result[key] = _merge_reviewers(result.get(key, []), overlay_val, source=source)
            continue
        if isinstance(overlay_val, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge_dict_section(
                result[key], overlay_val, section=key, source=source
            )
        else:
            result[key] = deepcopy(overlay_val)
    return result


def _merge_dict_section(
    base: dict[str, Any], overlay: Mapping[str, Any], *, section: str, source: str
) -> dict[str, Any]:
    """Merge a known second-level section, rejecting unknown keys."""
    allowed = _section_allowed_keys(section)
    if allowed is not None:
        unknown = set(overlay.keys()) - allowed
        if unknown:
            raise ConfigError(
                f"{source}: unknown key(s) in `{section}`: {sorted(unknown)}. "
                f"Allowed: {sorted(allowed)}"
            )
    out = deepcopy(base)
    for k, v in overlay.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = deepcopy(v)
    return out


def _section_allowed_keys(section: str) -> frozenset[str] | None:
    return {
        "orchestration": _ALLOWED_ORCHESTRATION,
        "defaults": _ALLOWED_DEFAULTS,
        "limits": _ALLOWED_LIMITS,
        "verifier": _ALLOWED_VERIFIER,
        "sanitize": _ALLOWED_SANITIZE,
        "telemetry": _ALLOWED_TELEMETRY,
    }.get(section)


def _merge_reviewers(
    base: list[Any], overlay: Any, *, source: str
) -> list[dict[str, Any]]:
    if not isinstance(overlay, list):
        raise ConfigError(
            f"{source}: `reviewers` must be a list, got {type(overlay).__name__}"
        )
    base_by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in base:
        entry_dict = dict(entry)
        name = entry_dict["name"]
        base_by_name[name] = entry_dict
        order.append(name)

    for raw in overlay:
        if not isinstance(raw, Mapping):
            raise ConfigError(
                f"{source}: each `reviewers` entry must be a mapping, got "
                f"{type(raw).__name__}"
            )
        if "name" not in raw:
            raise ConfigError(f"{source}: `reviewers` entry missing `name`")
        unknown = set(raw.keys()) - _ALLOWED_REVIEWER
        if unknown:
            raise ConfigError(
                f"{source}: unknown key(s) in reviewer `{raw['name']}`: {sorted(unknown)}. "
                f"Allowed: {sorted(_ALLOWED_REVIEWER)}"
            )
        name = raw["name"]
        if name in base_by_name:
            base_by_name[name].update(raw)
        else:
            base_by_name[name] = dict(raw)
            order.append(name)
    return [base_by_name[n] for n in order]


# ---------------------------------------------------------------------------
# Validation + dataclass construction
# ---------------------------------------------------------------------------


def _build_from_dict(data: dict[str, Any], *, source: str) -> CocoPRReviewConfig:
    orch = _build_orchestration(data["orchestration"], source=source)
    defaults = _build_defaults(data["defaults"], source=source)
    limits = _build_limits(data["limits"], source=source)
    verifier = _build_verifier(data["verifier"], source=source)
    reviewers = [_build_reviewer(r, source=source) for r in data["reviewers"]]
    sanitize = _build_sanitize(data["sanitize"], source=source)
    telemetry = _build_telemetry(data["telemetry"], source=source)

    paths_ignore = _require_list_of_str(
        data["paths_ignore"], key="paths_ignore", source=source
    )
    max_diff_lines = _require_positive_int(
        data["max_diff_lines"], key="max_diff_lines", source=source
    )
    review_bot_prs = _require_bool(
        data["review_bot_prs"], key="review_bot_prs", source=source
    )

    return CocoPRReviewConfig(
        orchestration=orch,
        defaults=defaults,
        limits=limits,
        verifier=verifier,
        reviewers=reviewers,
        sanitize=sanitize,
        telemetry=telemetry,
        paths_ignore=paths_ignore,
        max_diff_lines=max_diff_lines,
        review_bot_prs=review_bot_prs,
    )


def _build_orchestration(data: Mapping[str, Any], *, source: str) -> OrchestrationConfig:
    mode = data.get("mode", "python-fanout")
    if mode not in _ORCHESTRATION_MODES:
        raise ConfigError(
            f"{source}: orchestration.mode must be one of {list(_ORCHESTRATION_MODES)}, "
            f"got {mode!r}"
        )
    return OrchestrationConfig(mode=mode)


def _build_defaults(data: Mapping[str, Any], *, source: str) -> DefaultsConfig:
    model = _require_str(data["model"], key="defaults.model", source=source)
    effort = data["effort"]
    if effort not in _EFFORT_LEVELS:
        raise ConfigError(
            f"{source}: defaults.effort must be one of {list(_EFFORT_LEVELS)}, "
            f"got {effort!r}"
        )
    max_turns = _require_positive_int(
        data["max_turns"], key="defaults.max_turns", source=source
    )
    return DefaultsConfig(model=model, effort=effort, max_turns=max_turns)


def _build_limits(data: Mapping[str, Any], *, source: str) -> LimitsConfig:
    max_usd = data["max_usd_per_pr"]
    if not isinstance(max_usd, (int, float)) or isinstance(max_usd, bool) or max_usd < 0:
        raise ConfigError(
            f"{source}: limits.max_usd_per_pr must be a non-negative number, got {max_usd!r}"
        )
    job_timeout_sec = _require_positive_int(
        data["job_timeout_sec"], key="limits.job_timeout_sec", source=source
    )
    max_findings = _require_positive_int(
        data["max_findings_per_reviewer"],
        key="limits.max_findings_per_reviewer",
        source=source,
    )
    return LimitsConfig(
        max_usd_per_pr=float(max_usd),
        job_timeout_sec=job_timeout_sec,
        max_findings_per_reviewer=max_findings,
    )


def _build_verifier(data: Mapping[str, Any], *, source: str) -> VerifierConfig:
    enabled = _require_bool(data["enabled"], key="verifier.enabled", source=source)
    model = _require_str(data["model"], key="verifier.model", source=source)
    effort = data["effort"]
    if effort not in _EFFORT_LEVELS:
        raise ConfigError(
            f"{source}: verifier.effort must be one of {list(_EFFORT_LEVELS)}, "
            f"got {effort!r}"
        )
    threshold = data["confidence_threshold"]
    if (
        not isinstance(threshold, int)
        or isinstance(threshold, bool)
        or threshold < 0
        or threshold > 100
    ):
        raise ConfigError(
            f"{source}: verifier.confidence_threshold must be an integer in [0, 100], "
            f"got {threshold!r}"
        )
    return VerifierConfig(
        enabled=enabled, model=model, effort=effort, confidence_threshold=threshold
    )


def _build_reviewer(data: Mapping[str, Any], *, source: str) -> ReviewerOverride:
    if "name" not in data:
        raise ConfigError(f"{source}: reviewer entry missing `name`")
    name = _require_str(data["name"], key="reviewers[].name", source=source)
    tool_tier = data.get("tool_tier", "read-only")
    if tool_tier not in _TOOL_TIERS:
        raise ConfigError(
            f"{source}: reviewer `{name}` tool_tier must be one of {list(_TOOL_TIERS)}, "
            f"got {tool_tier!r}"
        )
    replicas = _require_positive_int(
        data.get("replicas", 1), key=f"reviewers[{name}].replicas", source=source
    )
    enabled = _require_bool(
        data.get("enabled", True), key=f"reviewers[{name}].enabled", source=source
    )
    prompt_extra = data.get("prompt_extra")
    if prompt_extra is not None and not isinstance(prompt_extra, str):
        raise ConfigError(
            f"{source}: reviewer `{name}` prompt_extra must be a string or null, "
            f"got {type(prompt_extra).__name__}"
        )
    return ReviewerOverride(
        name=name,
        tool_tier=tool_tier,
        replicas=replicas,
        enabled=enabled,
        prompt_extra=prompt_extra,
    )


def _build_sanitize(data: Mapping[str, Any], *, source: str) -> SanitizeConfig:
    enabled = _require_bool(data["enabled"], key="sanitize.enabled", source=source)
    raw_patterns = data.get("extra_patterns", [])
    if not isinstance(raw_patterns, list):
        raise ConfigError(
            f"{source}: sanitize.extra_patterns must be a list, "
            f"got {type(raw_patterns).__name__}"
        )
    patterns: list[str] = []
    for pat in raw_patterns:
        if not isinstance(pat, str):
            raise ConfigError(
                f"{source}: sanitize.extra_patterns entries must be strings, "
                f"got {type(pat).__name__}"
            )
        try:
            re.compile(pat)
        except re.error as exc:
            raise ConfigError(
                f"{source}: sanitize.extra_patterns entry {pat!r} is not a valid regex: {exc}"
            ) from exc
        patterns.append(pat)
    return SanitizeConfig(enabled=enabled, extra_patterns=patterns)


def _build_telemetry(data: Mapping[str, Any], *, source: str) -> TelemetryConfig:
    table = data.get("snowflake_table")
    if table is not None and not isinstance(table, str):
        raise ConfigError(
            f"{source}: telemetry.snowflake_table must be a string or null, "
            f"got {type(table).__name__}"
        )
    return TelemetryConfig(snowflake_table=table)


# ---------------------------------------------------------------------------
# Scalar coercion helpers — strict; never silently coerce.
# ---------------------------------------------------------------------------


def _require_str(value: Any, *, key: str, source: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(
            f"{source}: `{key}` must be a string, got {type(value).__name__}"
        )
    return value


def _require_bool(value: Any, *, key: str, source: str) -> bool:
    # `bool` is a subclass of `int`; accept only literal True/False to avoid
    # silently treating 1/0 as bool.
    if not isinstance(value, bool):
        raise ConfigError(
            f"{source}: `{key}` must be a boolean, got {type(value).__name__}"
        )
    return value


def _require_positive_int(value: Any, *, key: str, source: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(
            f"{source}: `{key}` must be a positive integer, got {value!r}"
        )
    return value


def _require_list_of_str(value: Any, *, key: str, source: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(
            f"{source}: `{key}` must be a list, got {type(value).__name__}"
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ConfigError(
                f"{source}: `{key}` entries must be strings, got {type(item).__name__}"
            )
        out.append(item)
    return out


__all__ = [
    "CocoPRReviewConfig",
    "ConfigError",
    "DEFAULT_CONFIG",
    "DefaultsConfig",
    "LimitsConfig",
    "OrchestrationConfig",
    "ReviewerOverride",
    "SanitizeConfig",
    "TelemetryConfig",
    "VerifierConfig",
    "find_config",
    "load_config",
]
