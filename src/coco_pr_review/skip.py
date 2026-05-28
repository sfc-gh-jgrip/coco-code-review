"""Config-driven PR skip helpers."""
from __future__ import annotations

from pathlib import PurePosixPath

from coco_pr_review.orchestration.base import ChangedFile


def filter_changed_files(changed_files: list[ChangedFile], paths_ignore: list[str]) -> list[ChangedFile]:
    if not paths_ignore:
        return changed_files
    return [changed_file for changed_file in changed_files if not _matches_any(changed_file.path, paths_ignore)]


def exceeds_diff_size(changed_files: list[ChangedFile], max_diff_lines: int) -> bool:
    total_changed_lines = sum(
        end_line - start_line + 1
        for changed_file in changed_files
        for start_line, end_line in changed_file.line_ranges
    )
    return total_changed_lines > max_diff_lines


def should_skip_bot_pr(*, pr_author_is_bot: bool, review_bot_prs: bool) -> bool:
    return pr_author_is_bot and not review_bot_prs


def _matches_any(path: str, patterns: list[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(_matches_pattern(candidate, pattern) for pattern in patterns)


def _matches_pattern(path: PurePosixPath, pattern: str) -> bool:
    if path.match(pattern):
        return True

    if pattern.startswith("**/"):
        return path.match(pattern[3:])

    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == PurePosixPath(prefix) or PurePosixPath(prefix) in path.parents

    return False