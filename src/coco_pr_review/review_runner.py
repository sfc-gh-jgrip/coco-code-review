"""Entry boundary for pre-review skip handling and orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from coco_pr_review.config import CocoPRReviewConfig
from coco_pr_review.github.diff import build_unified_diff
from coco_pr_review.github.sticky import (
    render_sticky_diff_too_large,
    render_sticky_failed,
    render_sticky_skipped,
    upsert_sticky_comment,
)
from coco_pr_review.orchestration.base import PullRequestContext
from coco_pr_review.skip import exceeds_diff_size, filter_changed_files, should_skip_bot_pr


@dataclass(frozen=True, slots=True)
class ReviewRunResult:
    status: str
    run_result: Any | None = None
    publish_report: Any | None = None


async def run_review(
    *,
    repo_root: Path,
    github_client: Any,
    config: CocoPRReviewConfig,
    reviewers: list[Any],
    verifier: Any,
    orchestrator: Any,
    publisher: Any,
    budget: Any,
    progress: Any,
    sanitize_fn: Callable[[str], str],
    unified_diff: str | None = None,
    conventions_text: str | None = None,
    force_review: bool = False,
) -> ReviewRunResult:
    """Run pre-review gating, orchestration, and publishing.

    ``force_review`` allows explicit/manual invocations to review draft PRs.
    """
    if should_skip_bot_pr(
        pr_author_is_bot=github_client.pr_author_is_bot,
        review_bot_prs=config.review_bot_prs,
    ):
        upsert_sticky_comment(
            github_client.pull_request,
            render_sticky_skipped(reason="pull request author is a bot account"),
            sanitize_fn,
            bot_login=github_client.bot_login,
        )
        return ReviewRunResult(status="skipped_bot_pr")

    if github_client.is_draft_pr and not force_review:
        upsert_sticky_comment(
            github_client.pull_request,
            render_sticky_skipped(reason="pull request is still marked as a draft"),
            sanitize_fn,
            bot_login=github_client.bot_login,
        )
        return ReviewRunResult(status="skipped_draft_pr")

    if "coco-review:skip" in github_client.label_names:
        upsert_sticky_comment(
            github_client.pull_request,
            render_sticky_skipped(reason="pull request has the `coco-review:skip` label"),
            sanitize_fn,
            bot_login=github_client.bot_login,
        )
        return ReviewRunResult(status="skipped_skip_label")

    filtered_files = filter_changed_files(github_client.changed_files, config.paths_ignore)
    if not filtered_files:
        upsert_sticky_comment(
            github_client.pull_request,
            render_sticky_skipped(reason="no reviewable files remain after applying ignore rules"),
            sanitize_fn,
            bot_login=github_client.bot_login,
        )
        return ReviewRunResult(status="skipped_no_reviewable_files")

    if exceeds_diff_size(filtered_files, config.max_diff_lines):
        upsert_sticky_comment(
            github_client.pull_request,
            render_sticky_diff_too_large(max_diff_lines=config.max_diff_lines),
            sanitize_fn,
            bot_login=github_client.bot_login,
        )
        return ReviewRunResult(status="skipped_diff_too_large")

    pr_context = PullRequestContext(
        repo_root=repo_root,
        changed_files=filtered_files,
        unified_diff=unified_diff if unified_diff is not None else build_unified_diff(filtered_files),
        conventions_text=conventions_text,
    )
    run_result = await orchestrator.run(
        pr_context=pr_context,
        reviewers=reviewers,
        verifier=verifier,
        budget=budget,
        progress=progress,
    )
    if getattr(run_result, "aborted", False):
        reason = getattr(run_result, "abort_reason", None) or "the review could not be completed"
        upsert_sticky_comment(
            github_client.pull_request,
            render_sticky_failed(reason=reason),
            sanitize_fn,
            bot_login=github_client.bot_login,
        )
        return ReviewRunResult(status="aborted", run_result=run_result)

    publish_report = publisher.publish(run_result)
    return ReviewRunResult(
        status="reviewed",
        run_result=run_result,
        publish_report=publish_report,
    )