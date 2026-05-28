"""Tests for `coco_pr_review.github.client` — GitHubClient construction and accessors."""
from __future__ import annotations

from unittest.mock import MagicMock


def test_github_client_construction_stores_params() -> None:
    """GitHubClient stores github, repo_full_name, pr_number, head_sha."""
    from coco_pr_review.github.client import GitHubClient

    gh = MagicMock()
    client = GitHubClient(
        github=gh,
        repo_full_name="owner/repo",
        pr_number=42,
        head_sha="a" * 40,
    )

    assert client.github is gh
    assert client.repo_full_name == "owner/repo"
    assert client.pr_number == 42
    assert client.head_sha == "a" * 40


def test_github_client_repo_accessor_calls_get_repo() -> None:
    """The repo property calls github.get_repo with the full name."""
    from coco_pr_review.github.client import GitHubClient

    gh = MagicMock()
    repo_mock = MagicMock()
    gh.get_repo.return_value = repo_mock

    client = GitHubClient(
        github=gh,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="b" * 40,
    )

    assert client.repo is repo_mock
    gh.get_repo.assert_called_once_with("owner/repo")


def test_github_client_pull_request_accessor() -> None:
    """The pull_request property calls repo.get_pull with the PR number."""
    from coco_pr_review.github.client import GitHubClient

    gh = MagicMock()
    repo_mock = MagicMock()
    pr_mock = MagicMock()
    gh.get_repo.return_value = repo_mock
    repo_mock.get_pull.return_value = pr_mock

    client = GitHubClient(
        github=gh,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="c" * 40,
    )

    assert client.pull_request is pr_mock
    repo_mock.get_pull.assert_called_once_with(7)


def test_github_client_head_commit_accessor() -> None:
    """The head_commit property calls repo.get_commit with the head SHA."""
    from coco_pr_review.github.client import GitHubClient

    gh = MagicMock()
    repo_mock = MagicMock()
    commit_mock = MagicMock()
    gh.get_repo.return_value = repo_mock
    repo_mock.get_commit.return_value = commit_mock

    client = GitHubClient(
        github=gh,
        repo_full_name="owner/repo",
        pr_number=7,
        head_sha="d" * 40,
    )

    assert client.head_commit is commit_mock
    repo_mock.get_commit.assert_called_once_with("d" * 40)


def test_github_client_pr_author_is_bot_uses_user_type() -> None:
    """Bot detection is derived from the PR author's GitHub user type."""
    from coco_pr_review.github.client import GitHubClient

    gh = MagicMock()
    repo_mock = MagicMock()
    pr_mock = MagicMock()
    pr_mock.user.type = "Bot"
    gh.get_repo.return_value = repo_mock
    repo_mock.get_pull.return_value = pr_mock

    client = GitHubClient(
        github=gh,
        repo_full_name="owner/repo",
        pr_number=9,
        head_sha="e" * 40,
    )

    assert client.pr_author_is_bot is True


def test_github_client_is_draft_pr_uses_pull_request_draft_flag() -> None:
    """Draft state is derived from the pull request draft flag."""
    from coco_pr_review.github.client import GitHubClient

    gh = MagicMock()
    repo_mock = MagicMock()
    pr_mock = MagicMock()
    pr_mock.draft = True
    gh.get_repo.return_value = repo_mock
    repo_mock.get_pull.return_value = pr_mock

    client = GitHubClient(
        github=gh,
        repo_full_name="owner/repo",
        pr_number=11,
        head_sha="1" * 40,
    )

    assert client.is_draft_pr is True


def test_github_client_label_names_materializes_pull_request_labels() -> None:
    """PR labels are exposed as a set of label names."""
    from coco_pr_review.github.client import GitHubClient

    gh = MagicMock()
    repo_mock = MagicMock()
    pr_mock = MagicMock()
    label_a = MagicMock(name="coco-review:skip")
    label_a.name = "coco-review:skip"
    label_b = MagicMock(name="needs-triage")
    label_b.name = "needs-triage"
    gh.get_repo.return_value = repo_mock
    repo_mock.get_pull.return_value = pr_mock
    pr_mock.labels = [label_a, label_b]

    client = GitHubClient(
        github=gh,
        repo_full_name="owner/repo",
        pr_number=12,
        head_sha="2" * 40,
    )

    assert client.label_names == {"coco-review:skip", "needs-triage"}


def test_github_client_changed_files_materializes_changed_file_objects() -> None:
    """Changed files are exposed as orchestrator-friendly dataclasses."""
    from coco_pr_review.github.client import GitHubClient

    gh = MagicMock()
    repo_mock = MagicMock()
    pr_mock = MagicMock()
    file_a = MagicMock(filename="src/app.py", changes=3)
    file_b = MagicMock(filename="README.md", changes=0)
    gh.get_repo.return_value = repo_mock
    repo_mock.get_pull.return_value = pr_mock
    pr_mock.get_files.return_value = [file_a, file_b]

    client = GitHubClient(
        github=gh,
        repo_full_name="owner/repo",
        pr_number=10,
        head_sha="f" * 40,
    )

    changed_files = client.changed_files

    assert [(changed_file.path, changed_file.line_ranges) for changed_file in changed_files] == [
        ("src/app.py", [(1, 3)]),
        ("README.md", []),
    ]
