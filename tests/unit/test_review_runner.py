"""Tests for `coco_pr_review.review_runner` public behavior."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_run_result(*, aborted: bool = False, abort_reason: str | None = None) -> MagicMock:
    run_result = MagicMock()
    run_result.aborted = aborted
    run_result.abort_reason = abort_reason
    return run_result


def _make_github_client() -> MagicMock:
    github_client = MagicMock()
    github_client.pr_author_is_bot = False
    github_client.pr_author_login = "github-actions[bot]"
    github_client.is_draft_pr = False
    github_client.label_names = set()
    return github_client


@pytest.mark.asyncio
async def test_run_review_skips_bot_pr_before_orchestrator() -> None:
    """Bot-authored PRs are skipped early when config disables bot reviews."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.pr_author_is_bot = True

    config = DEFAULT_CONFIG
    sanitize_fn = MagicMock(side_effect=lambda body: body)
    orchestrator = MagicMock()
    publisher = MagicMock()

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=config,
        reviewers=[],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=sanitize_fn,
    )

    assert result.status == "skipped_bot_pr"
    orchestrator.run.assert_not_called()
    publisher.publish.assert_not_called()
    github_client.pull_request.create_issue_comment.assert_called_once()


@pytest.mark.asyncio
async def test_run_review_passes_bot_login_to_skip_sticky_upsert() -> None:
    """Skip sticky updates preserve the bot-author filter during lookup."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.pr_author_is_bot = True
    github_client.bot_login = "github-actions[bot]"
    existing_comment = MagicMock()
    existing_comment.body = "## sticky\n<!-- coco-pr-review:summary -->"
    existing_comment.user.login = "github-actions[bot]"
    github_client.pull_request.get_issue_comments.return_value = [existing_comment]

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=DEFAULT_CONFIG,
        reviewers=[],
        verifier=MagicMock(),
        orchestrator=MagicMock(),
        publisher=MagicMock(),
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
    )

    assert result.status == "skipped_bot_pr"
    existing_comment.edit.assert_called_once()
    github_client.pull_request.create_issue_comment.assert_not_called()


@pytest.mark.asyncio
async def test_run_review_skips_draft_pr_before_filtering() -> None:
    """Draft PRs are skipped before file filtering or orchestration."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.is_draft_pr = True
    github_client.changed_files = MagicMock()

    config = DEFAULT_CONFIG
    sanitize_fn = MagicMock(side_effect=lambda body: body)
    orchestrator = MagicMock()
    publisher = MagicMock()

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=config,
        reviewers=[],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=sanitize_fn,
    )

    assert result.status == "skipped_draft_pr"
    orchestrator.run.assert_not_called()
    publisher.publish.assert_not_called()
    assert github_client.changed_files.mock_calls == []
    github_client.pull_request.create_issue_comment.assert_called_once()


@pytest.mark.asyncio
async def test_run_review_allows_forced_review_for_draft_pr() -> None:
    """Forced runs can review draft PRs without posting a skip comment."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.orchestration.base import ChangedFile
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.is_draft_pr = True
    github_client.changed_files = [ChangedFile(path="src/app.py", line_ranges=[(1, 3)])]

    run_result = _make_run_result()
    publish_report = MagicMock()

    orchestrator = MagicMock()
    orchestrator.run = AsyncMock(return_value=run_result)
    publisher = MagicMock()
    publisher.publish.return_value = publish_report

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=DEFAULT_CONFIG,
        reviewers=[MagicMock()],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
        force_review=True,
    )

    assert result.status == "reviewed"
    assert result.run_result is run_result
    assert result.publish_report is publish_report
    orchestrator.run.assert_called_once()
    publisher.publish.assert_called_once_with(run_result)
    github_client.pull_request.create_issue_comment.assert_not_called()


@pytest.mark.asyncio
async def test_run_review_returns_aborted_without_publishing() -> None:
    """Aborted orchestration exits without publishing a misleading clean result."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.orchestration.base import ChangedFile
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.changed_files = [ChangedFile(path="src/app.py", line_ranges=[(1, 3)])]

    run_result = _make_run_result(aborted=True, abort_reason="all reviewer replicas failed")

    orchestrator = MagicMock()
    orchestrator.run = AsyncMock(return_value=run_result)
    publisher = MagicMock()

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=DEFAULT_CONFIG,
        reviewers=[MagicMock()],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
    )

    assert result.status == "aborted"
    assert result.run_result is run_result
    assert result.publish_report is None
    publisher.publish.assert_not_called()


@pytest.mark.asyncio
async def test_run_review_skips_pr_with_skip_label_before_filtering() -> None:
    """Skip label short-circuits review before file filtering or orchestration."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.label_names = {"coco-review:skip", "needs-triage"}
    github_client.changed_files = MagicMock()

    orchestrator = MagicMock()
    publisher = MagicMock()

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=DEFAULT_CONFIG,
        reviewers=[],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
    )

    assert result.status == "skipped_skip_label"
    orchestrator.run.assert_not_called()
    publisher.publish.assert_not_called()
    assert github_client.changed_files.mock_calls == []
    github_client.pull_request.create_issue_comment.assert_called_once()


@pytest.mark.asyncio
async def test_run_review_skips_large_diff_after_filtering() -> None:
    """Diff-size gating uses filtered files and avoids orchestrator execution."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.orchestration.base import ChangedFile
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.changed_files = [
        ChangedFile(path="vendor/big.lock", line_ranges=[(1, 5000)]),
        ChangedFile(path="src/app.py", line_ranges=[(1, 6)]),
    ]

    config = DEFAULT_CONFIG
    config = config.__class__(**{**config.__dict__, "paths_ignore": ["vendor/**"], "max_diff_lines": 5})

    orchestrator = MagicMock()
    publisher = MagicMock()

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=config,
        reviewers=[],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
    )

    assert result.status == "skipped_diff_too_large"
    orchestrator.run.assert_not_called()
    publisher.publish.assert_not_called()
    github_client.pull_request.create_issue_comment.assert_called_once()


