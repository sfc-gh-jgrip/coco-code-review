"""Tests for GitHub event parsing and dispatch."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_parse_pull_request_event() -> None:
    from coco_pr_review.github_event import PullRequestEvent, parse_github_event

    payload = {
        "repository": {"full_name": "owner/repo"},
        "pull_request": {"number": 42, "head": {"sha": "a" * 40}},
    }

    event = parse_github_event(event_name="pull_request", payload=payload)

    assert event == PullRequestEvent(repo_full_name="owner/repo", pr_number=42, head_sha="a" * 40)


def test_parse_issue_comment_event_on_non_pr_issue() -> None:
    from coco_pr_review.github_event import IssueCommentEvent, parse_github_event

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 42},
        "comment": {"body": "@coco-review", "author_association": "MEMBER"},
    }

    event = parse_github_event(event_name="issue_comment", payload=payload)

    assert event == IssueCommentEvent(
        repo_full_name="owner/repo",
        pr_number=42,
        comment_body="@coco-review",
        author_association="MEMBER",
        is_pull_request=False,
    )


@pytest.mark.asyncio
async def test_run_github_event_dispatches_pull_request_with_force_review_false() -> None:
    from coco_pr_review.github_event import run_github_event

    payload = {
        "repository": {"full_name": "owner/repo"},
        "pull_request": {"number": 7, "head": {"sha": "b" * 40}},
    }
    github = MagicMock()
    repo = MagicMock()
    github.get_repo.return_value = repo

    run_review = AsyncMock(return_value="ok")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("coco_pr_review.github_event.run_review", run_review)
        result = await run_github_event(
            repo_root=Path("/tmp/repo"),
            config=MagicMock(),
            reviewers=[],
            verifier=MagicMock(),
            orchestrator=MagicMock(),
            publisher=MagicMock(),
            budget=MagicMock(),
            progress=MagicMock(),
            sanitize_fn=lambda body: body,
            event_name="pull_request",
            payload=payload,
            github=github,
        )

    assert result == "ok"
    assert run_review.await_args.kwargs["force_review"] is False
    github_client = run_review.await_args.kwargs["github_client"]
    assert github_client.repo_full_name == "owner/repo"
    assert github_client.pr_number == 7
    assert github_client.head_sha == "b" * 40


@pytest.mark.asyncio
async def test_run_github_event_rejects_non_pr_issue_comment() -> None:
    from coco_pr_review.github_event import UnsupportedGitHubEventError, run_github_event

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 7},
        "comment": {"body": "@coco-review", "author_association": "MEMBER"},
    }

    with pytest.raises(UnsupportedGitHubEventError, match="not an allowed @coco-review trigger"):
        await run_github_event(
            repo_root=Path("/tmp/repo"),
            config=MagicMock(),
            reviewers=[],
            verifier=MagicMock(),
            orchestrator=MagicMock(),
            publisher=MagicMock(),
            budget=MagicMock(),
            progress=MagicMock(),
            sanitize_fn=lambda body: body,
            event_name="issue_comment",
            payload=payload,
            github=MagicMock(),
        )


@pytest.mark.asyncio
async def test_run_github_event_rejects_comment_without_trigger_mention() -> None:
    from coco_pr_review.github_event import UnsupportedGitHubEventError, run_github_event

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 7, "pull_request": {"url": "https://example.test/pr/7"}},
        "comment": {"body": "please review", "author_association": "MEMBER"},
    }

    with pytest.raises(UnsupportedGitHubEventError, match="not an allowed @coco-review trigger"):
        await run_github_event(
            repo_root=Path("/tmp/repo"),
            config=MagicMock(),
            reviewers=[],
            verifier=MagicMock(),
            orchestrator=MagicMock(),
            publisher=MagicMock(),
            budget=MagicMock(),
            progress=MagicMock(),
            sanitize_fn=lambda body: body,
            event_name="issue_comment",
            payload=payload,
            github=MagicMock(),
        )


@pytest.mark.asyncio
async def test_run_github_event_rejects_non_maintainer_trigger() -> None:
    from coco_pr_review.github_event import UnsupportedGitHubEventError, run_github_event

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 7, "pull_request": {"url": "https://example.test/pr/7"}},
        "comment": {"body": "@coco-review", "author_association": "CONTRIBUTOR"},
    }

    with pytest.raises(UnsupportedGitHubEventError, match="not an allowed @coco-review trigger"):
        await run_github_event(
            repo_root=Path("/tmp/repo"),
            config=MagicMock(),
            reviewers=[],
            verifier=MagicMock(),
            orchestrator=MagicMock(),
            publisher=MagicMock(),
            budget=MagicMock(),
            progress=MagicMock(),
            sanitize_fn=lambda body: body,
            event_name="issue_comment",
            payload=payload,
            github=MagicMock(),
        )


@pytest.mark.asyncio
async def test_run_github_event_dispatches_maintainer_comment_with_force_review_true() -> None:
    from coco_pr_review.github_event import run_github_event

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 7, "pull_request": {"url": "https://example.test/pr/7"}},
        "comment": {"body": "please run @coco-review now", "author_association": "MEMBER"},
    }
    github = MagicMock()
    repo = MagicMock()
    repo.get_pull.return_value.head.sha = "c" * 40
    github.get_repo.return_value = repo
    run_review = AsyncMock(return_value="ok")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("coco_pr_review.github_event.run_review", run_review)
        result = await run_github_event(
            repo_root=Path("/tmp/repo"),
            config=MagicMock(),
            reviewers=[],
            verifier=MagicMock(),
            orchestrator=MagicMock(),
            publisher=MagicMock(),
            budget=MagicMock(),
            progress=MagicMock(),
            sanitize_fn=lambda body: body,
            event_name="issue_comment",
            payload=payload,
            github=github,
        )

    assert result == "ok"
    assert run_review.await_args.kwargs["force_review"] is True


@pytest.mark.asyncio
async def test_issue_comment_trigger_fetches_latest_pr_head_sha() -> None:
    from coco_pr_review.github_event import run_github_event

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 9, "pull_request": {"url": "https://example.test/pr/9"}},
        "comment": {"body": "@coco-review", "author_association": "OWNER"},
    }
    github = MagicMock()
    repo = MagicMock()
    repo.get_pull.return_value.head.sha = "d" * 40
    github.get_repo.return_value = repo
    run_review = AsyncMock(return_value="ok")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("coco_pr_review.github_event.run_review", run_review)
        await run_github_event(
            repo_root=Path("/tmp/repo"),
            config=MagicMock(),
            reviewers=[],
            verifier=MagicMock(),
            orchestrator=MagicMock(),
            publisher=MagicMock(),
            budget=MagicMock(),
            progress=MagicMock(),
            sanitize_fn=lambda body: body,
            event_name="issue_comment",
            payload=payload,
            github=github,
        )

    github_client = run_review.await_args.kwargs["github_client"]
    assert github_client.head_sha == "d" * 40
    repo.get_pull.assert_called_once_with(9)