"""Tests for the branch (no-PR) review source and its commit-comment adapter."""
from __future__ import annotations

from unittest.mock import MagicMock

from coco_pr_review.orchestration.base import ChangedFile


def _make_source(**overrides):
    from coco_pr_review.github.branch_source import BranchReviewSource

    github = overrides.pop("github", MagicMock())
    kwargs = dict(
        github=github,
        repo_full_name="owner/repo",
        head_sha="a" * 40,
        changed_files=[ChangedFile(path="f.py", line_ranges=[(1, 3)], patch="@@ -0,0 +1,3 @@\n+x\n")],
        bot_login="github-actions[bot]",
    )
    kwargs.update(overrides)
    return BranchReviewSource(**kwargs)


def test_branch_source_presents_always_eligible_pr_surface() -> None:
    """A pushed branch has no author/draft/label surface; gating must treat it as eligible."""
    source = _make_source()

    assert source.pr_author_is_bot is False
    assert source.is_draft_pr is False
    assert source.label_names == set()
    assert "coco-review:skip" not in source.label_names
    assert source.bot_login == "github-actions[bot]"
    assert len(source.changed_files) == 1


def test_branch_source_resolves_commit_via_head_sha() -> None:
    github = MagicMock()
    repo = MagicMock()
    commit = MagicMock()
    github.get_repo.return_value = repo
    repo.get_commit.return_value = commit

    source = _make_source(github=github)
    target = source.pull_request

    github.get_repo.assert_called_once_with("owner/repo")
    repo.get_commit.assert_called_once_with("a" * 40)
    # The target adapts the commit, not the repo or a PR.
    assert target.commit is commit


def test_commit_comment_target_maps_issue_comment_surface_onto_commit() -> None:
    """get/create issue comments delegate to the commit's comment methods."""
    from coco_pr_review.github.branch_source import CommitCommentTarget

    commit = MagicMock()
    existing = [MagicMock()]
    commit.get_comments.return_value = existing
    created = MagicMock()
    commit.create_comment.return_value = created

    target = CommitCommentTarget(commit)

    assert target.get_issue_comments() is existing
    assert target.create_issue_comment(body="hello") is created
    commit.create_comment.assert_called_once_with("hello")


def test_upsert_sticky_edits_in_place_on_commit_comment_target() -> None:
    """The sticky marker lets a re-push update the existing commit comment, not spam new ones."""
    from coco_pr_review.github.branch_source import CommitCommentTarget
    from coco_pr_review.github.sticky import SUMMARY_MARKER, upsert_sticky_comment

    existing_comment = MagicMock()
    existing_comment.body = f"## old\n{SUMMARY_MARKER}\n"
    existing_comment.user.login = "github-actions[bot]"

    commit = MagicMock()
    commit.get_comments.return_value = [existing_comment]

    target = CommitCommentTarget(commit)
    result = upsert_sticky_comment(
        target, f"## new\n{SUMMARY_MARKER}\nupdated", lambda b: b, bot_login="github-actions[bot]"
    )

    # Edited in place; no new comment created.
    existing_comment.edit.assert_called_once()
    commit.create_comment.assert_not_called()
    assert result is existing_comment


def test_upsert_sticky_creates_new_commit_comment_when_absent() -> None:
    from coco_pr_review.github.branch_source import CommitCommentTarget
    from coco_pr_review.github.sticky import upsert_sticky_comment

    commit = MagicMock()
    commit.get_comments.return_value = []
    created = MagicMock()
    commit.create_comment.return_value = created

    target = CommitCommentTarget(commit)
    result = upsert_sticky_comment(target, "body", lambda b: b, bot_login="github-actions[bot]")

    commit.create_comment.assert_called_once_with("body")
    assert result is created
