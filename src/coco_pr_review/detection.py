"""Deterministic activation gate for conditional reviewers.

A reviewer carrying an :class:`~coco_pr_review.config.ActivationRule`
(``activate_when``) only runs when the PR matches it; a reviewer with no rule
is always-on. This keeps Snowflake/SQL/dbt reviewers from firing on pure-Python
PRs (and vice versa) without spending any model budget — the decision is a pure
function of the changed-file list and a couple of marker-file existence checks.

The matcher implements ``**`` (any number of path segments, including zero),
``*`` (within a single segment), and ``?`` semantics directly, because
``pathlib.PurePath.match`` does not support recursive ``**`` on Python < 3.13.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from coco_pr_review.config import ReviewerOverride


@lru_cache(maxsize=512)
def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate a POSIX-style glob (with ``**``) into an anchored regex."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    # `**/` — zero or more leading directory segments.
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    # trailing/standalone `**` — anything, including slashes.
                    out.append(".*")
                    i += 2
            else:
                # single `*` — anything except a path separator.
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _glob_match(path: str, pattern: str) -> bool:
    """Return True if POSIX ``path`` matches the glob ``pattern``."""
    return _compile_glob(pattern).match(path.replace("\\", "/")) is not None


def should_activate(
    override: ReviewerOverride,
    repo_root: Path | str,
    changed_files: Iterable[str],
) -> bool:
    """Decide whether a reviewer should run for this PR.

    Always-on (``activate_when is None``) → True. Otherwise True when either a
    declared marker file exists under ``repo_root`` OR any changed path matches
    one of the rule's ``changed_globs``.
    """
    rule = override.activate_when
    if rule is None:
        return True

    root = Path(repo_root)
    for marker in rule.any_marker:
        if (root / marker).is_file():
            return True

    if rule.changed_globs:
        paths = list(changed_files)
        for pattern in rule.changed_globs:
            for path in paths:
                if _glob_match(path, pattern):
                    return True

    return False


__all__ = ["should_activate"]
