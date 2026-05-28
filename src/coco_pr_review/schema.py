"""JSON Schema dicts for review findings and verifier outputs.

Used as `output_format={"type": "json_schema", "schema": FINDING_SCHEMA}` when
dispatching reviewer and verifier subagents via the SDK.
"""
from __future__ import annotations

from typing import Any

SEVERITIES = ("blocker", "warning", "nit")
CATEGORIES = ("correctness", "security", "perf", "style", "test")


FINDING_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "file",
        "start_line",
        "end_line",
        "severity",
        "category",
        "title",
        "evidence",
        "comment",
    ],
    "properties": {
        "file": {"type": "string", "minLength": 1},
        "start_line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
        "severity": {"enum": list(SEVERITIES)},
        "category": {"enum": list(CATEGORIES)},
        "title": {"type": "string", "minLength": 1},
        "evidence": {"type": "string", "minLength": 1},
        "comment": {"type": "string", "minLength": 1},
        "suggested_fix": {"type": "string"},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "verifier_reasoning": {"type": "string"},
    },
}


REVIEWER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "maxItems": 20,
            "items": FINDING_SCHEMA,
        }
    },
}


VERIFIER_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["confidence", "evidence_matches", "lines_in_pr", "verifier_reasoning"],
    "properties": {
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "evidence_matches": {"type": "boolean"},
        "lines_in_pr": {"type": "boolean"},
        "verifier_reasoning": {"type": "string"},
    },
}
