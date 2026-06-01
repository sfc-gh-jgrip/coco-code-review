"""Pure helpers for turning PR file patches into line ranges and a unified diff.

GitHub's per-file ``patch`` is a standard unified-diff body (the hunks for one
file, without the ``diff --git`` header). These helpers parse hunk headers into
new-file line ranges and reassemble a full unified diff string for the model.
"""
from __future__ import annotations

import re

from coco_pr_review.orchestration.base import ChangedFile

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_hunk_ranges(patch: str) -> list[tuple[int, int]]:
    """Parse a unified-diff patch into 1-indexed new-file line ranges.

    Each ``@@ -a,b +c,d @@`` header maps to ``(c, c + d - 1)``; a missing ``,d``
    defaults to a single-line span.
    """
    ranges: list[tuple[int, int]] = []
    for line in patch.splitlines():
        match = _HUNK_HEADER.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        length = int(match.group(2)) if match.group(2) is not None else 1
        end = start + length - 1 if length > 0 else start
        ranges.append((start, end))
    return ranges


def build_unified_diff(changed_files: list[ChangedFile]) -> str:
    """Reassemble a unified diff from each file's patch body.

    Files without a patch (e.g. binary blobs) are skipped. Returns ``""`` when
    no file carries a patch.
    """
    blocks: list[str] = []
    for changed_file in changed_files:
        patch = changed_file.patch
        if not patch:
            continue
        path = changed_file.path
        body = patch if patch.endswith("\n") else patch + "\n"
        blocks.append(
            f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
            f"{body}"
        )
    return "".join(blocks)
