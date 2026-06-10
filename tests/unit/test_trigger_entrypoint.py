"""Tests for GitHub event parsing and dispatch."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_review_run_result(*, status: str = "reviewed", aborted: bool = False):
    from coco_pr_review.review_runner import ReviewRunResult

    run_result = None
    if status in {"reviewed", "aborted"}:
        run_result = MagicMock()
        run_result.aborted = aborted
    return ReviewRunResult(status=status, run_result=run_result)


def test_resolve_head_sha_uses_payload_for_pull_request_event() -> None:
    """PR events carry the head SHA; no API lookup is performed."""
    from coco_pr_review.github_event import PullRequestEvent, resolve_head_sha

    github = MagicMock()
    event = PullRequestEvent(repo_full_name="owner/repo", pr_number=1, head_sha="a" * 40)

    assert resolve_head_sha(event, github) == "a" * 40
    github.get_repo.assert_not_called()


def test_resolve_head_sha_looks_up_pr_for_comment_event() -> None:
    """Comment triggers look up the live PR head SHA."""
    from coco_pr_review.github_event import IssueCommentEvent, resolve_head_sha

    github = MagicMock()
    github.get_repo.return_value.get_pull.return_value.head.sha = "c" * 40
    event = IssueCommentEvent(
        repo_full_name="owner/repo",
        pr_number=9,
        comment_body="@coco-review",
        author_association="MEMBER",
        is_pull_request=True,
    )

    assert resolve_head_sha(event, github) == "c" * 40
    github.get_repo.return_value.get_pull.assert_called_once_with(9)


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


def test_main_returns_non_zero_for_aborted_review() -> None:
    from coco_pr_review.github_event import main

    github = MagicMock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("GITHUB_WORKSPACE", "/tmp/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_HOST", "acct.snowflakecomputing.com")
        monkeypatch.setattr("coco_pr_review.github_event.find_config", lambda repo_root: Path("/tmp/coco.toml"))
        monkeypatch.setattr("coco_pr_review.github_event.load_config", lambda path, profile=None: MagicMock(limits=MagicMock(max_usd_per_pr=1, job_timeout_sec=1), reviewer=MagicMock(confidence_threshold=80)))
        monkeypatch.setattr("coco_pr_review.github_event.parse_agent_md", lambda path: MagicMock(system_prompt="prompt"))
        monkeypatch.setattr("coco_pr_review.github_event.discover_conventions", lambda repo_root: None)
        monkeypatch.setattr("coco_pr_review.github_event.Github", lambda auth: github)
        monkeypatch.setattr("coco_pr_review.github_event.load_event_payload", lambda event_path=None: {"repository": {"full_name": "owner/repo"}, "pull_request": {"number": 1, "head": {"sha": "a" * 40}}})
        monkeypatch.setattr("coco_pr_review.github_event.run_github_event", AsyncMock(return_value=_make_review_run_result(status="aborted", aborted=True)))

        assert main() == 1


def test_main_returns_zero_for_reviewed_result() -> None:
    from coco_pr_review.github_event import main

    github = MagicMock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("GITHUB_WORKSPACE", "/tmp/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "token")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
        monkeypatch.setenv("SNOWFLAKE_HOST", "acct.snowflakecomputing.com")
        monkeypatch.setattr("coco_pr_review.github_event.find_config", lambda repo_root: Path("/tmp/coco.toml"))
        monkeypatch.setattr("coco_pr_review.github_event.load_config", lambda path, profile=None: MagicMock(limits=MagicMock(max_usd_per_pr=1, job_timeout_sec=1), reviewer=MagicMock(confidence_threshold=80)))
        monkeypatch.setattr("coco_pr_review.github_event.parse_agent_md", lambda path: MagicMock(system_prompt="prompt"))
        monkeypatch.setattr("coco_pr_review.github_event.discover_conventions", lambda repo_root: None)
        monkeypatch.setattr("coco_pr_review.github_event.Github", lambda auth: github)
        monkeypatch.setattr("coco_pr_review.github_event.load_event_payload", lambda event_path=None: {"repository": {"full_name": "owner/repo"}, "pull_request": {"number": 1, "head": {"sha": "a" * 40}}})
        monkeypatch.setattr("coco_pr_review.github_event.run_github_event", AsyncMock(return_value=_make_review_run_result(status="reviewed", aborted=False)))

        assert main() == 0

# ---------------------------------------------------------------------------
# Effort-profile comment parsing (@coco-review cheap|high)
# ---------------------------------------------------------------------------


def test_parse_review_command_recognises_profiles() -> None:
    from coco_pr_review.github_event import parse_review_command

    assert parse_review_command("@coco-review cheap") == "cheap"
    assert parse_review_command("@coco-review high") == "high"
    assert parse_review_command("hey @coco-review  HIGH please") == "high"


def test_parse_review_command_bare_or_unknown_returns_none() -> None:
    from coco_pr_review.github_event import parse_review_command

    assert parse_review_command("@coco-review") is None
    assert parse_review_command("@coco-review turbo") is None
    assert parse_review_command("no mention here") is None
    assert parse_review_command("@coco-review\nthanks") is None


# ---------------------------------------------------------------------------
# Push (branch / no-PR) trigger
# ---------------------------------------------------------------------------


def _push_payload(*, ref="refs/heads/feature", after="e" * 40, default_branch="main"):
    return {
        "ref": ref,
        "after": after,
        "repository": {"full_name": "owner/repo", "default_branch": default_branch},
    }


def test_parse_push_event_extracts_branch_identity() -> None:
    from coco_pr_review.github_event import PushEvent, parse_push_event

    event = parse_push_event(_push_payload())

    assert event == PushEvent(
        repo_full_name="owner/repo",
        head_sha="e" * 40,
        ref="refs/heads/feature",
        default_branch="main",
    )


def test_parse_push_event_rejects_incomplete_payload() -> None:
    from coco_pr_review.github_event import UnsupportedGitHubEventError, parse_push_event

    with pytest.raises(UnsupportedGitHubEventError):
        parse_push_event({"ref": "refs/heads/feature"})  # missing repository/after


def test_should_review_push_reviews_non_default_branch_head() -> None:
    from coco_pr_review.github_event import parse_push_event, should_review_push

    assert should_review_push(parse_push_event(_push_payload(ref="refs/heads/feature"))) is True


def test_should_review_push_skips_default_branch() -> None:
    from coco_pr_review.github_event import parse_push_event, should_review_push

    payload = _push_payload(ref="refs/heads/main", default_branch="main")
    assert should_review_push(parse_push_event(payload)) is False


def test_should_review_push_skips_non_branch_ref() -> None:
    from coco_pr_review.github_event import parse_push_event, should_review_push

    payload = _push_payload(ref="refs/tags/v1.0")
    assert should_review_push(parse_push_event(payload)) is False


def test_should_review_push_skips_branch_deletion() -> None:
    from coco_pr_review.github_event import parse_push_event, should_review_push

    payload = _push_payload(after="0" * 40)
    assert should_review_push(parse_push_event(payload)) is False


@pytest.mark.asyncio
async def test_run_branch_event_dispatches_branch_source_with_force_review_true() -> None:
    from coco_pr_review.github.branch_source import BranchReviewSource
    from coco_pr_review.github_event import PushEvent, run_branch_event
    from coco_pr_review.orchestration.base import ChangedFile

    push_event = PushEvent(
        repo_full_name="owner/repo", head_sha="e" * 40, ref="refs/heads/feature", default_branch="main"
    )
    github = MagicMock()
    changed = [ChangedFile(path="f.py", line_ranges=[(1, 2)], patch="@@ -0,0 +1,2 @@\n+a\n+b\n")]
    run_review = AsyncMock(return_value="ok")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "coco_pr_review.github_event.changed_files_from_git",
            lambda *, base_ref, repo_root, head: (changed, "UNIFIED"),
        )
        monkeypatch.setattr("coco_pr_review.github_event.run_review", run_review)
        result = await run_branch_event(
            repo_root=Path("/tmp/repo"),
            config=MagicMock(),
            reviewers=[],
            verifier=MagicMock(),
            orchestrator=MagicMock(),
            publisher=MagicMock(),
            budget=MagicMock(),
            progress=MagicMock(),
            sanitize_fn=lambda body: body,
            push_event=push_event,
            github=github,
            base_ref="main",
        )

    assert result == "ok"
    kwargs = run_review.await_args.kwargs
    assert kwargs["force_review"] is True
    assert kwargs["unified_diff"] == "UNIFIED"
    source = kwargs["github_client"]
    assert isinstance(source, BranchReviewSource)
    assert source.repo_full_name == "owner/repo"
    assert source.head_sha == "e" * 40
    assert source.changed_files == changed


def test_run_branch_event_uses_base_ref_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_branch_review honours COCO_PR_REVIEW_BASE_REF over the push default branch."""
    from coco_pr_review import github_event

    captured: dict[str, object] = {}

    async def _fake_run_branch_event(**kwargs):
        captured.update(kwargs)
        return _make_review_run_result(status="reviewed")

    monkeypatch.setenv("COCO_PR_REVIEW_BASE_REF", "release-2.0")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
    monkeypatch.setenv("SNOWFLAKE_HOST", "acct.snowflakecomputing.com")
    monkeypatch.setattr(github_event, "load_config", lambda path, profile=None: MagicMock(limits=MagicMock(max_usd_per_pr=1)))
    monkeypatch.setattr(github_event, "_build_review_runtime", lambda repo_root, config: ([], MagicMock(), MagicMock(), MagicMock(), MagicMock(), None))
    monkeypatch.setattr(github_event, "Github", lambda auth: MagicMock())
    monkeypatch.setattr(github_event, "CommitPublisher", lambda **kw: MagicMock())
    monkeypatch.setattr(github_event, "run_branch_event", _fake_run_branch_event)

    rc = github_event._run_branch_review(
        repo_root=Path("/tmp/repo"), config_path=Path("/tmp/coco.toml"), payload=_push_payload()
    )

    assert rc == 0
    assert captured["base_ref"] == "release-2.0"


