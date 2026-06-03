"""Tests for the commit-comment publisher used by branch (no-PR) reviews."""
from __future__ import annotations

from unittest.mock import MagicMock

from coco_pr_review.github.sticky import SUMMARY_MARKER


def _finding(severity="blocker", pre_existing=False):
    f = MagicMock()
    f.severity = severity
    f.pre_existing = pre_existing
    f.title = "Null deref"
    f.file = "a.py"
    f.start_line = 5
    f.end_line = 5
    f.category = "correctness"
    f.comment = "boom"
    return f


def _run_result(*, findings, deduped_count=0):
    rr = MagicMock()
    rr.findings = findings
    rr.deduped_count = deduped_count
    rr.reviewer_failures = 0
    rr.stats = None
    return rr


def _github_with_commit():
    github = MagicMock()
    repo = MagicMock()
    commit = MagicMock()
    github.get_repo.return_value = repo
    repo.get_commit.return_value = commit
    commit.get_comments.return_value = []
    created = MagicMock()
    created.id = 123
    commit.create_comment.return_value = created
    check_run = MagicMock()
    check_run.id = 777
    repo.create_check_run.return_value = check_run
    return github, repo, commit


def _make_publisher(github):
    from coco_pr_review.github.commit_publisher import CommitPublisher

    return CommitPublisher(
        github=github,
        repo_full_name="owner/repo",
        head_sha="a" * 40,
        sanitize_fn=lambda body: body,
        bot_login="github-actions[bot]",
    )


def test_commit_publisher_posts_sticky_and_check_run_no_inline() -> None:
    github, repo, commit = _github_with_commit()
    publisher = _make_publisher(github)

    report = publisher.publish(_run_result(findings=[_finding()], deduped_count=1))

    # Sticky posted as a commit comment with the marker.
    commit.create_comment.assert_called_once()
    posted_body = commit.create_comment.call_args.args[0]
    assert SUMMARY_MARKER in posted_body

    # Check run created, keyed on the head SHA.
    repo.create_check_run.assert_called_once()
    assert repo.create_check_run.call_args.kwargs["head_sha"] == "a" * 40

    # No inline review comments on the branch path.
    repo.get_pull.assert_not_called()
    assert report.comments_posted == 0
    assert report.check_run_id == 777
    assert report.sticky_comment_id == 123


def test_commit_publisher_uses_unverified_diagnostic() -> None:
    github, repo, commit = _github_with_commit()
    publisher = _make_publisher(github)

    publisher.publish(_run_result(findings=[], deduped_count=4))

    body = commit.create_comment.call_args.args[0]
    assert "could not be verified" in body or "none could be verified" in body


def test_commit_publisher_final_summary_for_genuinely_clean_run() -> None:
    github, repo, commit = _github_with_commit()
    publisher = _make_publisher(github)

    publisher.publish(_run_result(findings=[], deduped_count=0))

    body = commit.create_comment.call_args.args[0]
    assert "Review complete" in body
    assert "could not be verified" not in body


def test_commit_publisher_reports_pat_no_checks_when_check_run_zero() -> None:
    github, repo, commit = _github_with_commit()
    publisher = _make_publisher(github)

    with __import__("unittest").mock.patch(
        "coco_pr_review.github.commit_publisher.publish_check_run", return_value=0
    ):
        report = publisher.publish(_run_result(findings=[_finding()], deduped_count=1))

    assert report.check_run_id == 0
    assert report.skipped_reason == "pat-no-checks"


def test_commit_publisher_survives_sticky_failure() -> None:
    """A failed sticky write must not abort the run or the check run."""
    github, repo, commit = _github_with_commit()
    commit.get_comments.side_effect = RuntimeError("api down")
    publisher = _make_publisher(github)

    report = publisher.publish(_run_result(findings=[_finding()], deduped_count=1))

    assert report.sticky_comment_id == 0
    assert report.check_run_id == 777
