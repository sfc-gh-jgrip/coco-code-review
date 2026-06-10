"""Tests for the deterministic reviewer activation gate (`detection.py`)."""
from __future__ import annotations

from pathlib import Path

from coco_pr_review.config import ActivationRule, ReviewerOverride
from coco_pr_review.detection import _glob_match, should_activate


# ---------------------------------------------------------------------------
# Glob matcher — `**` recursive semantics (pathlib.match lacks these on <3.13)
# ---------------------------------------------------------------------------


def test_glob_star_star_matches_at_any_depth() -> None:
    assert _glob_match("foo.sql", "**/*.sql")
    assert _glob_match("models/foo.sql", "**/*.sql")
    assert _glob_match("a/b/c/foo.sql", "**/*.sql")


def test_glob_single_star_does_not_cross_directories() -> None:
    # `*` matches within a segment; it must not swallow the extension class here.
    assert _glob_match("foo.sql", "*.sql")
    assert not _glob_match("foo.py", "*.sql")


def test_glob_mid_pattern_star_star() -> None:
    assert _glob_match("models/x.sql", "**/models/**/*.sql")
    assert _glob_match("analytics/models/staging/x.sql", "**/models/**/*.sql")
    assert not _glob_match("src/app.py", "**/models/**/*.sql")


def test_glob_no_match_for_unrelated_extension() -> None:
    assert not _glob_match("src/app.py", "**/*.sql")
    assert not _glob_match("README.md", "**/dbt_project.yml")


# ---------------------------------------------------------------------------
# should_activate
# ---------------------------------------------------------------------------


def _ov(name: str, rule: ActivationRule | None) -> ReviewerOverride:
    return ReviewerOverride(name=name, activate_when=rule)


def test_always_on_reviewer_activates_regardless(tmp_path: Path) -> None:
    ov = _ov("bugs-and-security", None)
    assert should_activate(ov, tmp_path, []) is True
    assert should_activate(ov, tmp_path, ["src/app.py"]) is True


def test_changed_glob_match_activates(tmp_path: Path) -> None:
    ov = _ov("sql-correctness", ActivationRule(changed_globs=("**/*.sql",)))
    assert should_activate(ov, tmp_path, ["models/revenue.sql"]) is True


def test_changed_glob_no_match_does_not_activate(tmp_path: Path) -> None:
    ov = _ov("sql-correctness", ActivationRule(changed_globs=("**/*.sql",)))
    assert should_activate(ov, tmp_path, ["src/app.py", "README.md"]) is False


def test_marker_file_present_activates(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: demo\n")
    ov = _ov(
        "dbt-transformation",
        ActivationRule(any_marker=("dbt_project.yml",), changed_globs=("**/models/**/*.sql",)),
    )
    # No SQL changed, but the repo IS a dbt project → activate on the marker.
    assert should_activate(ov, tmp_path, ["src/app.py"]) is True


def test_marker_absent_and_no_glob_match_does_not_activate(tmp_path: Path) -> None:
    ov = _ov(
        "dbt-transformation",
        ActivationRule(any_marker=("dbt_project.yml",), changed_globs=("**/models/**/*.sql",)),
    )
    assert should_activate(ov, tmp_path, ["src/app.py"]) is False


def test_python_only_pr_skips_sql_and_dbt(tmp_path: Path) -> None:
    """A pure-Python PR in a non-dbt repo activates neither conditional reviewer."""
    sql = _ov("sql-correctness", ActivationRule(changed_globs=("**/*.sql",)))
    dbt = _ov(
        "dbt-transformation",
        ActivationRule(any_marker=("dbt_project.yml",), changed_globs=("**/models/**/*.sql",)),
    )
    changed = ["src/app.py", "tests/test_app.py"]
    assert should_activate(sql, tmp_path, changed) is False
    assert should_activate(dbt, tmp_path, changed) is False
