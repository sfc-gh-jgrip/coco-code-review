"""Tests for `coco_pr_review.github.publisher` — top-level Publisher class."""
from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock


def _make_finding(
    *,
    file: str = "src/foo.py",
    start_line: int = 10,
    end_line: int = 12,
    severity: str = "blocker",
    title: str = "Bug found",
    comment: str = "Fix this bug",
    evidence: str = "x = 1 / 0",
    confidence: int = 92,
    verifier_reasoning: str | None = "Verified via trace analysis",
    suggested_fix: str | None = None,
    category: str = "correctness",
) -> MagicMock:
    """Create a mock finding with the standard shape."""
    f = MagicMock()
    f.file = file
    f.start_line = start_line
    f.end_line = end_line
    f.severity = severity
    f.title = title
    f.comment = comment
    f.evidence = evidence
    f.confidence = confidence
    f.verifier_reasoning = verifier_reasoning
    f.suggested_fix = suggested_fix
    f.category = category
    return f


def _make_run_result(findings: list) -> MagicMock:
    """Create a mock RunResult."""
    rr = MagicMock()
    rr.findings = findings
    return rr


def _make_publisher_deps(
    existing_comments: list | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Create mocked github, repo, pr objects for Publisher.

    Returns (github_mock, repo_mock, pr_mock).
    """
    github_mock = MagicMock()
    repo_mock = MagicMock()
    pr_mock = MagicMock()

    github_mock.get_repo.return_value = repo_mock
    repo_mock.get_pull.return_value = pr_mock

    # Existing review comments (for fingerprint dedup)
    if existing_comments is None:
        existing_comments = []
    pr_mock.get_review_comments.return_value = existing_comments
    pr_mock.get_issue_comments.return_value = []

    # create_review returns a review mock with an id
    review_mock = MagicMock()
    review_mock.id = 42
    pr_mock.create_review.return_value = review_mock

    # get_single_review_comments returns empty by default
    pr_mock.get_single_review_comments.return_value = []

    # create_issue_comment returns a comment with an id
    sticky_mock = MagicMock()
    sticky_mock.id = 100
    pr_mock.create_issue_comment.return_value = sticky_mock

    # create_check_run returns a check run with an id
    check_run_mock = MagicMock()
    check_run_mock.id = 200
    repo_mock.create_check_run.return_value = check_run_mock
    repo_mock.get_commit.return_value = MagicMock()

    return github_mock, repo_mock, pr_mock


# ---------------------------------------------------------------------------
# Happy path — 3 new findings
# ---------------------------------------------------------------------------


def test_publish_happy_path_posts_all_new_findings() -> None:
    """3 findings with no prior comments → 3 inline comments via create_review, sticky, check run."""
    from coco_pr_review.github.publisher import Publisher, PublishReport

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="a" * 40,
        sanitize_fn=sanitize_fn,
    )

    findings = [
        _make_finding(file="src/a.py", start_line=1, end_line=2, title="Bug A", evidence="a"),
        _make_finding(file="src/b.py", start_line=5, end_line=6, title="Bug B", evidence="b"),
        _make_finding(file="src/c.py", start_line=10, end_line=11, title="Bug C", evidence="c"),
    ]
    run_result = _make_run_result(findings)

    report = publisher.publish(run_result, phase="final")

    assert isinstance(report, PublishReport)
    # All 3 posted as new inline comments
    assert report.comments_posted == 3
    assert report.comments_skipped == 0
    # Sticky was created/updated
    assert report.sticky_comment_id != 0
    # Check run was created
    assert report.check_run_id != 0


# ---------------------------------------------------------------------------
# Re-run with same fingerprints — deduplication
# ---------------------------------------------------------------------------


def test_publish_skips_findings_with_existing_fingerprints() -> None:
    """Re-run where all fingerprints already exist → 0 new inline comments posted."""
    from coco_pr_review.github.publisher import Publisher, PublishReport
    from coco_pr_review.github.fingerprints import hash_finding_fingerprint, format_fingerprint_marker

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    # Pre-populate existing comments with fingerprints
    finding_data = dict(file="src/a.py", start_line=1, end_line=2, title="Bug A", evidence="a")
    fp_hex = hash_finding_fingerprint(**finding_data)
    existing_comment = MagicMock()
    existing_comment.body = f"Some review text\n{format_fingerprint_marker(fp_hex)}"
    pr_mock.get_review_comments.return_value = [existing_comment]

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="a" * 40,
        sanitize_fn=sanitize_fn,
    )

    findings = [_make_finding(**finding_data)]
    run_result = _make_run_result(findings)

    report = publisher.publish(run_result, phase="final")

    assert report.comments_posted == 0
    assert report.comments_skipped == 1
    # Sticky and check run still updated
    assert report.sticky_comment_id != 0
    assert report.check_run_id != 0


# ---------------------------------------------------------------------------
# Mix of new + existing fingerprints
# ---------------------------------------------------------------------------


def test_publish_posts_only_new_findings_when_mixed() -> None:
    """With a mix of new and existing fingerprints, only new ones get posted."""
    from coco_pr_review.github.publisher import Publisher, PublishReport
    from coco_pr_review.github.fingerprints import hash_finding_fingerprint, format_fingerprint_marker

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    # One existing fingerprint
    existing_data = dict(file="src/old.py", start_line=1, end_line=2, title="Old Bug", evidence="old")
    existing_fp = hash_finding_fingerprint(**existing_data)
    existing_comment = MagicMock()
    existing_comment.body = f"text\n{format_fingerprint_marker(existing_fp)}"
    pr_mock.get_review_comments.return_value = [existing_comment]

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="b" * 40,
        sanitize_fn=sanitize_fn,
    )

    findings = [
        _make_finding(**existing_data),  # already posted
        _make_finding(file="src/new.py", start_line=5, end_line=6, title="New Bug", evidence="new"),
    ]
    run_result = _make_run_result(findings)

    report = publisher.publish(run_result, phase="final")

    assert report.comments_posted == 1
    assert report.comments_skipped == 1


# ---------------------------------------------------------------------------
# Fork-PR no-write path — 403 from create_review
# ---------------------------------------------------------------------------


def test_publish_handles_fork_pr_permission_error_gracefully() -> None:
    """When create_review raises a 403/permission error, Publisher does not raise."""
    from coco_pr_review.github.publisher import Publisher, PublishReport
    from github import GithubException

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    # Simulate 403 on create_review
    pr_mock.create_review.side_effect = GithubException(
        status=403, data={"message": "Resource not accessible by integration"}, headers={}
    )

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="c" * 40,
        sanitize_fn=sanitize_fn,
    )

    findings = [_make_finding()]
    run_result = _make_run_result(findings)

    # Should NOT raise
    report = publisher.publish(run_result, phase="final")

    assert report.comments_posted == 0
    assert report.skipped_reason is not None


# ---------------------------------------------------------------------------
# Fork-PR — check run creation also fails gracefully
# ---------------------------------------------------------------------------


def test_publish_handles_check_run_permission_error_gracefully() -> None:
    """When both create_review and create_check_run raise 403, Publisher still returns."""
    from coco_pr_review.github.publisher import Publisher, PublishReport
    from github import GithubException

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    pr_mock.create_review.side_effect = GithubException(
        status=403, data={"message": "Forbidden"}, headers={}
    )
    repo_mock.create_check_run.side_effect = GithubException(
        status=403, data={"message": "Forbidden"}, headers={}
    )

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="d" * 40,
        sanitize_fn=sanitize_fn,
    )

    findings = [_make_finding()]
    run_result = _make_run_result(findings)

    report = publisher.publish(run_result, phase="final")

    assert report.comments_posted == 0
    assert report.check_run_id == 0
    assert report.skipped_reason is not None


# ---------------------------------------------------------------------------
# sanitize_fn is called on every outbound body
# ---------------------------------------------------------------------------


def test_publish_calls_sanitize_fn_on_all_outbound_text() -> None:
    """sanitize_fn is invoked on inline comment bodies, sticky body, and check run output text."""
    from coco_pr_review.github.publisher import Publisher, PublishReport

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="e" * 40,
        sanitize_fn=sanitize_fn,
    )

    findings = [_make_finding(), _make_finding(file="src/b.py", evidence="b", title="Bug B")]
    run_result = _make_run_result(findings)

    publisher.publish(run_result, phase="final")

    # sanitize_fn should be called multiple times:
    # - once per inline comment body (2)
    # - once for sticky body
    # - at least once for check run output text
    assert sanitize_fn.call_count >= 4


# ---------------------------------------------------------------------------
# PublishReport shape
# ---------------------------------------------------------------------------


def test_publish_report_has_expected_fields() -> None:
    """PublishReport has: comments_posted, comments_skipped, sticky_comment_id, check_run_id, reactions_attached, reactions_failed, skipped_reason."""
    from coco_pr_review.github.publisher import PublishReport

    report = PublishReport(
        comments_posted=3,
        comments_skipped=1,
        sticky_comment_id=100,
        check_run_id=200,
        reactions_attached=3,
        reactions_failed=0,
        skipped_reason=None,
    )

    assert report.comments_posted == 3
    assert report.comments_skipped == 1
    assert report.sticky_comment_id == 100
    assert report.check_run_id == 200
    assert report.reactions_attached == 3
    assert report.reactions_failed == 0
    assert report.skipped_reason is None


# ---------------------------------------------------------------------------
# Severity table ordering in checks output is deterministic
# ---------------------------------------------------------------------------


def test_publish_check_run_severity_ordering_is_deterministic() -> None:
    """The severity table in checks output maintains a consistent order across runs."""
    from coco_pr_review.github.checks import render_checks_output_text

    findings = [
        _make_finding(severity="nit", title="Nit 1"),
        _make_finding(severity="blocker", title="Blocker 1"),
        _make_finding(severity="warning", title="Warning 1"),
        _make_finding(severity="blocker", title="Blocker 2"),
    ]

    text_a = render_checks_output_text(findings)
    text_b = render_checks_output_text(findings)

    assert text_a == text_b


# ---------------------------------------------------------------------------
# Empty findings — no inline comments, sticky shows 0-finding state, check run still posts
# ---------------------------------------------------------------------------


def test_publish_no_findings() -> None:
    """Empty findings → no inline comments, sticky shows 0-finding state, check run still posts."""
    from coco_pr_review.github.publisher import Publisher, PublishReport

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="f" * 40,
        sanitize_fn=sanitize_fn,
    )

    run_result = _make_run_result([])

    report = publisher.publish(run_result, phase="final")

    assert report.comments_posted == 0
    assert report.comments_skipped == 0
    # Sticky was still created
    assert report.sticky_comment_id != 0
    # Check run was still created
    assert report.check_run_id != 0
    # No inline review was created (create_review not called)
    pr_mock.create_review.assert_not_called()


# ---------------------------------------------------------------------------
# Sticky/find-by-marker: non-bot user comment carrying the marker is ignored
# ---------------------------------------------------------------------------


def test_publish_ignores_non_bot_fingerprint_markers() -> None:
    """A non-bot user comment with a fingerprint marker is not treated as a prior finding."""
    from coco_pr_review.github.publisher import Publisher, PublishReport
    from coco_pr_review.github.fingerprints import hash_finding_fingerprint, format_fingerprint_marker

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    # A user (non-bot) comment that happens to contain a fingerprint marker
    finding_data = dict(file="src/a.py", start_line=1, end_line=2, title="Bug A", evidence="a")
    fp_hex = hash_finding_fingerprint(**finding_data)
    user_comment = MagicMock()
    user_comment.body = f"Quoting bot:\n{format_fingerprint_marker(fp_hex)}"
    user_comment.user.login = "some-human-user"
    pr_mock.get_review_comments.return_value = [user_comment]

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="a" * 40,
        sanitize_fn=sanitize_fn,
        bot_login="github-actions[bot]",
    )

    findings = [_make_finding(**finding_data)]
    run_result = _make_run_result(findings)

    report = publisher.publish(run_result, phase="final")

    # The finding should be posted as new (not skipped) since the marker was from a non-bot user
    assert report.comments_posted == 1
    assert report.comments_skipped == 0


# ---------------------------------------------------------------------------
# Reaction failure does not affect comments_posted count
# ---------------------------------------------------------------------------


def test_reactions_failure_does_not_affect_comments_posted() -> None:
    """Reaction subsystem raising must leave comments_posted correct."""
    from coco_pr_review.github.publisher import Publisher, PublishReport

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    # Make reaction attachment raise (get_single_review_comments raises)
    pr_mock.get_single_review_comments.side_effect = RuntimeError("API down")

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="a" * 40,
        sanitize_fn=sanitize_fn,
    )

    findings = [
        _make_finding(file="src/a.py", start_line=1, end_line=2, title="Bug A", evidence="a"),
        _make_finding(file="src/b.py", start_line=5, end_line=6, title="Bug B", evidence="b"),
    ]
    run_result = _make_run_result(findings)

    report = publisher.publish(run_result, phase="final")

    # Comments were still posted successfully before reactions failed
    assert report.comments_posted == 2
    # Reactions all failed
    assert report.reactions_failed == 2
    assert report.reactions_attached == 0


# ---------------------------------------------------------------------------
# get_single_review_comments failure does not break comment posting
# ---------------------------------------------------------------------------


def test_get_single_review_comments_failure() -> None:
    """The API call raising during reaction harvest must not break comment posting."""
    from coco_pr_review.github.publisher import Publisher, PublishReport

    github_mock, repo_mock, pr_mock = _make_publisher_deps()
    sanitize_fn = MagicMock(side_effect=lambda x: x)

    # Simulate an Exception on get_single_review_comments
    pr_mock.get_single_review_comments.side_effect = Exception("Network error")

    publisher = Publisher(
        github=github_mock,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="a" * 40,
        sanitize_fn=sanitize_fn,
    )

    findings = [_make_finding()]
    run_result = _make_run_result(findings)

    report = publisher.publish(run_result, phase="final")

    # Comments posted successfully (reactions are best-effort)
    assert report.comments_posted == 1
    # Sticky and check run still work
    assert report.sticky_comment_id != 0
    assert report.check_run_id != 0