def test_run_branch_review_skips_default_branch_without_running() -> None:
    from coco_pr_review import github_event

    with pytest.MonkeyPatch.context() as monkeypatch:
        load_config = MagicMock()
        monkeypatch.setattr(github_event, "load_config", load_config)
        rc = github_event._run_branch_review(
            repo_root=Path("/tmp/repo"),
            config_path=Path("/tmp/coco.toml"),
            payload=_push_payload(ref="refs/heads/main", default_branch="main"),
        )

    assert rc == 0
    load_config.assert_not_called()


def test_main_routes_push_event_to_branch_review() -> None:
    from coco_pr_review import github_event

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("GITHUB_WORKSPACE", "/tmp/repo")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.delenv("COCO_PR_REVIEW_EVENT_NAME", raising=False)
        monkeypatch.setattr(github_event, "find_config", lambda repo_root: Path("/tmp/coco.toml"))
        monkeypatch.setattr(github_event, "load_event_payload", lambda event_path=None: _push_payload())
        branch_review = MagicMock(return_value=0)
        monkeypatch.setattr(github_event, "_run_branch_review", branch_review)

        assert github_event.main() == 0
        branch_review.assert_called_once()
        assert branch_review.call_args.kwargs["payload"] == _push_payload()


# ---------------------------------------------------------------------------
# Review runtime — enabled-reviewer loading + read-only/skill sandbox
# ---------------------------------------------------------------------------


