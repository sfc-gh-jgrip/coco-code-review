"""Commit-comment publisher for branch (no-PR) reviews.

The PR :class:`~coco_pr_review.github.publisher.Publisher` posts inline review
comments, a sticky summary, and a check run. A pushed branch has no PR diff to
hang inline comments on, so this publisher posts a **summary-only** result: the
same sticky body (via the shared :func:`choose_sticky_body` WS-A guard) as a
commit comment, plus the severity check run keyed on the head SHA. It returns a
:class:`PublishReport` for symmetry with the PR path.
"""
from __future__ import annotations

from typing import Any, Callable

from github import GithubException

from coco_pr_review.github.branch_source import CommitCommentTarget
from coco_pr_review.github.checks import publish_check_run
from coco_pr_review.github.publisher import PublishReport
from coco_pr_review.github.sticky import choose_sticky_body, upsert_sticky_comment

_DEFAULT_BOT_LOGIN = "github-actions[bot]"


class CommitPublisher:
    """Posts a branch review as a commit comment + check run (no inline comments)."""

    def __init__(
        self,
        *,
        github: Any,
        repo_full_name: str,
        head_sha: str,
        sanitize_fn: Callable[[str], str],
        bot_login: str = _DEFAULT_BOT_LOGIN,
    ) -> None:
        self._github = github
        self._repo_full_name = repo_full_name
        self._head_sha = head_sha
        self._sanitize_fn = sanitize_fn
        self._bot_login = bot_login

    def publish(self, run_result: Any) -> PublishReport:
        """Upsert the sticky commit comment and create the check run."""
        repo = self._github.get_repo(self._repo_full_name)
        commit = repo.get_commit(self._head_sha)
        target = CommitCommentTarget(commit)

        skipped_reason: str | None = None
        sticky_comment_id = 0

        # Summary-only: branches have no PR diff, so no inline comments (posted=0).
        try:
            sticky_body = choose_sticky_body(run_result)
            sticky_comment = upsert_sticky_comment(
                target, sticky_body, self._sanitize_fn, bot_login=self._bot_login
            )
            sticky_comment_id = getattr(sticky_comment, "id", None) or 0
        except Exception:
            pass

        check_run_id = 0
        try:
            check_run_id = publish_check_run(
                repo, self._head_sha, run_result.findings, self._sanitize_fn
            )
            if check_run_id == 0:
                skipped_reason = "pat-no-checks"
        except GithubException as exc:
            if exc.status in (403, 404):
                skipped_reason = "pat-no-checks"
            else:
                raise

        return PublishReport(
            comments_posted=0,
            comments_skipped=0,
            check_run_id=check_run_id,
            sticky_comment_id=sticky_comment_id,
            reactions_attached=0,
            reactions_failed=0,
            skipped_reason=skipped_reason,
        )
