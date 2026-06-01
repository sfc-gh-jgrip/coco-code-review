"""Thin PyGithub wrapper exposing typed accessors for PR review workflows.

Centralizes the ``repo → pull_request → head_commit`` resolution chain so that
individual modules (sticky, checks, reactions) receive already-resolved objects
rather than repeating raw PyGithub boilerplate.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from github import Github
from github.Commit import Commit
from github.PullRequest import PullRequest
from github.Repository import Repository

from coco_pr_review.github.diff import parse_hunk_ranges
from coco_pr_review.orchestration.base import ChangedFile


_DEFAULT_BOT_LOGIN = "github-actions[bot]"


@dataclass
class GitHubClient:
    """Resolves a GitHub token + PR identity into typed PyGithub objects.

    Parameters
    ----------
    github:
        A pre-built ``Github(auth=Auth.Token(...))`` instance.
    repo_full_name:
        Repository in ``"owner/repo"`` format.
    pr_number:
        Pull request number (integer).
    head_sha:
        Full 40-char SHA of the PR head commit.
    bot_login:
        Login used when filtering bot-authored PR comments. Defaults to
        ``github-actions[bot]``; overridden when running under a GitHub App
        (e.g. ``coco-pr-review[bot]``).
    """

    github: Github
    repo_full_name: str
    pr_number: int
    head_sha: str
    bot_login: str = _DEFAULT_BOT_LOGIN

    @cached_property
    def repo(self) -> Repository:
        """The repository object."""
        return self.github.get_repo(self.repo_full_name)

    @cached_property
    def pull_request(self) -> PullRequest:
        """The pull request object."""
        return self.repo.get_pull(self.pr_number)

    @cached_property
    def head_commit(self) -> Commit:
        """The commit object for *head_sha* (needed by ``create_review``)."""
        return self.repo.get_commit(self.head_sha)

    @cached_property
    def pr_author_login(self) -> str | None:
        """The login of the PR author, if present."""
        user = getattr(self.pull_request, "user", None)
        return getattr(user, "login", None)

    @cached_property
    def pr_author_is_bot(self) -> bool:
        """Whether the PR author is a bot account."""
        user = getattr(self.pull_request, "user", None)
        return getattr(user, "type", None) == "Bot"

    @cached_property
    def is_draft_pr(self) -> bool:
        """Whether the pull request is still marked as a draft."""
        return bool(getattr(self.pull_request, "draft", False))

    @cached_property
    def label_names(self) -> set[str]:
        """The set of label names currently applied to the pull request."""
        return {
            label.name
            for label in getattr(self.pull_request, "labels", [])
            if getattr(label, "name", None)
        }

    @cached_property
    def changed_files(self) -> list[ChangedFile]:
        """Changed files materialized into orchestrator-friendly dataclasses.

        Line ranges come from the patch hunk headers when a patch is present;
        patchless files (e.g. binary blobs) fall back to a single whole-file
        range derived from the reported change count.
        """
        changed_files: list[ChangedFile] = []
        for file in self.pull_request.get_files():
            patch = getattr(file, "patch", None)
            if patch:
                line_ranges = parse_hunk_ranges(patch)
            else:
                changes = getattr(file, "changes", 0)
                line_ranges = [(1, changes)] if changes else []
            changed_files.append(
                ChangedFile(path=file.filename, line_ranges=line_ranges, patch=patch)
            )
        return changed_files
