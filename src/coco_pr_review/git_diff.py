"""Reconstruct changed files + a unified diff from local git for branch (no-PR) review.

The PR path materializes ``ChangedFile`` objects from the GitHub API
(``client.py``). For a branch that has *no* pull request, we derive the same
shape directly from git so the existing review engine runs unchanged: per-file
new-line ranges (parsed by the canonical :func:`parse_hunk_ranges`), a per-file
patch body matching GitHub's format (hunks only, no ``diff --git`` header), and
the full unified diff string.

Three-dot ``base_ref...head`` semantics are used so the diff reflects what the
branch *introduced* relative to its merge-base with ``base_ref`` — not unrelated
commits that landed on ``base_ref`` after the branch diverged.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from coco_pr_review.github.diff import parse_hunk_ranges
from coco_pr_review.orchestration.base import ChangedFile


def _git(args: list[str], repo_root: Path) -> str:
    """Run a git command in *repo_root* and return its stdout."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
        env=os.environ,
    ).stdout


def _patch_body(per_file_diff: str) -> str | None:
    """Strip the ``diff --git``/``index``/``---``/``+++`` header, keeping hunks only.

    Mirrors GitHub's per-file ``patch`` shape so a ``ChangedFile`` from git is
    interchangeable with one from the PR API. Returns ``None`` when the file has
    no textual hunks (e.g. a pure rename, mode change, or binary blob).
    """
    lines = per_file_diff.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("@@"):
            return "".join(lines[index:])
    return None


def changed_files_from_git(
    *,
    base_ref: str,
    repo_root: Path,
    head: str = "HEAD",
) -> tuple[list[ChangedFile], str]:
    """Return changed files (vs the ``base_ref`` merge-base) plus the unified diff.

    Parameters
    ----------
    base_ref:
        The ref to diff against (e.g. the default branch). Three-dot semantics
        anchor the diff at the merge-base of ``base_ref`` and ``head``.
    repo_root:
        Path to the checked-out git working tree.
    head:
        The branch head to review; defaults to the checked-out ``HEAD``.
    """
    diff_range = f"{base_ref}...{head}"
    unified_diff = _git(["diff", diff_range], repo_root)
    names = _git(["diff", "--name-only", diff_range], repo_root).split("\n")

    changed_files: list[ChangedFile] = []
    for raw_path in names:
        path = raw_path.strip()
        if not path:
            continue
        per_file = _git(["diff", diff_range, "--", path], repo_root)
        patch = _patch_body(per_file)
        line_ranges = parse_hunk_ranges(patch) if patch else []
        changed_files.append(
            ChangedFile(path=path, line_ranges=line_ranges, patch=patch)
        )
    return changed_files, unified_diff