@pytest.mark.asyncio
async def test_run_review_builds_filtered_context_for_happy_path() -> None:
    """The runner passes filtered files through to the orchestrator and publisher."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.orchestration.base import ChangedFile
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.changed_files = [
        ChangedFile(path="generated/code.py", line_ranges=[(1, 2)]),
        ChangedFile(path="src/app.py", line_ranges=[(10, 12)]),
        ChangedFile(path="src/util.py", line_ranges=[(20, 21)]),
    ]

    config = DEFAULT_CONFIG
    config = config.__class__(**{**config.__dict__, "paths_ignore": ["generated/**"], "max_diff_lines": 10})

    run_result = _make_run_result()
    publish_report = MagicMock()

    async def fake_run(**kwargs: object) -> object:
        pr_context = kwargs["pr_context"]
        assert [changed_file.path for changed_file in pr_context.changed_files] == ["src/app.py", "src/util.py"]
        return run_result

    orchestrator = MagicMock()
    orchestrator.run.side_effect = fake_run
    publisher = MagicMock()
    publisher.publish.return_value = publish_report

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=config,
        reviewers=[MagicMock()],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
        unified_diff="diff text",
        conventions_text="team conventions",
    )

    assert result.status == "reviewed"
    assert result.run_result is run_result
    assert result.publish_report is publish_report
    publisher.publish.assert_called_once_with(run_result)


@pytest.mark.asyncio
async def test_run_review_derives_unified_diff_from_filtered_files() -> None:
    """When no diff is supplied, the runner builds one from filtered file patches."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.orchestration.base import ChangedFile
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.changed_files = [
        ChangedFile(path="generated/code.py", line_ranges=[(1, 1)], patch="@@ -1 +1 @@\n+ignored"),
        ChangedFile(path="src/app.py", line_ranges=[(1, 1)], patch="@@ -1 +1 @@\n+kept"),
    ]

    config = DEFAULT_CONFIG
    config = config.__class__(**{**config.__dict__, "paths_ignore": ["generated/**"], "max_diff_lines": 100})

    captured: dict[str, object] = {}

    async def fake_run(**kwargs: object) -> object:
        captured["unified_diff"] = kwargs["pr_context"].unified_diff
        return _make_run_result()

    orchestrator = MagicMock()
    orchestrator.run.side_effect = fake_run
    publisher = MagicMock()
    publisher.publish.return_value = MagicMock()

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=config,
        reviewers=[MagicMock()],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
    )

    assert result.status == "reviewed"
    diff = captured["unified_diff"]
    assert "b/src/app.py" in diff
    assert "+kept" in diff
    assert "generated/code.py" not in diff


@pytest.mark.asyncio
async def test_run_review_prefers_explicit_unified_diff_over_derived() -> None:
    """An explicitly-passed unified diff is forwarded verbatim, not regenerated."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.orchestration.base import ChangedFile
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.changed_files = [
        ChangedFile(path="src/app.py", line_ranges=[(1, 1)], patch="@@ -1 +1 @@\n+derived"),
    ]

    captured: dict[str, object] = {}

    async def fake_run(**kwargs: object) -> object:
        captured["unified_diff"] = kwargs["pr_context"].unified_diff
        return _make_run_result()

    orchestrator = MagicMock()
    orchestrator.run.side_effect = fake_run
    publisher = MagicMock()
    publisher.publish.return_value = MagicMock()

    await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=DEFAULT_CONFIG,
        reviewers=[MagicMock()],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
        unified_diff="explicit diff text",
    )

    assert captured["unified_diff"] == "explicit diff text"


@pytest.mark.asyncio
async def test_run_review_bot_skip_takes_precedence_over_draft_and_label() -> None:
    """Bot gating wins when multiple early-exit conditions overlap."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.pr_author_is_bot = True
    github_client.is_draft_pr = True
    github_client.label_names = {"coco-review:skip"}
    github_client.changed_files = MagicMock()

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=DEFAULT_CONFIG,
        reviewers=[],
        verifier=MagicMock(),
        orchestrator=MagicMock(),
        publisher=MagicMock(),
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
    )

    assert result.status == "skipped_bot_pr"
    assert github_client.changed_files.mock_calls == []


@pytest.mark.asyncio
async def test_run_review_draft_skip_takes_precedence_over_label_and_filtering() -> None:
    """Draft gating runs before label checks and changed-file inspection."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.is_draft_pr = True
    github_client.label_names = {"coco-review:skip"}
    github_client.changed_files = MagicMock()

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=DEFAULT_CONFIG,
        reviewers=[],
        verifier=MagicMock(),
        orchestrator=MagicMock(),
        publisher=MagicMock(),
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
    )

    assert result.status == "skipped_draft_pr"
    assert github_client.changed_files.mock_calls == []


@pytest.mark.asyncio
async def test_run_review_skip_label_takes_precedence_over_filtering() -> None:
    """The explicit skip label wins before ignore filtering or size checks."""
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.review_runner import run_review

    github_client = _make_github_client()
    github_client.label_names = {"coco-review:skip"}
    github_client.changed_files = MagicMock()

    result = await run_review(
        repo_root=Path("/tmp/repo"),
        github_client=github_client,
        config=DEFAULT_CONFIG,
        reviewers=[],
        verifier=MagicMock(),
        orchestrator=MagicMock(),
        publisher=MagicMock(),
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
    )

    assert result.status == "skipped_skip_label"
    assert github_client.changed_files.mock_calls == []