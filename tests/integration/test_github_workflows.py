"""Integration-style workflow tests across GitHub review boundaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from coco_pr_review.github.fingerprints import format_fingerprint_marker, hash_finding_fingerprint


@dataclass
class FakeIssueComment:
    id: int
    body: str
    login: str
    edited_bodies: list[str] = field(default_factory=list)

    @property
    def user(self) -> SimpleNamespace:
        return SimpleNamespace(login=self.login)

    def edit(self, *, body: str) -> None:
        self.body = body
        self.edited_bodies.append(body)


@dataclass
class FakeReviewComment:
    body: str
    login: str

    @property
    def user(self) -> SimpleNamespace:
        return SimpleNamespace(login=self.login)


@dataclass
class FakeReview:
    id: int
    comments: list[FakeReviewComment]


class FakePullRequest:
    def __init__(
        self,
        *,
        issue_comments: list[FakeIssueComment] | None = None,
        review_comments: list[FakeReviewComment] | None = None,
        next_issue_comment_id: int = 100,
        next_review_id: int = 200,
        bot_login: str = "github-actions[bot]",
    ) -> None:
        self._issue_comments = list(issue_comments or [])
        self._review_comments = list(review_comments or [])
        self._single_review_comments: dict[int, list[FakeReviewComment]] = {}
        self._next_issue_comment_id = next_issue_comment_id
        self._next_review_id = next_review_id
        self._bot_login = bot_login
        self.created_issue_comments: list[FakeIssueComment] = []
        self.created_reviews: list[dict[str, object]] = []

    def get_issue_comments(self) -> list[FakeIssueComment]:
        return list(self._issue_comments)

    def get_review_comments(self) -> list[FakeReviewComment]:
        return list(self._review_comments)

    def create_issue_comment(self, *, body: str) -> FakeIssueComment:
        comment = FakeIssueComment(id=self._next_issue_comment_id, body=body, login=self._bot_login)
        self._next_issue_comment_id += 1
        self._issue_comments.append(comment)
        self.created_issue_comments.append(comment)
        return comment

    def create_review(self, *, commit: object, body: str, event: str, comments: list[dict[str, object]]) -> FakeReview:
        review_comment_objects = [
            FakeReviewComment(body=str(comment["body"]), login=self._bot_login)
            for comment in comments
        ]
        review = FakeReview(id=self._next_review_id, comments=review_comment_objects)
        self._next_review_id += 1
        self.created_reviews.append(
            {"commit": commit, "body": body, "event": event, "comments": comments, "review": review}
        )
        self._single_review_comments[review.id] = review_comment_objects
        self._review_comments.extend(review_comment_objects)
        return review

    def get_single_review_comments(self, review_id: int) -> list[FakeReviewComment]:
        return list(self._single_review_comments[review_id])

    @property
    def user(self) -> SimpleNamespace:
        return SimpleNamespace(type="User")

    @property
    def head(self) -> SimpleNamespace:
        return SimpleNamespace(sha="f" * 40)

    @property
    def draft(self) -> bool:
        return False

    @property
    def labels(self) -> list[object]:
        return []

    def get_files(self) -> list[object]:
        return []


class FakePullRequestWithDraft(FakePullRequest):
    def __init__(self, *, draft: bool, changed_files: list[object] | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._draft = draft
        self._changed_files = list(changed_files or [])

    @property
    def draft(self) -> bool:
        return self._draft

    def get_files(self) -> list[object]:
        return list(self._changed_files)


class FakeBotPullRequest(FakePullRequest):
    @property
    def user(self) -> SimpleNamespace:
        return SimpleNamespace(type="Bot")


def _make_finding(*, title: str = "Use safer API", evidence: str = "line content") -> SimpleNamespace:
    return SimpleNamespace(
        file="src/app.py",
        start_line=10,
        end_line=10,
        title=title,
        comment="This should change.",
        evidence=evidence,
        severity="warning",
        confidence=90,
        suggested_fix=None,
        verifier_reasoning=None,
    )


def _fingerprint_marker_for(finding: SimpleNamespace) -> str:
    return format_fingerprint_marker(
        hash_finding_fingerprint(
            file=finding.file,
            start_line=finding.start_line,
            end_line=finding.end_line,
            title=finding.title,
            evidence=finding.evidence,
        )
    )


@pytest.mark.asyncio
async def test_run_review_skip_path_preserves_human_marker_and_edits_bot_sticky() -> None:
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.review_runner import run_review

    human_comment = FakeIssueComment(
        id=1,
        body="Quoted summary\n<!-- coco-pr-review:summary -->",
        login="some-human-user",
    )
    bot_comment = FakeIssueComment(
        id=2,
        body="## Coco PR Review\n<!-- coco-pr-review:summary -->\n\nOld sticky",
        login="github-actions[bot]",
    )
    pr = FakeBotPullRequest(issue_comments=[human_comment, bot_comment])

    github_client = MagicMock()
    github_client.pr_author_is_bot = True
    github_client.bot_login = "github-actions[bot]"
    github_client.pull_request = pr

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
    assert human_comment.edited_bodies == []
    assert bot_comment.edited_bodies
    assert "pull request author is a bot account" in bot_comment.body
    assert pr.created_issue_comments == []


def test_publisher_rerun_only_dedupes_bot_comments_and_only_edits_bot_sticky(monkeypatch: pytest.MonkeyPatch) -> None:
    from coco_pr_review.github.publisher import Publisher

    finding = _make_finding()
    fingerprint_marker = _fingerprint_marker_for(finding)

    human_sticky = FakeIssueComment(
        id=10,
        body=f"Quoted sticky\n<!-- coco-pr-review:summary -->\n{fingerprint_marker}",
        login="some-human-user",
    )
    bot_sticky = FakeIssueComment(
        id=11,
        body="## Coco PR Review\n<!-- coco-pr-review:summary -->\n\nOld bot summary",
        login="github-actions[bot]",
    )
    human_review_comment = FakeReviewComment(
        body=f"Human quoted marker\n{fingerprint_marker}",
        login="some-human-user",
    )
    pr = FakePullRequest(
        issue_comments=[human_sticky, bot_sticky],
        review_comments=[human_review_comment],
    )

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_commit.return_value = object()
    github = MagicMock()
    github.get_repo.return_value = repo

    monkeypatch.setattr("coco_pr_review.github.publisher.attach_reactions", lambda comment: SimpleNamespace(thumbsup=True))
    monkeypatch.setattr("coco_pr_review.github.publisher.publish_check_run", lambda repo, sha, findings, sanitize_fn: 321)

    publisher = Publisher(
        github=github,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="a" * 40,
        sanitize_fn=lambda body: body,
    )

    report = publisher.publish(SimpleNamespace(findings=[finding]), phase="final")

    assert report.comments_posted == 1
    assert report.comments_skipped == 0
    assert report.sticky_comment_id == 11
    assert len(pr.created_reviews) == 1
    posted_comment = pr.created_reviews[0]["comments"][0]
    assert posted_comment["path"] == "src/app.py"
    assert fingerprint_marker in posted_comment["body"]
    assert human_sticky.edited_bodies == []
    assert bot_sticky.edited_bodies
    assert "posted 1 findings" in bot_sticky.body


def test_publisher_rerun_skips_existing_bot_fingerprint_and_still_updates_sticky(monkeypatch: pytest.MonkeyPatch) -> None:
    from coco_pr_review.github.publisher import Publisher

    finding = _make_finding()
    fingerprint_marker = _fingerprint_marker_for(finding)

    bot_sticky = FakeIssueComment(
        id=31,
        body="## Coco PR Review\n<!-- coco-pr-review:summary -->\n\nOld bot summary",
        login="github-actions[bot]",
    )
    bot_review_comment = FakeReviewComment(
        body=f"Existing bot finding\n{fingerprint_marker}",
        login="github-actions[bot]",
    )
    pr = FakePullRequest(issue_comments=[bot_sticky], review_comments=[bot_review_comment])

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_commit.return_value = object()
    github = MagicMock()
    github.get_repo.return_value = repo

    monkeypatch.setattr("coco_pr_review.github.publisher.attach_reactions", lambda comment: SimpleNamespace(thumbsup=True))
    monkeypatch.setattr("coco_pr_review.github.publisher.publish_check_run", lambda repo, sha, findings, sanitize_fn: 654)

    publisher = Publisher(
        github=github,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="a" * 40,
        sanitize_fn=lambda body: body,
    )

    report = publisher.publish(SimpleNamespace(findings=[finding]), phase="final")

    assert report.comments_posted == 0
    assert report.comments_skipped == 1
    assert report.sticky_comment_id == 31
    assert pr.created_reviews == []
    assert bot_sticky.edited_bodies
    assert "0 posted" in bot_sticky.body
    assert "1 skipped" in bot_sticky.body


def test_publisher_rerun_posts_only_new_findings_in_mixed_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    from coco_pr_review.github.publisher import Publisher

    duplicate_finding = _make_finding(title="Use safer API", evidence="line content")
    new_finding = _make_finding(title="Add timeout", evidence="different line content")
    duplicate_marker = _fingerprint_marker_for(duplicate_finding)
    new_marker = _fingerprint_marker_for(new_finding)

    bot_sticky = FakeIssueComment(
        id=41,
        body="## Coco PR Review\n<!-- coco-pr-review:summary -->\n\nOld bot summary",
        login="github-actions[bot]",
    )
    prior_bot_review_comment = FakeReviewComment(
        body=f"Existing bot finding\n{duplicate_marker}",
        login="github-actions[bot]",
    )
    pr = FakePullRequest(issue_comments=[bot_sticky], review_comments=[prior_bot_review_comment])

    repo = MagicMock()
    repo.get_pull.return_value = pr
    repo.get_commit.return_value = object()
    github = MagicMock()
    github.get_repo.return_value = repo

    monkeypatch.setattr("coco_pr_review.github.publisher.attach_reactions", lambda comment: SimpleNamespace(thumbsup=True))
    monkeypatch.setattr("coco_pr_review.github.publisher.publish_check_run", lambda repo, sha, findings, sanitize_fn: 777)

    publisher = Publisher(
        github=github,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="a" * 40,
        sanitize_fn=lambda body: body,
    )

    report = publisher.publish(SimpleNamespace(findings=[duplicate_finding, new_finding]), phase="final")

    assert report.comments_posted == 1
    assert report.comments_skipped == 1
    assert report.reactions_attached == 1
    assert report.sticky_comment_id == 41
    assert len(pr.created_reviews) == 1
    posted_comments = pr.created_reviews[0]["comments"]
    assert len(posted_comments) == 1
    assert new_marker in posted_comments[0]["body"]
    assert duplicate_marker not in posted_comments[0]["body"]
    assert "1 posted" in bot_sticky.body
    assert "1 skipped" in bot_sticky.body


@pytest.mark.asyncio
async def test_run_github_event_issue_comment_force_review_bypasses_draft_skip() -> None:
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.github_event import run_github_event

    changed_file = SimpleNamespace(filename="src/app.py", changes=3)
    pr = FakePullRequestWithDraft(draft=True, changed_files=[changed_file], issue_comments=[])

    repo = MagicMock()
    repo.get_pull.return_value = pr
    github = MagicMock()
    github.get_repo.return_value = repo

    run_result = SimpleNamespace(aborted=False, findings=[])
    publish_report = SimpleNamespace(check_run_id=1)

    orchestrator = MagicMock()
    orchestrator.run = AsyncMock(return_value=run_result)
    publisher = MagicMock()
    publisher.publish.return_value = publish_report

    payload = {
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 9, "pull_request": {"url": "https://example.test/pr/9"}},
        "comment": {"body": "please run @coco-review now", "author_association": "MEMBER"},
    }

    result = await run_github_event(
        repo_root=Path("/tmp/repo"),
        config=DEFAULT_CONFIG,
        reviewers=[MagicMock()],
        verifier=MagicMock(),
        orchestrator=orchestrator,
        publisher=publisher,
        budget=MagicMock(),
        progress=MagicMock(),
        sanitize_fn=lambda body: body,
        event_name="issue_comment",
        payload=payload,
        github=github,
    )

    assert result.status == "reviewed"
    assert result.run_result is run_result
    assert result.publish_report is publish_report
    orchestrator.run.assert_awaited_once()
    publisher.publish.assert_called_once_with(run_result)
    assert pr.created_issue_comments == []


@pytest.mark.asyncio
async def test_run_github_event_skip_path_preserves_human_marker_and_edits_bot_sticky() -> None:
    from coco_pr_review.config import DEFAULT_CONFIG
    from coco_pr_review.github_event import run_github_event

    human_comment = FakeIssueComment(
        id=21,
        body="Quoted summary\n<!-- coco-pr-review:summary -->",
        login="some-human-user",
    )
    bot_comment = FakeIssueComment(
        id=22,
        body="## Coco PR Review\n<!-- coco-pr-review:summary -->\n\nOld sticky",
        login="github-actions[bot]",
    )
    pr = FakeBotPullRequest(issue_comments=[human_comment, bot_comment])

    repo = MagicMock()
    repo.get_pull.return_value = pr
    github = MagicMock()
    github.get_repo.return_value = repo

    payload = {
        "repository": {"full_name": "owner/repo"},
        "pull_request": {"number": 7, "head": {"sha": "b" * 40}},
    }

    result = await run_github_event(
        repo_root=Path("/tmp/repo"),
        config=DEFAULT_CONFIG,
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

    assert result.status == "skipped_bot_pr"
    assert human_comment.edited_bodies == []
    assert bot_comment.edited_bodies
    assert "pull request author is a bot account" in bot_comment.body
    assert pr.created_issue_comments == []