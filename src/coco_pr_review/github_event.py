"""GitHub Actions event parsing and dispatch for PR review runs."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import asyncio
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex_code_agent_sdk import CortexCodeAgentOptions, query
from github import Auth, Github

from coco_pr_review.config import (
    DEFAULT_PROFILE,
    PROFILE_NAMES,
    find_config,
    load_config,
)
from coco_pr_review.git_diff import changed_files_from_git
from coco_pr_review.github.branch_source import BranchReviewSource
from coco_pr_review.github.client import GitHubClient
from coco_pr_review.github.commit_publisher import CommitPublisher
from coco_pr_review.github.publisher import Publisher
from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
from coco_pr_review.orchestration.sdk_adapter import run_one_query
from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
from coco_pr_review.prompts import discover_conventions
from coco_pr_review.review_runner import ReviewRunResult, run_review
from coco_pr_review.reviewer_spec import parse_agent_md
from coco_pr_review.sanitize import redact

TRIGGER_MENTION = "@coco-review"
ALLOWED_AUTHOR_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

# Login used to find/filter bot-authored comments. Defaults to the
# GITHUB_TOKEN identity in Actions; a GitHub App run overrides it via the
# COCO_BOT_LOGIN env var (e.g. "coco-pr-review[bot]").
DEFAULT_BOT_LOGIN = "github-actions[bot]"

# Read-only + skill sandbox for reviewer queries. Reviewers are dispatched as
# the main agent of each `query()` (via append_system_prompt), so they inherit
# the CLI's full default tool surface unless we trim it. These canonical CLI
# tool ids are removed from the model's context so a reviewer can only Read /
# Glob / Grep the checked-out source and load its `skill` (lowercase tool id in
# the CLI). It cannot mutate files, run shell or SQL, reach the network, or
# spawn subagents. Unknown ids are harmless no-ops if the CLI renames a tool.
_REVIEWER_DISALLOWED_TOOLS: tuple[str, ...] = (
    "Bash",
    "SQL",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "notebook_actions",
    "fdbt",
    "WebFetch",
    "WebSearch",
    "Task",
    "AskUserQuestion",
    "TodoWrite",
)


class UnsupportedGitHubEventError(ValueError):
    """Raised when the current GitHub event cannot trigger a review."""


@dataclass(frozen=True, slots=True)
class PullRequestEvent:
    repo_full_name: str
    pr_number: int
    head_sha: str


@dataclass(frozen=True, slots=True)
class IssueCommentEvent:
    repo_full_name: str
    pr_number: int
    comment_body: str
    author_association: str
    is_pull_request: bool


@dataclass(frozen=True, slots=True)
class PushEvent:
    """A branch push with no pull request (the branch-review trigger)."""

    repo_full_name: str
    head_sha: str
    ref: str
    default_branch: str


def load_event_payload(event_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load the GitHub event payload JSON from disk."""
    resolved_event_path = Path(event_path or os.environ["GITHUB_EVENT_PATH"])
    with resolved_event_path.open("r", encoding="utf-8") as event_file:
        payload = json.load(event_file)
    if not isinstance(payload, dict):
        raise UnsupportedGitHubEventError("GitHub event payload must be a JSON object")
    return payload


def parse_pull_request_event(payload: dict[str, Any]) -> PullRequestEvent:
    """Extract PR identity from a pull_request event payload."""
    repository = payload.get("repository")
    pull_request = payload.get("pull_request")
    if not isinstance(repository, dict) or not isinstance(pull_request, dict):
        raise UnsupportedGitHubEventError("pull_request payload is missing repository or pull_request data")

    repo_full_name = repository.get("full_name")
    pr_number = pull_request.get("number") or payload.get("number")
    head = pull_request.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(repo_full_name, str) or not isinstance(pr_number, int) or not isinstance(head_sha, str):
        raise UnsupportedGitHubEventError("pull_request payload is missing repo, number, or head sha")
    return PullRequestEvent(repo_full_name=repo_full_name, pr_number=pr_number, head_sha=head_sha)


def parse_issue_comment_event(payload: dict[str, Any]) -> IssueCommentEvent:
    """Extract comment trigger metadata from an issue_comment event payload."""
    repository = payload.get("repository")
    issue = payload.get("issue")
    comment = payload.get("comment")
    if not isinstance(repository, dict) or not isinstance(issue, dict) or not isinstance(comment, dict):
        raise UnsupportedGitHubEventError("issue_comment payload is missing repository, issue, or comment data")

    repo_full_name = repository.get("full_name")
    pr_number = issue.get("number")
    comment_body = comment.get("body")
    author_association = comment.get("author_association")
    is_pull_request = isinstance(issue.get("pull_request"), dict)
    if not isinstance(repo_full_name, str) or not isinstance(pr_number, int):
        raise UnsupportedGitHubEventError("issue_comment payload is missing repo or issue number")
    if not isinstance(comment_body, str) or not isinstance(author_association, str):
        raise UnsupportedGitHubEventError("issue_comment payload is missing comment body or author association")

    return IssueCommentEvent(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        comment_body=comment_body,
        author_association=author_association,
        is_pull_request=is_pull_request,
    )


