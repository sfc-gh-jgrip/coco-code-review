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

# Effort profiles — named bundles of config overrides selected per-repo (config)
# or per-PR (`@coco-review cheap|high`). A profile is layered between the raw
# base defaults and the repo's `.coco-pr-review.yml`, so a repo can still tune
# any individual knob a profile sets. See `_profile_overlays`.
PROFILE_NAMES = ("snowflake", "high", "cheap")
DEFAULT_PROFILE = "snowflake"
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

_ALLOWED_ORCHESTRATION = frozenset({"mode", "profile"})
_ALLOWED_DEFAULTS = frozenset({"model", "effort", "max_turns"})
_ALLOWED_LIMITS = frozenset(
    {"max_usd_per_pr", "job_timeout_sec", "max_findings_per_reviewer"}
)
_ALLOWED_VERIFIER = frozenset({"enabled", "model", "effort", "confidence_threshold"})
_ALLOWED_REVIEWER = frozenset(
    {"name", "tool_tier", "replicas", "enabled", "prompt_extra", "activate_when", "skill"}
)
_ALLOWED_ACTIVATE_WHEN = frozenset({"any_marker", "changed_globs"})
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
    # The effort profile that was resolved for this config. Informational on the
    # raw base/DEFAULT_CONFIG (no overlay applied); set to the active profile by
    # `load_config` once an overlay is layered in.
    profile: str = DEFAULT_PROFILE


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
class ActivationRule:
    """Conditional-activation predicate for a reviewer.

    A reviewer carrying an ``ActivationRule`` only runs when the PR matches it:
    either a marker file exists somewhere in the repo (``any_marker``) OR a
    changed path matches one of ``changed_globs``. A reviewer with no rule
    (``activate_when is None``) is always-on.
    """

    any_marker: tuple[str, ...] = ()
    changed_globs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewerOverride:
    name: str
    tool_tier: str = "read-only"
    replicas: int = 1
    enabled: bool = True
    prompt_extra: str | None = None
    # None → always-on; otherwise the reviewer is gated by this predicate.
    activate_when: ActivationRule | None = None
    # Name of a bundled Cortex skill the reviewer loads as its checklist.
    skill: str | None = None


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


def _profile_overlays() -> dict[str, dict[str, Any]]:
    """Built-in effort profiles, as config-dict overlays merged over the base.

    Each overlay is a strict subset of the config-dict shape and is layered via
    the same `_deep_merge`/`_merge_reviewers` machinery the file loader uses, so
    a repo's `.coco-pr-review.yml` can still override anything a profile sets.

    Reviewer scope is correctness/security focused (mirrors Anthropic's hosted
    Code Review). `style-and-conventions` and `tests-coverage` are off by default
    in both profiles; a repo re-enables one with a one-line overlay, e.g.
    `reviewers: [{name: tests-coverage, enabled: true}]`. Replica counts, the
    verifier, and limits are all knobs — tune freely.
    """
    return {
        # snowflake — the default profile for Snowflake/dbt repos. Three
        # always-on reviewers (generic bugs/security, warehouse cost/perf,
        # Snowflake governance & security) plus two conditional reviewers that
        # only fire when the PR actually touches SQL or a dbt project. Each
        # Snowflake-specific reviewer loads one bundled Cortex skill as its
        # checklist. Style/test reviewers stay off (re-enable with a one-liner).
        "snowflake": {
            "limits": {"job_timeout_sec": 1800, "max_usd_per_pr": 4.00},
            "reviewers": [
                # Always-on (no activate_when).
                {"name": "bugs-and-security", "enabled": True, "replicas": 1},
                {
                    "name": "performance-and-cost",
                    "enabled": True,
                    "replicas": 1,
                    "skill": "warehouse",
                },
                {
                    "name": "snowflake-governance-security",
                    "enabled": True,
                    "replicas": 1,
                    "tool_tier": "read-only",
                    "skill": "data-governance",
                },
                # Conditional — only when the PR touches SQL.
                {
                    "name": "sql-correctness",
                    "enabled": True,
                    "replicas": 1,
                    "tool_tier": "read-only",
                    "skill": "sql-author",
                    "activate_when": {
                        "changed_globs": [
                            "**/*.sql",
                            "**/*.sql.jinja",
                            "**/*.sql.j2",
                        ]
                    },
                },
                # Conditional — only for dbt projects.
                {
                    "name": "dbt-transformation",
                    "enabled": True,
                    "replicas": 1,
                    "tool_tier": "read-only",
                    "skill": "dbt-projects-on-snowflake",
                    "activate_when": {
                        "any_marker": ["dbt_project.yml", "dbt_project.yaml"],
                        "changed_globs": [
                            "**/dbt_project.yml",
                            "**/models/**/*.sql",
                            "**/models/**/*.yml",
                            "**/models/**/*.yaml",
                        ],
                    },
                },
                # Off by default; re-enable per-repo with a one-line overlay.
                {"name": "style-and-conventions", "enabled": False},
                {"name": "tests-coverage", "enabled": False},
            ],
        },
        # high — the serious multi-lens review (bughunter-like): correctness +
        # performance lenses, verifier on, generous 30-min budget.
        "high": {
            "limits": {"job_timeout_sec": 1800, "max_usd_per_pr": 4.00},
            "reviewers": [
                {"name": "bugs-and-security", "enabled": True, "replicas": 1},
                {"name": "performance-and-cost", "enabled": True, "replicas": 1},
                {"name": "style-and-conventions", "enabled": False},
                {"name": "tests-coverage", "enabled": False},
            ],
        },
        # cheap — the quick pass: one correctness reviewer, verifier still on
        # (nearly free at low candidate counts), tight 10-min budget.
        "cheap": {
            "limits": {"job_timeout_sec": 600, "max_usd_per_pr": 1.00},
            "verifier": {"enabled": True},
            "reviewers": [
                {"name": "bugs-and-security", "enabled": True, "replicas": 1},
                {"name": "performance-and-cost", "enabled": False},
                {"name": "style-and-conventions", "enabled": False},
                {"name": "tests-coverage", "enabled": False},
            ],
        },
    }


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
    profile: str | None = None,
) -> CocoPRReviewConfig:
    """Load a config file and layer on an effort profile + optional CLI overrides.

    Precedence (lowest → highest): base defaults → profile overlay → file → CLI.

    Profile selection (highest → lowest): explicit ``profile=`` arg (e.g. a
    ``@coco-review cheap|high`` comment) > ``orchestration.profile`` in the CLI
    overrides > ``orchestration.profile`` in the file > ``DEFAULT_PROFILE``.

    ``load_config(None)`` with no profile returns the raw, un-profiled base
    (== ``DEFAULT_CONFIG``); profile resolution only kicks in once a path, CLI
    overrides, or an explicit ``profile`` is supplied.
    """
    if path is None and not cli_overrides and profile is None:
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

    active_profile = (
        profile
        or _profile_from_dict(cli_overrides)
        or _profile_from_dict(file_data)
        or DEFAULT_PROFILE
    )
    if active_profile not in PROFILE_NAMES:
        raise ConfigError(
            f"unknown effort profile {active_profile!r}; "
            f"allowed: {list(PROFILE_NAMES)}"
        )

    base = _deep_merge(
        _default_dict(),
        _profile_overlays()[active_profile],
        source=f"<profile:{active_profile}>",
    )
    merged = _deep_merge(base, file_data, source=file_label)

    if cli_overrides:
        merged = _deep_merge(merged, dict(cli_overrides), source="<cli-overrides>")

    # Record the profile that was actually applied.
    merged["orchestration"]["profile"] = active_profile

    return _build_from_dict(merged, source=file_label)


