"""Review source for a branch with no pull request (push-triggered review).

The PR path feeds ``run_review`` a :class:`~coco_pr_review.github.client.GitHubClient`.
A pushed branch has no PR, so :class:`BranchReviewSource` duck-types the exact
surface ``run_review`` consumes — gating flags, changed files, bot login, and a
``pull_request`` target for the sticky comment — while sourcing changes from git
instead of the GitHub PR API.

The sticky comment lands on the *commit* (there is no PR conversation):
:class:`CommitCommentTarget` adapts a PyGithub ``Commit`` onto the small
issue-comment surface (`get_issue_comments`/`create_issue_comment`) that the
sticky helpers require. Commit comments support edit-in-place via the summary
marker, so re-pushing updates the sticky rather than spamming new comments.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

from coco_pr_review.orchestration.base import ChangedFile

_DEFAULT_BOT_LOGIN = "github-actions[bot]"


@dataclass
class CommitCommentTarget:
    """Adapts a PyGithub ``Commit`` onto the issue-comment surface the sticky uses.

    ``find_sticky_comment``/``upsert_sticky_comment`` only call
    ``get_issue_comments()``, ``create_issue_comment(body=...)``, and (on the
    returned object) ``.edit(body=...)`` / ``.id`` — all of which map cleanly
    onto ``Commit.get_comments()`` / ``Commit.create_comment(body)`` and the
    resulting ``CommitComment``.
    """

    commit: Any

    def get_issue_comments(self) -> Any:
        return self.commit.get_comments()

    def create_issue_comment(self, body: str) -> Any:
        return self.commit.create_comment(body)


@dataclass
class BranchReviewSource:
    """A ``GitHubClient``-shaped source backed by a branch push rather than a PR.

    Exposes the gating surface ``run_review`` reads (``pr_author_is_bot``,
    ``is_draft_pr``, ``label_names``), the ``changed_files`` derived from git,
    the ``bot_login`` for sticky filtering, and a ``pull_request`` commit-comment
    target — so the existing review engine runs unchanged against a branch.
    """

    github: Any
    repo_full_name: str
    head_sha: str
    changed_files: list[ChangedFile]
    bot_login: str = _DEFAULT_BOT_LOGIN

    # A pushed branch has no PR author, draft state, or labels; these constants
    # make run_review's skip gates treat the branch as an always-eligible review.
    pr_author_is_bot: bool = False
    is_draft_pr: bool = False

    @property
    def label_names(self) -> set[str]:
        return set()

    @cached_property
    def repo(self) -> Any:
        return self.github.get_repo(self.repo_full_name)

    @cached_property
    def commit(self) -> Any:
        return self.repo.get_commit(self.head_sha)

    @cached_property
    def pull_request(self) -> CommitCommentTarget:
        """The sticky target: a commit-comment adapter (there is no PR to comment on)."""
        return CommitCommentTarget(self.commit)