def parse_push_event(payload: dict[str, Any]) -> PushEvent:
    """Extract branch + head identity from a push event payload."""
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        raise UnsupportedGitHubEventError("push payload is missing repository data")

    repo_full_name = repository.get("full_name")
    default_branch = repository.get("default_branch")
    head_sha = payload.get("after")
    ref = payload.get("ref")
    if not (
        isinstance(repo_full_name, str)
        and isinstance(default_branch, str)
        and isinstance(head_sha, str)
        and isinstance(ref, str)
    ):
        raise UnsupportedGitHubEventError(
            "push payload is missing repo, default_branch, after, or ref"
        )
    return PushEvent(
        repo_full_name=repo_full_name,
        head_sha=head_sha,
        ref=ref,
        default_branch=default_branch,
    )


def should_review_push(event: PushEvent) -> bool:
    """Whether a push should trigger a branch review.

    Only branch-head pushes to a NON-default branch are reviewed: a push to the
    default branch would diff against itself (empty), and tag/non-branch refs
    have no branch to review. A branch deletion (all-zero ``after`` SHA) is
    skipped too — there is nothing checked out to review.
    """
    if not event.ref.startswith("refs/heads/"):
        return False
    branch = event.ref[len("refs/heads/") :]
    if branch == event.default_branch:
        return False
    if set(event.head_sha) <= {"0"}:
        return False
    return True


def parse_github_event(
    *,
    event_name: str | None = None,
    payload: dict[str, Any] | None = None,
    event_path: str | os.PathLike[str] | None = None,
) -> PullRequestEvent | IssueCommentEvent:
    """Parse the current GitHub event into a supported trigger shape."""
    resolved_event_name = event_name or os.environ["GITHUB_EVENT_NAME"]
    resolved_payload = payload if payload is not None else load_event_payload(event_path)
    if resolved_event_name == "pull_request":
        return parse_pull_request_event(resolved_payload)
    if resolved_event_name == "issue_comment":
        return parse_issue_comment_event(resolved_payload)
    raise UnsupportedGitHubEventError(f"Unsupported GitHub event: {resolved_event_name}")


def is_comment_review_trigger(event: IssueCommentEvent) -> bool:
    """Whether an issue comment is an allowed explicit review trigger."""
    return (
        event.is_pull_request
        and TRIGGER_MENTION in event.comment_body
        and event.author_association in ALLOWED_AUTHOR_ASSOCIATIONS
    )


_REVIEW_COMMAND = re.compile(re.escape(TRIGGER_MENTION) + r"\s+([A-Za-z]+)")


def parse_review_command(comment_body: str) -> str | None:
    """Extract an effort-profile override from a ``@coco-review`` comment.

    Recognises ``@coco-review cheap`` / ``@coco-review high``. A bare
    ``@coco-review`` (or any unrecognised token) returns ``None`` so the caller
    falls back to the config default profile. Tolerant of case and surrounding
    text.
    """
    match = _REVIEW_COMMAND.search(comment_body)
    if not match:
        return None
    token = match.group(1).lower()
    return token if token in PROFILE_NAMES else None


def _resolve_config(config_path: Any, profile_override: str | None = None) -> Any:
    """Load config, ensuring the default profile applies even with no config file.

    ``load_config(None)`` deliberately returns the raw, *unprofiled* base, so a
    consumer repo that ships no ``.coco-pr-review.yml`` would otherwise miss the
    default (snowflake) profile entirely. When there is no file we explicitly
    resolve the default profile (or a ``@coco-review`` override). When a file
    exists, its own ``orchestration.profile`` (or ``DEFAULT_PROFILE``) is honored
    as usual, with an explicit override still winning.
    """
    if config_path is None:
        return load_config(None, profile=profile_override or DEFAULT_PROFILE)
    return load_config(config_path, profile=profile_override)


def resolve_head_sha(
    event: PullRequestEvent | IssueCommentEvent,
    github: Github,
) -> str:
    """Resolve the PR head SHA for an event.

    ``pull_request`` events carry the head SHA in their payload; comment
    triggers must look it up from the live PR.
    """
    if isinstance(event, PullRequestEvent):
        return event.head_sha
    return github.get_repo(event.repo_full_name).get_pull(event.pr_number).head.sha


