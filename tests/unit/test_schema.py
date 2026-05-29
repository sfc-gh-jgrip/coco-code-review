"""Tests for `coco_pr_review.schema` — JSON Schema dicts for findings + verifier output."""
from __future__ import annotations

import jsonschema
import pytest


VALID_FINDING = {
    "file": "src/foo.py",
    "start_line": 42,
    "end_line": 44,
    "severity": "blocker",
    "category": "correctness",
    "title": "Division by zero",
    "evidence": "    return a / b",
    "comment": "Will raise ZeroDivisionError when b is 0.",
}


def test_finding_schema_accepts_a_complete_valid_finding() -> None:
    """Tracer bullet: a complete finding matching §6 of the spec validates cleanly."""
    from coco_pr_review.schema import FINDING_SCHEMA

    # validate raises on failure; success is silent
    jsonschema.validate(instance=VALID_FINDING, schema=FINDING_SCHEMA)


def test_finding_schema_rejects_finding_missing_required_field() -> None:
    """A finding without `evidence` is malformed — verifier can't validate without it."""
    from coco_pr_review.schema import FINDING_SCHEMA

    no_evidence = {k: v for k, v in VALID_FINDING.items() if k != "evidence"}

    with pytest.raises(jsonschema.ValidationError, match="evidence"):
        jsonschema.validate(instance=no_evidence, schema=FINDING_SCHEMA)


def test_finding_schema_rejects_invalid_severity() -> None:
    """Severity is enum-restricted to blocker | warning | nit."""
    from coco_pr_review.schema import FINDING_SCHEMA

    bogus = {**VALID_FINDING, "severity": "super_critical"}

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bogus, schema=FINDING_SCHEMA)


def test_finding_schema_rejects_non_integer_line_numbers() -> None:
    """Line numbers must be integers; SDK output_format will enforce this on the model."""
    from coco_pr_review.schema import FINDING_SCHEMA

    bogus = {**VALID_FINDING, "start_line": "42"}  # string, not int

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bogus, schema=FINDING_SCHEMA)


def test_finding_schema_allows_optional_suggested_fix() -> None:
    """`suggested_fix` is optional; presence shouldn't cause rejection."""
    from coco_pr_review.schema import FINDING_SCHEMA

    with_fix = {**VALID_FINDING, "suggested_fix": "    if b == 0: return None\n    return a / b"}

    jsonschema.validate(instance=with_fix, schema=FINDING_SCHEMA)


def test_finding_schema_allows_verifier_appended_fields() -> None:
    """The verifier appends `confidence` and `verifier_reasoning` after dispatch."""
    from coco_pr_review.schema import FINDING_SCHEMA

    verified = {
        **VALID_FINDING,
        "confidence": 87,
        "verifier_reasoning": "Lines match; division by zero is real.",
    }

    jsonschema.validate(instance=verified, schema=FINDING_SCHEMA)


def test_verifier_output_schema_accepts_complete_verification() -> None:
    """The verifier subagent's output schema accepts a well-formed verification."""
    from coco_pr_review.schema import VERIFIER_OUTPUT_SCHEMA

    verification = {
        "confidence": 87,
        "evidence_matches": True,
        "lines_in_pr": True,
        "verifier_reasoning": "Lines 1-2 match the evidence; the defect is real.",
    }

    jsonschema.validate(instance=verification, schema=VERIFIER_OUTPUT_SCHEMA)


def test_verifier_output_schema_rejects_confidence_above_100() -> None:
    """Confidence must be 0..100 inclusive."""
    from coco_pr_review.schema import VERIFIER_OUTPUT_SCHEMA

    bogus = {
        "confidence": 150,
        "evidence_matches": True,
        "lines_in_pr": True,
        "verifier_reasoning": "ok",
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bogus, schema=VERIFIER_OUTPUT_SCHEMA)


def test_verifier_output_schema_rejects_missing_evidence_matches() -> None:
    """`evidence_matches` is mandatory — it's the hallucination check."""
    from coco_pr_review.schema import VERIFIER_OUTPUT_SCHEMA

    bogus = {
        "confidence": 80,
        "lines_in_pr": True,
        "verifier_reasoning": "ok",
    }

    with pytest.raises(jsonschema.ValidationError, match="evidence_matches"):
        jsonschema.validate(instance=bogus, schema=VERIFIER_OUTPUT_SCHEMA)


def test_schemas_omit_dollar_schema_for_cli_compatibility() -> None:
    """No schema may carry a ``$schema`` meta-ref.

    The Cortex CLI's structured-output validator fails to resolve a ``$schema``
    URI (``no schema with key or ref "..."``), which silently dropped every
    verifier result in production. Guard against reintroducing it anywhere,
    including nested subschemas.
    """
    from coco_pr_review.schema import (
        FINDING_SCHEMA,
        REVIEWER_OUTPUT_SCHEMA,
        VERIFIER_OUTPUT_SCHEMA,
    )

    def _assert_no_dollar_schema(node: object) -> None:
        if isinstance(node, dict):
            assert "$schema" not in node, f"$schema must not appear in CLI schema: {node!r}"
            for value in node.values():
                _assert_no_dollar_schema(value)
        elif isinstance(node, list):
            for item in node:
                _assert_no_dollar_schema(item)

    for schema in (FINDING_SCHEMA, REVIEWER_OUTPUT_SCHEMA, VERIFIER_OUTPUT_SCHEMA):
        _assert_no_dollar_schema(schema)