def _profile_from_dict(data: Mapping[str, Any] | None) -> str | None:
    """Extract ``orchestration.profile`` from a config-dict, if present."""
    if not isinstance(data, Mapping):
        return None
    orch = data.get("orchestration")
    if not isinstance(orch, Mapping):
        return None
    value = orch.get("profile")
    return value if isinstance(value, str) else None


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
    profile = data.get("profile", DEFAULT_PROFILE)
    if profile not in PROFILE_NAMES:
        raise ConfigError(
            f"{source}: orchestration.profile must be one of {list(PROFILE_NAMES)}, "
            f"got {profile!r}"
        )
    return OrchestrationConfig(mode=mode, profile=profile)


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
    activate_when = _build_activate_when(
        data.get("activate_when"), name=name, source=source
    )
    skill = data.get("skill")
    if skill is not None and not isinstance(skill, str):
        raise ConfigError(
            f"{source}: reviewer `{name}` skill must be a string or null, "
            f"got {type(skill).__name__}"
        )
    return ReviewerOverride(
        name=name,
        tool_tier=tool_tier,
        replicas=replicas,
        enabled=enabled,
        prompt_extra=prompt_extra,
        activate_when=activate_when,
        skill=skill,
    )


def _build_activate_when(
    value: Any, *, name: str, source: str
) -> ActivationRule | None:
    """Parse a reviewer's optional ``activate_when`` predicate.

    ``None`` (or absent) → always-on. Otherwise a mapping with optional
    ``any_marker`` and ``changed_globs`` string lists.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigError(
            f"{source}: reviewer `{name}` activate_when must be a mapping or null, "
            f"got {type(value).__name__}"
        )
    unknown = set(value.keys()) - _ALLOWED_ACTIVATE_WHEN
    if unknown:
        raise ConfigError(
            f"{source}: unknown key(s) in reviewer `{name}` activate_when: "
            f"{sorted(unknown)}. Allowed: {sorted(_ALLOWED_ACTIVATE_WHEN)}"
        )
    any_marker = _require_list_of_str(
        value.get("any_marker", []),
        key=f"reviewers[{name}].activate_when.any_marker",
        source=source,
    )
    changed_globs = _require_list_of_str(
        value.get("changed_globs", []),
        key=f"reviewers[{name}].activate_when.changed_globs",
        source=source,
    )
    return ActivationRule(
        any_marker=tuple(any_marker), changed_globs=tuple(changed_globs)
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
    "ActivationRule",
    "CocoPRReviewConfig",
    "ConfigError",
    "DEFAULT_CONFIG",
    "DEFAULT_PROFILE",
    "PROFILE_NAMES",
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