def build_github_client(
    *,
    event: PullRequestEvent | IssueCommentEvent,
    github: Github | None = None,
    github_token: str | None = None,
    bot_login: str = DEFAULT_BOT_LOGIN,
) -> GitHubClient:
    """Construct a GitHub client for the event using the latest PR head SHA."""
    resolved_github = github
    if resolved_github is None:
        token = github_token or os.environ["GITHUB_TOKEN"]
        resolved_github = Github(auth=Auth.Token(token))

    return GitHubClient(
        github=resolved_github,
        repo_full_name=event.repo_full_name,
        pr_number=event.pr_number,
        head_sha=resolve_head_sha(event, resolved_github),
        bot_login=bot_login,
    )


async def run_github_event(
    *,
    repo_root: Path,
    config: Any,
    reviewers: list[Any],
    verifier: Any,
    orchestrator: Any,
    publisher: Any,
    budget: Any,
    progress: Any,
    sanitize_fn: Any,
    unified_diff: str | None = None,
    conventions_text: str | None = None,
    event_name: str | None = None,
    payload: dict[str, Any] | None = None,
    event_path: str | os.PathLike[str] | None = None,
    event: PullRequestEvent | IssueCommentEvent | None = None,
    github: Github | None = None,
    github_token: str | None = None,
    bot_login: str = DEFAULT_BOT_LOGIN,
) -> ReviewRunResult:
    """Dispatch a supported GitHub event into the review runner.

    Callers may pass an already-parsed ``event`` to avoid re-parsing the
    payload; when omitted it is parsed from ``event_name``/``payload``/
    ``event_path``.
    """
    if event is None:
        event = parse_github_event(event_name=event_name, payload=payload, event_path=event_path)
    if isinstance(event, IssueCommentEvent) and not is_comment_review_trigger(event):
        raise UnsupportedGitHubEventError("issue_comment event is not an allowed @coco-review trigger")

    github_client = build_github_client(
        event=event, github=github, github_token=github_token, bot_login=bot_login
    )
    return await run_review(
        repo_root=repo_root,
        github_client=github_client,
        config=config,
        reviewers=reviewers,
        verifier=verifier,
        orchestrator=orchestrator,
        publisher=publisher,
        budget=budget,
        progress=progress,
        sanitize_fn=sanitize_fn,
        unified_diff=unified_diff,
        conventions_text=conventions_text,
        force_review=isinstance(event, IssueCommentEvent),
    )


async def run_branch_event(
    *,
    repo_root: Path,
    config: Any,
    reviewers: list[Any],
    verifier: Any,
    orchestrator: Any,
    publisher: Any,
    budget: Any,
    progress: Any,
    sanitize_fn: Any,
    push_event: PushEvent,
    github: Github,
    base_ref: str,
    bot_login: str = DEFAULT_BOT_LOGIN,
    conventions_text: str | None = None,
) -> ReviewRunResult:
    """Dispatch a branch push (no PR) into the review runner.

    Sources the changed files + unified diff from git (vs ``base_ref``) and feeds
    them through the same ``run_review`` engine via a ``BranchReviewSource``.
    A branch push is an explicit ask, so ``force_review=True`` (no draft gate).
    """
    changed_files, unified_diff = changed_files_from_git(
        base_ref=base_ref, repo_root=repo_root, head=push_event.head_sha
    )
    source = BranchReviewSource(
        github=github,
        repo_full_name=push_event.repo_full_name,
        head_sha=push_event.head_sha,
        changed_files=changed_files,
        bot_login=bot_login,
    )
    return await run_review(
        repo_root=repo_root,
        github_client=source,
        config=config,
        reviewers=reviewers,
        verifier=verifier,
        orchestrator=orchestrator,
        publisher=publisher,
        budget=budget,
        progress=progress,
        sanitize_fn=sanitize_fn,
        unified_diff=unified_diff,
        conventions_text=conventions_text,
        force_review=True,
    )


def resolve_event_context() -> tuple[str | None, str | None]:
    """Resolve the event name and path, preferring explicit smoke overrides.

    GitHub Actions does not reliably allow a step-level ``env:`` block to
    override the reserved ``GITHUB_EVENT_NAME``/``GITHUB_EVENT_PATH`` values, so
    the manual smoke workflow sets ``COCO_PR_REVIEW_EVENT_NAME`` and
    ``COCO_PR_REVIEW_EVENT_PATH`` instead. Those take precedence when present.
    """
    event_name = os.environ.get("COCO_PR_REVIEW_EVENT_NAME") or os.environ.get("GITHUB_EVENT_NAME")
    event_path = os.environ.get("COCO_PR_REVIEW_EVENT_PATH") or os.environ.get("GITHUB_EVENT_PATH")
    return event_name, event_path


