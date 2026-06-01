"""Tests for `coco_pr_review.orchestration.base` — dataclasses, ABC, budget gate, progress sink.

These pin the orchestrator's public contract before implementation exists.
The tests MUST fail with ImportError until `orchestration/base.py` is written.
"""
from __future__ import annotations

import asyncio
import dataclasses

import jsonschema
import pytest


# ---------------------------------------------------------------------------
# BudgetGate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_gate_should_abort_flips_when_cost_exceeds_max() -> None:
    """BudgetGate.should_abort() becomes True once accumulated cost >= max_usd."""
    from coco_pr_review.orchestration.base import BudgetGate

    gate = BudgetGate(max_usd=1.00)
    assert gate.should_abort() is False

    await gate.register_cost(0.60)
    assert gate.should_abort() is False

    await gate.register_cost(0.50)  # total now 1.10
    assert gate.should_abort() is True


@pytest.mark.asyncio
async def test_budget_gate_should_abort_flips_when_turns_exceed_max() -> None:
    """BudgetGate can also abort based on turn count."""
    from coco_pr_review.orchestration.base import BudgetGate

    gate = BudgetGate(max_usd=100.0, max_turns=5)
    assert gate.should_abort() is False

    await gate.register_turns(3)
    assert gate.should_abort() is False

    await gate.register_turns(3)  # total now 6
    assert gate.should_abort() is True


@pytest.mark.asyncio
async def test_budget_gate_is_async_safe_under_concurrent_updates() -> None:
    """Concurrent register_cost calls must produce the correct total (no lost updates)."""
    from coco_pr_review.orchestration.base import BudgetGate

    gate = BudgetGate(max_usd=100.0)

    # 100 concurrent calls of 0.01 each → total should be exactly 1.00
    await asyncio.gather(*(gate.register_cost(0.01) for _ in range(100)))

    # Since max is 100.0, we shouldn't abort, but the total should be correct.
    # We can't easily read the internal total — but we can verify it didn't abort
    # spuriously. More importantly, let's push it past the limit.
    await gate.register_cost(99.01)  # total now 100.01
    assert gate.should_abort() is True


# ---------------------------------------------------------------------------
# ProgressSink
# ---------------------------------------------------------------------------


def test_progress_sink_noop_does_not_raise() -> None:
    """The no-op default implementation accepts calls without raising."""
    from coco_pr_review.orchestration.base import Finding, NoOpProgressSink

    sink = NoOpProgressSink()
    sink.phase_started("reviewer_fanout")
    sink.phase_completed("reviewer_fanout", "4 findings")
    sink.finding_emitted(
        Finding(
            file="src/foo.py",
            start_line=1,
            end_line=2,
            severity="warning",
            category="correctness",
            title="test",
            evidence="x = 1",
            comment="test finding",
        )
    )


def test_recording_progress_sink_stores_events_in_order() -> None:
    """RecordingProgressSink captures phase events for assertions in tests."""
    from coco_pr_review.orchestration.base import Finding, RecordingProgressSink

    sink = RecordingProgressSink()
    sink.phase_started("reviewer_fanout")
    sink.phase_completed("reviewer_fanout", "done")
    sink.phase_started("verifier_fanout")
    sink.finding_emitted(
        Finding(
            file="a.py",
            start_line=10,
            end_line=12,
            severity="blocker",
            category="security",
            title="XSS",
            evidence="<script>",
            comment="unsanitized",
        )
    )
    sink.phase_completed("verifier_fanout", "1 verified")

    assert sink.events[0] == ("phase_started", "reviewer_fanout")
    assert sink.events[1] == ("phase_completed", "reviewer_fanout", "done")
    assert sink.events[2] == ("phase_started", "verifier_fanout")
    assert sink.events[3][0] == "finding_emitted"
    assert sink.events[4] == ("phase_completed", "verifier_fanout", "1 verified")


# ---------------------------------------------------------------------------
# Orchestrator ABC
# ---------------------------------------------------------------------------


def test_orchestrator_abc_cannot_be_instantiated_without_run() -> None:
    """Attempting to instantiate the ABC directly raises TypeError."""
    from coco_pr_review.orchestration.base import Orchestrator

    with pytest.raises(TypeError):
        Orchestrator()  # type: ignore[abstract]


def test_orchestrator_abc_can_be_subclassed_with_run() -> None:
    """A concrete subclass that implements run() can be instantiated."""
    from coco_pr_review.orchestration.base import Orchestrator, RunResult

    class FakeOrchestrator(Orchestrator):
        async def run(self, pr_context, reviewers, verifier, budget, progress) -> RunResult:
            return RunResult(
                findings=[],
                candidate_count=0,
                deduped_count=0,
                total_cost_usd=0.0,
                total_turns=0,
                aborted=False,
                abort_reason=None,
            )

    orch = FakeOrchestrator()
    assert orch is not None


# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------


def test_finding_round_trips_through_asdict() -> None:
    """Finding → dataclasses.asdict → dict is lossless."""
    from coco_pr_review.orchestration.base import Finding

    f = Finding(
        file="src/foo.py",
        start_line=42,
        end_line=44,
        severity="blocker",
        category="correctness",
        title="Division by zero",
        evidence="    return a / b",
        comment="Will raise ZeroDivisionError when b is 0.",
        suggested_fix="    if b == 0: return None\n    return a / b",
        confidence=87,
        verifier_reasoning="Lines match; division by zero is real.",
    )

    d = dataclasses.asdict(f)
    assert d["file"] == "src/foo.py"
    assert d["start_line"] == 42
    assert d["confidence"] == 87
    assert d["suggested_fix"] is not None


def test_finding_asdict_validates_against_finding_schema() -> None:
    """A well-formed Finding, when serialised to dict, passes FINDING_SCHEMA validation."""
    from coco_pr_review.orchestration.base import Finding
    from coco_pr_review.schema import FINDING_SCHEMA

    f = Finding(
        file="src/foo.py",
        start_line=42,
        end_line=44,
        severity="blocker",
        category="correctness",
        title="Division by zero",
        evidence="    return a / b",
        comment="Will raise ZeroDivisionError when b is 0.",
    )

    d = dataclasses.asdict(f)
    # Remove None values and internal verifier-derived fields (confidence,
    # verifier_reasoning, pre_existing) that are not part of the reviewer-emitted
    # FINDING_SCHEMA shape.
    _internal = {"confidence", "verifier_reasoning", "pre_existing"}
    d = {k: v for k, v in d.items() if v is not None and k not in _internal}
    jsonschema.validate(instance=d, schema=FINDING_SCHEMA)


# ---------------------------------------------------------------------------
# RunResult
# ---------------------------------------------------------------------------


def test_run_result_defaults_sensible() -> None:
    """RunResult can be constructed with minimal required fields."""
    from coco_pr_review.orchestration.base import RunResult

    r = RunResult(
        findings=[],
        candidate_count=0,
        deduped_count=0,
        total_cost_usd=0.0,
        total_turns=0,
        aborted=False,
        abort_reason=None,
    )

    assert r.findings == []
    assert r.aborted is False
    assert r.abort_reason is None
    assert r.total_cost_usd == 0.0


# ---------------------------------------------------------------------------
# BudgetExceededError
# ---------------------------------------------------------------------------


def test_budget_exceeded_error_is_exception() -> None:
    """BudgetExceededError inherits from Exception and carries a message."""
    from coco_pr_review.orchestration.base import BudgetExceededError

    exc = BudgetExceededError("cost limit reached: $1.50 > $1.00")
    assert isinstance(exc, Exception)
    assert "cost limit" in str(exc)
