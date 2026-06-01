"""Checks API: severity table rendering, annotations, and check-run publishing.

Handles the GitHub Checks API integration:
- Severity table with deterministic sort order
- JSON severity tag for machine-readable counts
- Per-finding annotations (max 50 per API call, accumulate-and-replace batching)
- Check run creation and update
"""
from __future__ import annotations

import json
from typing import Any, Callable

from github import GithubException

from coco_pr_review.severity import SEVERITIES, emoji_for, severity_rank

SEVERITY_TO_LEVEL: dict[str, str] = {
    "blocker": "failure",
    "warning": "warning",
    "nit": "notice",
}

_MAX_ANNOTATIONS_PER_CALL = 50


def _sort_key(finding: Any) -> tuple[int, str, int]:
    """Deterministic sort key: severity tier desc, file path asc, start_line asc."""
    return (
        severity_rank(finding.severity),
        finding.file,
        finding.start_line,
    )


def render_checks_output_text(findings: list[Any]) -> str:
    """Render the severity table markdown with JSON tag.

    Sorted by (severity tier desc, file path asc, start_line asc).
    Appends a machine-readable JSON comment tag with severity counts.
    """
    sorted_findings = sorted(findings, key=_sort_key)

    # Build markdown table
    lines = [
        "| Severity | File:Line | Issue |",
        "|----------|-----------|-------|",
    ]
    for f in sorted_findings:
        emoji = emoji_for(f.severity)
        lines.append(f"| {emoji} | {f.file}:{f.start_line} | {f.title} |")

    # Severity counts
    counts = {severity: 0 for severity in SEVERITIES}
    for f in findings:
        if f.severity in counts:
            counts[f.severity] += 1

    json_tag = json.dumps(counts, separators=(",", ":"))
    lines.append("")
    lines.append(f"<!-- coco-pr-review-severity: {json_tag} -->")

    return "\n".join(lines)


def findings_to_annotations(findings: list[Any]) -> list[dict[str, Any]]:
    """Convert findings to Checks API annotation dicts."""
    annotations = []
    for f in findings:
        annotations.append({
            "path": f.file,
            "start_line": f.start_line,
            "end_line": f.end_line,
            "annotation_level": SEVERITY_TO_LEVEL.get(f.severity, "notice"),
            "message": f.comment,
            "title": f.title,
        })
    return annotations


def publish_check_run(
    repo: Any,
    head_sha: str,
    findings: list[Any],
    sanitize_fn: Callable[[str], str],
) -> int:
    """Create a check run with annotations.

    Returns the check run ID, or 0 if creation fails (e.g. 403 from PAT mode).

    Uses accumulate-and-replace batching for >50 annotations:
    each edit() call sends the full cumulative annotation list.
    """
    # Render output text and sanitize
    output_text = sanitize_fn(render_checks_output_text(findings))

    # Build annotations with sanitized fields
    annotations = []
    for f in findings:
        annotations.append({
            "path": f.file,
            "start_line": f.start_line,
            "end_line": f.end_line,
            "annotation_level": SEVERITY_TO_LEVEL.get(f.severity, "notice"),
            "message": sanitize_fn(f.comment),
            "title": sanitize_fn(f.title),
        })

    # Count severities for summary
    blocker_count = sum(1 for f in findings if f.severity == "blocker")
    warning_count = sum(1 for f in findings if f.severity == "warning")
    nit_count = sum(1 for f in findings if f.severity == "nit")
    total = len(findings)

    summary = sanitize_fn(
        f"Found {total} issues ({blocker_count} blockers, {warning_count} warnings, {nit_count} nits)"
    )

    # First batch: up to 50 annotations in create_check_run
    first_batch = annotations[:_MAX_ANNOTATIONS_PER_CALL]

    try:
        check_run = repo.create_check_run(
            name="Coco PR Review",
            head_sha=head_sha,
            status="completed",
            conclusion="neutral",
            output={
                "title": "Coco PR Review Results",
                "summary": summary,
                "text": output_text,
                "annotations": first_batch,
            },
        )
    except GithubException:
        return 0

    # Remaining batches: accumulate-and-replace
    if len(annotations) > _MAX_ANNOTATIONS_PER_CALL:
        offset = _MAX_ANNOTATIONS_PER_CALL
        while offset < len(annotations):
            offset += _MAX_ANNOTATIONS_PER_CALL
            cumulative = annotations[: min(offset, len(annotations))]
            check_run.edit(
                output={
                    "title": "Coco PR Review Results",
                    "summary": summary,
                    "text": output_text,
                    "annotations": cumulative,
                },
            )

    return check_run.id