def _build_review_runtime(
    repo_root: Path, config: Any
) -> tuple[list[Any], Any, Any, Any, Any, str | None]:
    """Build the event-agnostic review runtime shared by the PR and branch paths.

    Returns ``(reviewers, verifier, orchestrator, budget, progress,
    conventions_text)``. Everything here is independent of *which* event
    triggered the run, so both the PR/comment dispatch and the branch (push)
    dispatch construct it identically.
    """
    # Load the bundled reviewer prompts from THIS package, not from repo_root
    # (the consumer's checkout). A consumer repo using this as a GitHub Action
    # won't contain src/coco_pr_review/agents/; the prompts ship inside the
    # pip-installed package, so resolve them relative to this module.
    reviewers_dir = Path(__file__).resolve().parent / "agents"
    # Load a spec for every reviewer ENABLED in the resolved config (the active
    # profile decides the set). The orchestrator further drops conditional
    # reviewers whose `activate_when` predicate does not match this PR (see
    # `detection`). Disabled reviewers are never loaded.
    reviewers = [
        parse_agent_md(reviewers_dir / f"{override.name}.md")
        for override in config.reviewers
        if override.enabled
    ]
    verifier = parse_agent_md(reviewers_dir / "verifier.md")

    async def run_one_query_with_sdk(
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, Any] | None = None,
        **_: Any,
    ) -> tuple[Any, Any]:
        # Forward the cortex CLI subprocess stderr to our stderr so that
        # SDK-level execution errors are visible in CI logs.
        def _on_stderr(line: str) -> None:
            print(f"[cortex-sdk] {line}", file=sys.stderr, flush=True)

        # Request structured output via JSON schema when the caller supplies one.
        # This makes the CLI emit `--json-schema`, populating ResultMessage
        # `structured_output` directly (no fence/prose recovery needed).
        output_format = (
            {"type": "json_schema", "schema": output_schema}
            if output_schema is not None
            else None
        )

        # Close the SDK async generator in the same task that iterates it.
        # Otherwise GC-time cleanup of query()'s internal anyio task group raises
        # "Attempted to exit cancel scope in a different task than it was entered in".
        async with aclosing(
            query(
                prompt=user_prompt,
                options=CortexCodeAgentOptions(
                    cwd=str(repo_root),
                    append_system_prompt=system_prompt,
                    stderr=_on_stderr,
                    output_format=output_format,
                    # Read-only + skill sandbox: reviewers keep Read/Glob/Grep
                    # and the `skill` tool (lowercase, per the CLI) but cannot
                    # mutate the checkout, run shell/SQL, hit the network, or
                    # spawn subagents. Names are the CLI's canonical tool ids;
                    # unknown names are harmless no-ops.
                    disallowed_tools=list(_REVIEWER_DISALLOWED_TOOLS),
                ),
            )
        ) as message_stream:
            return await run_one_query(message_stream=message_stream)

    orchestrator = PythonFanoutOrchestrator(run_one_query=run_one_query_with_sdk, config=config)
    budget = BudgetGate(max_usd=config.limits.max_usd_per_pr)
    progress = NoOpProgressSink()

    conventions_path = discover_conventions(repo_root)
    conventions_text = conventions_path.read_text() if conventions_path else None

    return reviewers, verifier, orchestrator, budget, progress, conventions_text


def _finish_result(result: Any) -> int:
    """Log the run summary and map a ``ReviewRunResult`` to a process exit code."""
    if not isinstance(result, ReviewRunResult):
        return 1

    run_result = result.run_result
    if run_result is not None:
        logging.getLogger("coco_pr_review").info(
            "review finished: status=%s candidates=%s deduped=%s verified=%s "
            "cost_usd=%s aborted=%s reason=%s",
            result.status,
            getattr(run_result, "candidate_count", None),
            getattr(run_result, "deduped_count", None),
            len(getattr(run_result, "findings", []) or []),
            getattr(run_result, "total_cost_usd", None),
            getattr(run_result, "aborted", None),
            getattr(run_result, "abort_reason", None),
        )
    else:
        logging.getLogger("coco_pr_review").info(
            "review finished: status=%s (no run_result)", result.status
        )

    if run_result is not None and getattr(run_result, "aborted", False):
        return 1
    return 0


