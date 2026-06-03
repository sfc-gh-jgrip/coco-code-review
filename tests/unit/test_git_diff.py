"""Tests for reconstructing changed files + unified diff from local git (branch mode)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(args: list[str], cwd: Path) -> str:
    import os

    env = {**os.environ, **_GIT_ENV}
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env
    ).stdout


@pytest.fixture()
def branch_repo(tmp_path: Path) -> Path:
    """A git repo on a feature branch with one modified file and one new file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)

    (repo / "keep.py").write_text("a = 1\nb = 2\nc = 3\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "base"], repo)

    _git(["checkout", "-b", "feature"], repo)
    # Modify an existing file and add a brand-new file.
    (repo / "keep.py").write_text("a = 1\nb = 22\nc = 3\n")
    (repo / "new.py").write_text("x = 10\ny = 20\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "feature work"], repo)
    return repo


def test_changed_files_from_git_returns_paths_and_ranges(branch_repo: Path) -> None:
    from coco_pr_review.git_diff import changed_files_from_git

    changed_files, unified_diff = changed_files_from_git(
        base_ref="main", repo_root=branch_repo
    )

    by_path = {cf.path: cf for cf in changed_files}
    assert set(by_path) == {"keep.py", "new.py"}

    # The modified line (b = 22 on line 2) shows up in keep.py's ranges.
    keep_lines = {
        line for start, end in by_path["keep.py"].line_ranges for line in range(start, end + 1)
    }
    assert 2 in keep_lines

    # The new file's added lines are covered.
    new_lines = {
        line for start, end in by_path["new.py"].line_ranges for line in range(start, end + 1)
    }
    assert new_lines >= {1, 2}


def test_changed_files_from_git_patch_is_hunk_body_only(branch_repo: Path) -> None:
    """Each file's patch matches GitHub's shape: hunk bodies, no ``diff --git`` header."""
    from coco_pr_review.git_diff import changed_files_from_git

    changed_files, _ = changed_files_from_git(base_ref="main", repo_root=branch_repo)
    for cf in changed_files:
        assert cf.patch is not None
        assert cf.patch.lstrip().startswith("@@")
        assert "diff --git" not in cf.patch


def test_changed_files_from_git_unified_diff_spans_files(branch_repo: Path) -> None:
    from coco_pr_review.git_diff import changed_files_from_git

    _, unified_diff = changed_files_from_git(base_ref="main", repo_root=branch_repo)
    assert "diff --git" in unified_diff
    assert "keep.py" in unified_diff
    assert "new.py" in unified_diff


def test_changed_files_from_git_reuses_parse_hunk_ranges(
    branch_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Line ranges come from the canonical ``parse_hunk_ranges`` (no duplicate regex)."""
    import coco_pr_review.git_diff as git_diff

    calls: list[str] = []
    real = git_diff.parse_hunk_ranges

    def _spy(patch: str):
        calls.append(patch)
        return real(patch)

    monkeypatch.setattr(git_diff, "parse_hunk_ranges", _spy)
    changed_files, _ = git_diff.changed_files_from_git(base_ref="main", repo_root=branch_repo)
    assert calls  # parse_hunk_ranges was used to derive ranges
    assert len(changed_files) == 2


def test_changed_files_from_git_empty_when_no_diff(tmp_path: Path) -> None:
    from coco_pr_review.git_diff import changed_files_from_git

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    (repo / "f.py").write_text("a = 1\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "base"], repo)

    changed_files, unified_diff = changed_files_from_git(
        base_ref="main", repo_root=repo, head="HEAD"
    )
    assert changed_files == []
    assert unified_diff == ""