def test_build_review_runtime_loads_only_enabled_reviewers(tmp_path: Path) -> None:
    """The snowflake profile loads its 5 enabled reviewers; style/tests are skipped."""
    from coco_pr_review.config import load_config
    from coco_pr_review.github_event import _build_review_runtime

    config = load_config(None, profile="snowflake")
    reviewers, verifier, *_ = _build_review_runtime(tmp_path, config)

    names = {r.name for r in reviewers}
    assert names == {
        "bugs-and-security",
        "performance-and-cost",
        "snowflake-governance-security",
        "sql-correctness",
        "dbt-transformation",
    }
    assert verifier.name == "verifier"


@pytest.mark.asyncio
async def test_reviewer_query_uses_readonly_skill_sandbox(tmp_path: Path, monkeypatch) -> None:
    """The reviewer query passes a disallowed_tools sandbox that keeps Read/Glob/Grep/skill."""
    import coco_pr_review.github_event as ge
    from coco_pr_review.config import load_config

    captured: dict[str, object] = {}

    def fake_query(*, prompt, options):
        captured["options"] = options

        async def _empty_stream():
            return
            yield  # pragma: no cover — makes this an async generator

        return _empty_stream()

    async def fake_run_one_query(*, message_stream):
        await message_stream.aclose()
        return ({"findings": []}, MagicMock(total_cost_usd=0.0, num_turns=0, files_read=[]))

    monkeypatch.setattr(ge, "query", fake_query)
    monkeypatch.setattr(ge, "run_one_query", fake_run_one_query)

    config = load_config(None, profile="snowflake")
    _, _, orchestrator, *_ = _build_runtime(ge, tmp_path, config)

    await orchestrator._run_one_query(
        system_prompt="You are sql-correctness.",
        user_prompt="diff",
        output_schema=None,
    )

    options = captured["options"]
    disallowed = set(options.disallowed_tools)
    # Mutating / data / network / subagent tools are removed.
    assert {"Bash", "SQL", "Write", "Edit"} <= disallowed
    # Read-only inspection + skill loading remain available.
    assert disallowed.isdisjoint({"Read", "Glob", "Grep", "skill"})


def _build_runtime(ge, repo_root, config):
    return ge._build_review_runtime(repo_root, config)


def test_resolve_config_applies_default_profile_without_config_file() -> None:
    """No .coco-pr-review.yml → snowflake (default) profile still applies."""
    from coco_pr_review.github_event import _resolve_config

    config = _resolve_config(None)
    assert config.orchestration.profile == "snowflake"
    enabled = {r.name for r in config.reviewers if r.enabled}
    assert "snowflake-governance-security" in enabled
    assert "sql-correctness" in enabled


def test_resolve_config_honors_comment_override_without_config_file() -> None:
    """A @coco-review cheap override applies even when no config file exists."""
    from coco_pr_review.github_event import _resolve_config

    config = _resolve_config(None, "cheap")
    assert config.orchestration.profile == "cheap"
