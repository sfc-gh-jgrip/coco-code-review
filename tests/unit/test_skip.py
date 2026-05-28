"""Tests for `coco_pr_review.skip`."""
from __future__ import annotations

import inspect

from coco_pr_review.orchestration.base import ChangedFile
from coco_pr_review.skip import exceeds_diff_size, filter_changed_files, should_skip_bot_pr


def test_filter_changed_files_returns_original_files_when_no_patterns() -> None:
    files = [
        ChangedFile(path="src/app.py", line_ranges=[(1, 2)]),
        ChangedFile(path="vendor/pkg/lib.py", line_ranges=[(3, 4)]),
    ]

    assert filter_changed_files(files, []) == files


def test_filter_changed_files_excludes_literal_path_match() -> None:
    files = [
        ChangedFile(path="src/app.py", line_ranges=[(1, 2)]),
        ChangedFile(path="poetry.lock", line_ranges=[(1, 1)]),
    ]

    filtered = filter_changed_files(files, ["poetry.lock"])

    assert filtered == [files[0]]


def test_filter_changed_files_excludes_lockfiles_recursively() -> None:
    files = [
        ChangedFile(path="frontend/package-lock.lock", line_ranges=[(1, 10)]),
        ChangedFile(path="services/api/poetry.lock", line_ranges=[(2, 3)]),
        ChangedFile(path="src/main.py", line_ranges=[(4, 5)]),
    ]

    filtered = filter_changed_files(files, ["**/*.lock"])

    assert filtered == [files[2]]


def test_filter_changed_files_excludes_vendor_tree_recursively() -> None:
    files = [
        ChangedFile(path="vendor/root.py", line_ranges=[(1, 1)]),
        ChangedFile(path="vendor/sub/dir/foo.py", line_ranges=[(2, 2)]),
        ChangedFile(path="src/vendor_helper.py", line_ranges=[(3, 3)]),
    ]

    filtered = filter_changed_files(files, ["vendor/**"])

    assert filtered == [files[2]]


def test_filter_changed_files_supports_multiple_patterns() -> None:
    files = [
        ChangedFile(path="vendor/sub/dir/foo.py", line_ranges=[(1, 1)]),
        ChangedFile(path="src/generated.py", line_ranges=[(2, 2)]),
        ChangedFile(path="src/app.py", line_ranges=[(3, 3)]),
    ]

    filtered = filter_changed_files(files, ["vendor/**", "src/generated.py"])

    assert filtered == [files[2]]


def test_filter_changed_files_preserves_non_matching_files_in_order() -> None:
    files = [
        ChangedFile(path="src/first.py", line_ranges=[(1, 1)]),
        ChangedFile(path="docs/readme.md", line_ranges=[(2, 2)]),
    ]

    assert filter_changed_files(files, ["vendor/**", "**/*.lock"]) == files


def test_exceeds_diff_size_is_false_for_empty_file_list() -> None:
    assert exceeds_diff_size([], max_diff_lines=1) is False


def test_exceeds_diff_size_is_false_when_under_cap() -> None:
    files = [ChangedFile(path="src/app.py", line_ranges=[(1, 2), (10, 10)])]

    assert exceeds_diff_size(files, max_diff_lines=4) is False


def test_exceeds_diff_size_is_false_when_exactly_at_cap() -> None:
    files = [ChangedFile(path="src/app.py", line_ranges=[(1, 3)])]

    assert exceeds_diff_size(files, max_diff_lines=3) is False


def test_exceeds_diff_size_is_true_when_over_cap() -> None:
    files = [ChangedFile(path="src/app.py", line_ranges=[(1, 4)])]

    assert exceeds_diff_size(files, max_diff_lines=3) is True


def test_exceeds_diff_size_counts_multiple_files_and_ranges() -> None:
    files = [
        ChangedFile(path="src/app.py", line_ranges=[(1, 2), (10, 12)]),
        ChangedFile(path="src/other.py", line_ranges=[(20, 21)]),
    ]

    assert exceeds_diff_size(files, max_diff_lines=6) is True


def test_should_skip_bot_pr_covers_truth_table() -> None:
    assert should_skip_bot_pr(pr_author_is_bot=False, review_bot_prs=False) is False
    assert should_skip_bot_pr(pr_author_is_bot=False, review_bot_prs=True) is False
    assert should_skip_bot_pr(pr_author_is_bot=True, review_bot_prs=False) is True
    assert should_skip_bot_pr(pr_author_is_bot=True, review_bot_prs=True) is False


def test_skip_helper_signatures_are_stable() -> None:
    assert str(inspect.signature(filter_changed_files)) == "(changed_files: 'list[ChangedFile]', paths_ignore: 'list[str]') -> 'list[ChangedFile]'"
    assert str(inspect.signature(exceeds_diff_size)) == "(changed_files: 'list[ChangedFile]', max_diff_lines: 'int') -> 'bool'"
    assert str(inspect.signature(should_skip_bot_pr)) == "(*, pr_author_is_bot: 'bool', review_bot_prs: 'bool') -> 'bool'"