def _run_branch_review(*, repo_root: Path, config_path: Any, payload: dict[str, Any]) -> int:
    """Dispatch a ``push`` event into a branch (no-PR) review."""
    push_event = parse_push_event(payload)
    if not should_review_push(push_event):
        logging.getLogger("coco_pr_review").info(
            "skipping push: %s is the default branch or not a branch head", push_event.ref
        )
        return 0

    config = _resolve_config(config_path)
    logging.getLogger("coco_pr_review").info(
        "effort profile: %s (config default)", config.orchestration.profile
    )

    if not os.environ.get("SNOWFLAKE_ACCOUNT") or not os.environ.get("SNOWFLAKE_HOST"):
        print("SNOWFLAKE_ACCOUNT and SNOWFLAKE_HOST must be set for the review runtime")
        return 1

    reviewers, verifier, orchestrator, budget, progress, conventions_text = (
        _build_review_runtime(repo_root, config)
    )
    github = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))
    bot_login = os.environ.get("COCO_BOT_LOGIN", DEFAULT_BOT_LOGIN)
    # Base ref defaults to the repo's default branch; an env override lets a
    # consumer review against a different baseline without a config-schema change.
    base_ref = os.environ.get("COCO_PR_REVIEW_BASE_REF") or push_event.default_branch

    publisher = CommitPublisher(
        github=github,
        repo_full_name=push_event.repo_full_name,
        head_sha=push_event.head_sha,
        sanitize_fn=redact,
        bot_login=bot_login,
    )
    result = asyncio.run(
        run_branch_event(
            repo_root=repo_root,
            config=config,
            reviewers=reviewers,
            verifier=verifier,
            orchestrator=orchestrator,
            publisher=publisher,
            budget=budget,
            progress=progress,
            sanitize_fn=redact,
            push_event=push_event,
            github=github,
            base_ref=base_ref,
            bot_login=bot_login,
            conventions_text=conventions_text,
        )
    )
    return _finish_result(result)


def main() -> int:
    """CLI entrypoint for GitHub Actions event dispatch."""
    logging.basicConfig(
        level=os.environ.get("COCO_PR_REVIEW_LOG_LEVEL", "INFO").upper(),
        stream=sys.stdout,
        format="%(levelname)s %(name)s: %(message)s",
    )
    repo_root = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    config_path = find_config(repo_root)

    event_name, event_path = resolve_event_context()

    # Load the payload once, then dispatch by event kind. A `push` event is a
    # branch (no-PR) review, handled before `parse_github_event` (which only
    # understands pull_request/issue_comment). A `@coco-review cheap|high`
    # comment can override the effort profile, so the event is parsed before
    # config is loaded.
    try:
        payload = load_event_payload(event_path)
        if event_name == "push":
            return _run_branch_review(
                repo_root=repo_root, config_path=config_path, payload=payload
            )
        event = parse_github_event(
            event_name=event_name, payload=payload, event_path=event_path
        )
    except UnsupportedGitHubEventError as exc:
        print(str(exc))
        return 1

    profile_override = (
        parse_review_command(event.comment_body)
        if isinstance(event, IssueCommentEvent)
        else None
    )
    config = _resolve_config(config_path, profile_override)
    logging.getLogger("coco_pr_review").info(
        "effort profile: %s%s",
        config.orchestration.profile,
        " (from @coco-review comment)" if profile_override else " (config default)",
    )

    if not os.environ.get("SNOWFLAKE_ACCOUNT") or not os.environ.get("SNOWFLAKE_HOST"):
        print("SNOWFLAKE_ACCOUNT and SNOWFLAKE_HOST must be set for the review runtime")
        return 1

    reviewers, verifier, orchestrator, budget, progress, conventions_text = (
        _build_review_runtime(repo_root, config)
    )
    github = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))

    try:
        bot_login = os.environ.get("COCO_BOT_LOGIN", DEFAULT_BOT_LOGIN)
        head_sha = resolve_head_sha(event, github)
        publisher = Publisher(
            github=github,
            repo_full_name=event.repo_full_name,
            pr_number=event.pr_number,
            head_sha=head_sha,
            sanitize_fn=redact,
            bot_login=bot_login,
        )
        result = asyncio.run(
            run_github_event(
                repo_root=repo_root,
                config=config,
                reviewers=reviewers,
                verifier=verifier,
                orchestrator=orchestrator,
                publisher=publisher,
                budget=budget,
                progress=progress,
                sanitize_fn=redact,
                conventions_text=conventions_text,
                event=event,
                github=github,
                bot_login=bot_login,
            )
        )
    except UnsupportedGitHubEventError as exc:
        print(str(exc))
        return 1

    return _finish_result(result)