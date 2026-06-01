"""Tests for `coco_pr_review.github.diff` — hunk parsing and diff assembly."""
from __future__ import annotations


def test_parse_hunk_ranges_single_hunk_uses_new_file_span() -> None:
    """A single hunk header yields one (start, end) range on the new file."""
    from coco_pr_review.github.diff import parse_hunk_ranges

    patch = "@@ -1,3 +1,4 @@ def foo():\n context\n-old\n+new1\n+new2\n context"

    assert parse_hunk_ranges(patch) == [(1, 4)]


def test_parse_hunk_ranges_multiple_hunks_and_omitted_length() -> None:
    """Multiple hunks each yield a range; omitted ``,d`` means a single line."""
    from coco_pr_review.github.diff import parse_hunk_ranges

    patch = (
        "@@ -1,2 +1,2 @@\n context\n-old\n+new\n"
        "@@ -40 +42 @@\n-gone\n+single\n"
        "@@ -100,5 +110,8 @@ class C:\n+a\n+b\n+c"
    )

    assert parse_hunk_ranges(patch) == [(1, 2), (42, 42), (110, 117)]


def test_parse_hunk_ranges_ignores_non_header_lines() -> None:
    """Patch content that merely looks diff-ish does not produce spurious ranges."""
    from coco_pr_review.github.diff import parse_hunk_ranges

    assert parse_hunk_ranges("+@@ not a header\n-still not") == []


def test_build_unified_diff_wraps_each_patch_with_git_headers() -> None:
    """Each changed file contributes a ``diff --git`` block plus its patch body."""
    from coco_pr_review.github.diff import build_unified_diff
    from coco_pr_review.orchestration.base import ChangedFile

    files = [
        ChangedFile(
            path="src/app.py",
            line_ranges=[(1, 2)],
            patch="@@ -1,2 +1,2 @@\n context\n-old\n+new",
        ),
        ChangedFile(
            path="src/util.py",
            line_ranges=[(5, 5)],
            patch="@@ -5 +5 @@\n-gone\n+single",
        ),
    ]

    diff = build_unified_diff(files)

    assert diff == (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -1,2 +1,2 @@\n context\n-old\n+new\n"
        "diff --git a/src/util.py b/src/util.py\n"
        "--- a/src/util.py\n"
        "+++ b/src/util.py\n"
        "@@ -5 +5 @@\n-gone\n+single\n"
    )


def test_build_unified_diff_skips_files_without_patches() -> None:
    """Binary/patchless files contribute nothing; all-empty input yields ""."""
    from coco_pr_review.github.diff import build_unified_diff
    from coco_pr_review.orchestration.base import ChangedFile

    files = [
        ChangedFile(path="logo.png", line_ranges=[(1, 1)], patch=None),
        ChangedFile(path="data.bin", line_ranges=[], patch=""),
    ]

    assert build_unified_diff(files) == ""
