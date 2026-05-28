"""GitHub Actions event parsing and dispatch for PR review runs."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from github import Auth, Github

from coco_pr_review.config import find_config, load_config
from coco_pr_review.github.client import GitHubClient
from coco_pr_review.github.publisher import Publisher
from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
from coco_pr_review.prompts import discover_conventions
from coco_pr_review.review_runner import ReviewRunResult, run_review
from coco_pr_review.reviewer_spec import parse_agent_md
from coco_pr_review.sanitize import redact

TRIGGER_MENTION = "@coco-review"
ALLOWED_AUTHOR_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


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


def build_github_client(
    *,
    event: PullRequestEvent | IssueCommentEvent,
    github: Github | None = None,
    github_token: str | None = None,
) -> GitHubClient:
    """Construct a GitHub client for the event using the latest PR head SHA."""
    resolved_github = github
    if resolved_github is None:
        token = github_token or os.environ["GITHUB_TOKEN"]
        resolved_github = Github(auth=Auth.Token(token))

    repo = resolved_github.get_repo(event.repo_full_name)
    if isinstance(event, PullRequestEvent):
        head_sha = event.head_sha
    else:
        head_sha = repo.get_pull(event.pr_number).head.sha

    return GitHubClient(
        github=resolved_github,
        repo_full_name=event.repo_full_name,
        pr_number=event.pr_number,
        head_sha=head_sha,
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
    github: Github | None = None,
    github_token: str | None = None,
) -> ReviewRunResult:
    """Dispatch a supported GitHub event into the review runner."""
    event = parse_github_event(event_name=event_name, payload=payload, event_path=event_path)
    if isinstance(event, IssueCommentEvent) and not is_comment_review_trigger(event):
        raise UnsupportedGitHubEventError("issue_comment event is not an allowed @coco-review trigger")

    github_client = build_github_client(event=event, github=github, github_token=github_token)
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


def main() -> int:
    """CLI entrypoint for GitHub Actions event dispatch."""
    repo_root = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    config_path = find_config(repo_root)
    config = load_config(config_path)

    reviewers_dir = repo_root / "src" / "coco_pr_review" / "agents"
    reviewers = [
        parse_agent_md(reviewers_dir / "bugs-and-security.md"),
        parse_agent_md(reviewers_dir / "tests-coverage.md"),
        parse_agent_md(reviewers_dir / "style-and-conventions.md"),
        parse_agent_md(reviewers_dir / "performance-and-cost.md"),
    ]
    verifier = parse_agent_md(reviewers_dir / "verifier.md")

    orchestrator = PythonFanoutOrchestrator(config=config)
    github = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))
    budget = BudgetGate(max_usd=config.limits.max_usd_per_pr)
    progress = NoOpProgressSink()

    conventions_path = discover_conventions(repo_root)
    conventions_text = conventions_path.read_text() if conventions_path else None

    try:
        event = parse_github_event()
        publisher = Publisher(
            github=github,
            repo_full_name=event.repo_full_name,
            pr_number=event.pr_number,
            head_sha=event.head_sha if isinstance(event, PullRequestEvent) else github.get_repo(event.repo_full_name).get_pull(event.pr_number).head.sha,
            sanitize_fn=redact,
        )
        result = __import__("asyncio").run(
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
                payload=load_event_payload(),
                event_name=os.environ.get("GITHUB_EVENT_NAME"),
                github=github,
            )
        )
    except UnsupportedGitHubEventError as exc:
        print(str(exc))
        return 1

    return 0 if isinstance(result, ReviewRunResult) else 1