"""Tests for `coco_pr_review.orchestration.python_fanout` — PythonFanoutOrchestrator.

Drives the full reviewer fan-out → dedupe → verifier fan-out → confidence filter
pipeline against a fake `run_one_query` callable. No real SDK calls are made.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers: fake run_one_query + fixture data
# ---------------------------------------------------------------------------


def _make_finding(
    *,
    file: str = "src/app.py",
    start_line: int = 10,
    end_line: int = 12,
    severity: str = "warning",
    category: str = "correctness",
    title: str = "Bug found",
    evidence: str = "x = 1 / 0",
    comment: str = "Division by zero.",
    suggested_fix: str | None = None,
) -> dict[str, Any]:
    """Build a raw finding dict as a reviewer subagent would emit."""
    d: dict[str, Any] = {
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "severity": severity,
        "category": category,
        "title": title,
        "evidence": evidence,
        "comment": comment,
    }
    if suggested_fix:
        d["suggested_fix"] = suggested_fix
    return d


def _make_verification(*, confidence: int = 85) -> dict[str, Any]:
    """Build a raw verifier output dict."""
    return {
        "confidence": confidence,
        "evidence_matches": True,
        "lines_in_pr": True,
        "verifier_reasoning": "Evidence matches; defect is real.",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_two_reviewers_all_verified() -> None:
    """Two reviewers × 1 replica each; each returns 2 unique findings; all verified at confidence=85."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        Finding,
        NoOpProgressSink,
        PullRequestContext,
        RunResult,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer_a = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    reviewer_b = ReviewerSpec(
        name="performance-and-cost",
        description="Finds perf issues",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find perf issues.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies findings",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    findings_a = [
        _make_finding(title="Bug A1", start_line=1, end_line=2),
        _make_finding(title="Bug A2", start_line=5, end_line=6),
    ]
    findings_b = [
        _make_finding(title="Perf B1", start_line=20, end_line=22, category="perf"),
        _make_finding(title="Perf B2", start_line=30, end_line=32, category="perf"),
    ]

    call_count = {"reviewer": 0, "verifier": 0}

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        """Return findings for reviewers, verification for verifier calls."""
        if "verify" in system_prompt.lower() or "verify" in kwargs.get("role", ""):
            call_count["verifier"] += 1
            return _make_verification(confidence=85), _FakeResult(cost=0.001, turns=1)
        else:
            call_count["reviewer"] += 1
            # Alternate between reviewer_a and reviewer_b findings
            if "bugs" in system_prompt.lower():
                return {"findings": findings_a}, _FakeResult(cost=0.01, turns=3)
            else:
                return {"findings": findings_b}, _FakeResult(cost=0.01, turns=3)

    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake-repo"),
        changed_files=[],
        unified_diff="fake diff",
        conventions_text=None,
    )

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    budget = BudgetGate(max_usd=10.0)
    progress = NoOpProgressSink()

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer_a, reviewer_b],
        verifier=verifier,
        budget=budget,
        progress=progress,
    )

    assert isinstance(result, RunResult)
    assert len(result.findings) == 4
    assert result.aborted is False
    assert result.candidate_count == 4
    assert result.deduped_count == 4


@pytest.mark.asyncio
async def test_replicas_are_deduped_by_fingerprint() -> None:
    """1 reviewer × 2 replicas returning identical findings → 1 candidate after dedupe."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
        RunResult,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="style-and-conventions",
        description="Style check",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You check style.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    # Both replicas return the exact same finding
    duplicate_finding = _make_finding(title="Style violation", evidence="tabs used")

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=90), _FakeResult(cost=0.001, turns=1)
        return {"findings": [duplicate_finding]}, _FakeResult(cost=0.01, turns=2)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
        replicas={"style-and-conventions": 2},
    )

    # 2 replicas × 1 finding = 2 raw, but dedupe collapses to 1
    assert result.candidate_count == 2
    assert result.deduped_count == 1
    assert len(result.findings) == 1


@pytest.mark.asyncio
async def test_candidate_cap_truncates_to_20() -> None:
    """A reviewer returning 25 findings gets truncated to 20 before verifier."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    # 25 unique findings
    many_findings = [
        _make_finding(title=f"Bug {i}", start_line=i, end_line=i + 1)
        for i in range(25)
    ]

    verifier_calls = 0

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        nonlocal verifier_calls
        if "verify" in system_prompt.lower():
            verifier_calls += 1
            return _make_verification(confidence=90), _FakeResult(cost=0.001, turns=1)
        return {"findings": many_findings}, _FakeResult(cost=0.02, turns=4)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    # Truncated to 20 before verifier
    assert verifier_calls == 20
    assert result.deduped_count == 20


@pytest.mark.asyncio
async def test_confidence_filter_drops_below_threshold() -> None:
    """Verifier returns confidence=70 for half, 90 for half → only 90s survive."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    findings = [
        _make_finding(title=f"Bug {i}", start_line=i * 10, end_line=i * 10 + 2)
        for i in range(4)
    ]

    verifier_call_idx = 0

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        nonlocal verifier_call_idx
        if "verify" in system_prompt.lower():
            # Alternate: 70, 90, 70, 90
            confidence = 70 if verifier_call_idx % 2 == 0 else 90
            verifier_call_idx += 1
            return _make_verification(confidence=confidence), _FakeResult(cost=0.001, turns=1)
        return {"findings": findings}, _FakeResult(cost=0.01, turns=3)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
        confidence_threshold=80,
    )

    # Only 2 out of 4 findings pass (confidence=90)
    assert len(result.findings) == 2
    assert all(f.confidence >= 80 for f in result.findings)


@pytest.mark.asyncio
async def test_budget_abort_skips_verifier() -> None:
    """When budget is exceeded after reviewer fan-out, verifier is skipped and aborted=True."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    verifier_called = False

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        nonlocal verifier_called
        if "verify" in system_prompt.lower():
            verifier_called = True
            return _make_verification(), _FakeResult(cost=0.001, turns=1)
        # Reviewer blows past the budget
        return {"findings": [_make_finding()]}, _FakeResult(cost=0.05, turns=5)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    # Budget is tiny — reviewer will exceed it
    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=0.01),
        progress=NoOpProgressSink(),
    )

    assert result.aborted is True
    assert result.abort_reason == "budget"
    assert verifier_called is False
    assert result.findings == []


@pytest.mark.asyncio
async def test_per_reviewer_failure_isolation() -> None:
    """One reviewer raises; the other completes; result has the surviving reviewer's findings."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer_ok = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    reviewer_broken = ReviewerSpec(
        name="performance-and-cost",
        description="Broken",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You are broken.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=90), _FakeResult(cost=0.001, turns=1)
        if "broken" in system_prompt.lower():
            raise RuntimeError("Simulated reviewer failure")
        return {"findings": [_make_finding(title="Real bug")]}, _FakeResult(cost=0.01, turns=2)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer_ok, reviewer_broken],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    # Only the working reviewer's findings survive
    assert len(result.findings) == 1
    assert result.findings[0].title == "Real bug"
    assert result.aborted is False


@pytest.mark.asyncio
async def test_verifier_failure_drops_finding() -> None:
    """A verifier exception for one candidate drops it; others survive."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    findings = [
        _make_finding(title="Bug OK", start_line=1, end_line=2),
        _make_finding(title="Bug FAIL", start_line=10, end_line=12),
    ]

    verifier_call_idx = 0

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        nonlocal verifier_call_idx
        if "verify" in system_prompt.lower():
            idx = verifier_call_idx
            verifier_call_idx += 1
            if idx == 1:
                raise RuntimeError("Simulated verifier timeout")
            return _make_verification(confidence=90), _FakeResult(cost=0.001, turns=1)
        return {"findings": findings}, _FakeResult(cost=0.01, turns=3)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    # One finding passes, one dropped due to verifier failure
    assert len(result.findings) == 1
    assert result.findings[0].title == "Bug OK"


# ---------------------------------------------------------------------------
# Extended tests: mandatory drop, negative dedupe, None handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mandatory_drop_confidence_79() -> None:
    """A verified finding with confidence=79 is dropped (threshold is 80)."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            # Confidence 79 — just below threshold.
            return {
                "confidence": 79,
                "evidence_matches": True,
                "lines_in_pr": True,
                "verifier_reasoning": "Close but not enough.",
            }, _FakeResult(cost=0.001, turns=1)
        return {"findings": [_make_finding(title="Almost-sure bug")]}, _FakeResult()

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
        confidence_threshold=80,
    )

    # Finding dropped — confidence 79 < 80.
    assert len(result.findings) == 0
    assert result.deduped_count == 1


@pytest.mark.asyncio
async def test_mandatory_drop_evidence_matches_false() -> None:
    """A finding with evidence_matches=False is dropped even with confidence=95."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return {
                "confidence": 95,
                "evidence_matches": False,  # ← MANDATORY DROP
                "lines_in_pr": True,
                "verifier_reasoning": "Evidence does not match actual code.",
            }, _FakeResult(cost=0.001, turns=1)
        return {"findings": [_make_finding(title="Phantom bug")]}, _FakeResult()

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    # Dropped: evidence_matches=False regardless of confidence=95.
    assert len(result.findings) == 0


@pytest.mark.asyncio
async def test_mandatory_drop_lines_in_pr_false() -> None:
    """A finding with lines_in_pr=False is dropped even with confidence=90."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return {
                "confidence": 90,
                "evidence_matches": True,
                "lines_in_pr": False,  # ← MANDATORY DROP: pre-existing issue
                "verifier_reasoning": "Lines are not in this PR.",
            }, _FakeResult(cost=0.001, turns=1)
        return {"findings": [_make_finding(title="Pre-existing bug")]}, _FakeResult()

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    # Dropped: lines_in_pr=False regardless of confidence=90.
    assert len(result.findings) == 0


@pytest.mark.asyncio
async def test_negative_dedupe_different_evidence_both_survive() -> None:
    """Two findings with same (file, start, end, title) but DIFFERENT evidence → both survive.

    This is the documented v1 limitation: the fingerprint includes `evidence`,
    so slightly different wording escapes dedupe. This test PINS that behavior
    so it doesn't get "fixed" by accident.
    """
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="style-and-conventions",
        description="Style",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You check style.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    # Two findings with identical (file, start_line, end_line, title)
    # but DIFFERENT evidence strings — they should NOT be deduped.
    finding_a = _make_finding(
        title="Style issue",
        file="src/app.py",
        start_line=10,
        end_line=12,
        evidence="tabs used instead of spaces",
    )
    finding_b = _make_finding(
        title="Style issue",
        file="src/app.py",
        start_line=10,
        end_line=12,
        evidence="indentation uses tabs not spaces",  # DIFFERENT evidence
    )

    replica_idx = 0

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        nonlocal replica_idx
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=85), _FakeResult(cost=0.001, turns=1)
        # Replica 0 returns finding_a, replica 1 returns finding_b.
        idx = replica_idx
        replica_idx += 1
        if idx == 0:
            return {"findings": [finding_a]}, _FakeResult()
        return {"findings": [finding_b]}, _FakeResult()

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
        replicas={"style-and-conventions": 2},
    )

    # Both survive dedupe because fingerprints differ (evidence differs).
    # This is the documented v1 limitation — acknowledged noise.
    assert result.candidate_count == 2
    assert result.deduped_count == 2  # NOT collapsed
    assert len(result.findings) == 2


@pytest.mark.asyncio
async def test_total_cost_usd_none_does_not_crash() -> None:
    """Reviewer returns cost=None; orchestrator accumulates 0 and continues."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=90), _FakeResult(cost=None, turns=1)
        return {"findings": [_make_finding(title="Null-cost bug")]}, _FakeResult(cost=None, turns=2)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    # Should not crash; cost treated as 0.
    assert result.total_cost_usd == 0.0
    assert len(result.findings) == 1
    assert result.aborted is False


@pytest.mark.asyncio
async def test_structured_output_none_treated_as_zero_findings() -> None:
    """run_one_query returns (None, result) → orchestrator treats as zero findings."""
    from coco_pr_review.orchestration.base import (
        BudgetGate,
        NoOpProgressSink,
        PullRequestContext,
    )
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.reviewer_spec import ReviewerSpec

    reviewer = ReviewerSpec(
        name="bugs-and-security",
        description="Finds bugs",
        model="claude-sonnet-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You find bugs.",
    )
    verifier = ReviewerSpec(
        name="verifier",
        description="Verifies",
        model="claude-opus-4-6",
        tools=["Read", "Glob", "Grep"],
        system_prompt="You verify.",
    )

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **kwargs: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=90), _FakeResult(cost=0.001, turns=1)
        # Reviewer returns None structured output (parse failure in SDK).
        return None, _FakeResult(cost=0.01, turns=2)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    pr_context = PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )

    result = await orch.run(
        pr_context=pr_context,
        reviewers=[reviewer],
        verifier=verifier,
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    # Zero findings — reviewer's output was None.
    assert len(result.findings) == 0
    assert result.candidate_count == 0
    assert result.aborted is False


# ---------------------------------------------------------------------------
# Helper: fake result object returned by fake_run_one_query
# ---------------------------------------------------------------------------


class _FakeResult:
    """Mimics the cost/turns fields the orchestrator reads from ResultMessage."""

    def __init__(self, *, cost: float = 0.01, turns: int = 1) -> None:
        self.total_cost_usd = cost
        self.num_turns = turns


# ---------------------------------------------------------------------------
# Config-driven behavior tests
# ---------------------------------------------------------------------------


def _make_reviewer_spec(name: str = "bugs-and-security") -> "object":
    """Build a minimal ReviewerSpec for config-wiring tests."""
    from coco_pr_review.reviewer_spec import ReviewerSpec

    return ReviewerSpec(
        name=name,
        description="x",
        model="claude-sonnet-4-6",
        tools=["Read"],
        system_prompt=f"You are {name}.",
    )


def _make_verifier_spec() -> "object":
    from coco_pr_review.reviewer_spec import ReviewerSpec

    return ReviewerSpec(
        name="verifier",
        description="x",
        model="claude-opus-4-6",
        tools=["Read"],
        system_prompt="You verify findings.",
    )


def _make_pr_context() -> "object":
    from coco_pr_review.orchestration.base import PullRequestContext

    return PullRequestContext(
        repo_root=Path("/tmp/fake"),
        changed_files=[],
        unified_diff=None,
        conventions_text=None,
    )


@pytest.mark.asyncio
async def test_config_supplies_default_confidence_threshold() -> None:
    """When no per-call threshold is given, orchestrator uses config.verifier.confidence_threshold."""
    from coco_pr_review.config import DEFAULT_CONFIG
    import dataclasses as dc

    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    # Threshold = 90 — finding at 85 should be DROPPED.
    cfg = dc.replace(
        DEFAULT_CONFIG,
        verifier=dc.replace(DEFAULT_CONFIG.verifier, confidence_threshold=90),
    )

    finding = _make_finding(title="Maybe-real", evidence="suspicious()")

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=85), _FakeResult(cost=0.0, turns=0)
        return {"findings": [finding]}, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query, config=cfg)

    result = await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec()],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    # confidence (85) < threshold from config (90) → dropped.
    assert result.findings == []
    assert result.candidate_count == 1
    assert result.deduped_count == 1


@pytest.mark.asyncio
async def test_per_call_confidence_threshold_overrides_config() -> None:
    """Explicit `confidence_threshold=` on .run() wins over config."""
    from coco_pr_review.config import DEFAULT_CONFIG
    import dataclasses as dc

    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    cfg = dc.replace(
        DEFAULT_CONFIG,
        verifier=dc.replace(DEFAULT_CONFIG.verifier, confidence_threshold=99),
    )

    finding = _make_finding(title="Real bug")

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=85), _FakeResult(cost=0.0, turns=0)
        return {"findings": [finding]}, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query, config=cfg)

    result = await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec()],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
        confidence_threshold=80,  # per-call override beats config.99
    )

    assert len(result.findings) == 1


@pytest.mark.asyncio
async def test_config_replicas_drive_default_fanout() -> None:
    """When no per-call replicas dict is given, replica counts come from config.reviewers[]."""
    from coco_pr_review.config import DEFAULT_CONFIG
    import dataclasses as dc

    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    # Override style-and-conventions to 5 replicas.
    new_reviewers = []
    for r in DEFAULT_CONFIG.reviewers:
        if r.name == "style-and-conventions":
            new_reviewers.append(dc.replace(r, replicas=5))
        else:
            new_reviewers.append(r)
    cfg = dc.replace(DEFAULT_CONFIG, reviewers=new_reviewers)

    invocation_count = {"reviewer": 0}

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=95), _FakeResult(cost=0.0, turns=0)
        invocation_count["reviewer"] += 1
        return {"findings": []}, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query, config=cfg)

    await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec(name="style-and-conventions")],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    assert invocation_count["reviewer"] == 5


@pytest.mark.asyncio
async def test_per_call_replicas_override_config() -> None:
    """Explicit `replicas=` dict on .run() wins outright over config replicas."""
    from coco_pr_review.config import DEFAULT_CONFIG
    import dataclasses as dc

    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    new_reviewers = []
    for r in DEFAULT_CONFIG.reviewers:
        if r.name == "style-and-conventions":
            new_reviewers.append(dc.replace(r, replicas=5))
        else:
            new_reviewers.append(r)
    cfg = dc.replace(DEFAULT_CONFIG, reviewers=new_reviewers)

    invocation_count = {"reviewer": 0}

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=95), _FakeResult(cost=0.0, turns=0)
        invocation_count["reviewer"] += 1
        return {"findings": []}, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query, config=cfg)

    await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec(name="style-and-conventions")],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
        replicas={"style-and-conventions": 1},  # beats config.5
    )

    assert invocation_count["reviewer"] == 1


@pytest.mark.asyncio
async def test_config_caps_max_findings_per_reviewer() -> None:
    """A reviewer returning more findings than config.limits.max_findings_per_reviewer is capped."""
    from coco_pr_review.config import DEFAULT_CONFIG
    import dataclasses as dc

    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    cfg = dc.replace(
        DEFAULT_CONFIG,
        limits=dc.replace(DEFAULT_CONFIG.limits, max_findings_per_reviewer=3),
    )

    # Reviewer returns 10 findings; cap is 3.
    flood = [
        _make_finding(title=f"F{i}", start_line=i, end_line=i, evidence=f"e{i}")
        for i in range(10)
    ]

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=95), _FakeResult(cost=0.0, turns=0)
        return {"findings": flood}, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query, config=cfg)

    result = await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec()],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    assert result.candidate_count == 3
    assert len(result.findings) == 3


@pytest.mark.asyncio
async def test_disabled_reviewer_in_config_is_skipped() -> None:
    """A reviewer with `enabled: false` in config is not invoked at all."""
    from coco_pr_review.config import DEFAULT_CONFIG
    import dataclasses as dc

    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    new_reviewers = []
    for r in DEFAULT_CONFIG.reviewers:
        if r.name == "tests-coverage":
            new_reviewers.append(dc.replace(r, enabled=False))
        else:
            new_reviewers.append(r)
    cfg = dc.replace(DEFAULT_CONFIG, reviewers=new_reviewers)

    invoked_names: list[str] = []

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=95), _FakeResult(cost=0.0, turns=0)
        # Capture which reviewer's system prompt was sent.
        invoked_names.append(system_prompt.split("You are ")[-1].rstrip("."))
        return {"findings": []}, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query, config=cfg)

    await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[
            _make_reviewer_spec(name="bugs-and-security"),
            _make_reviewer_spec(name="tests-coverage"),
        ],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    assert "bugs-and-security" in invoked_names
    assert "tests-coverage" not in invoked_names


@pytest.mark.asyncio
async def test_prompt_extra_appended_to_reviewer_system_prompt() -> None:
    """`config.reviewers[].prompt_extra` is appended after the reviewer's spec.system_prompt."""
    from coco_pr_review.config import DEFAULT_CONFIG
    import dataclasses as dc

    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    extra = "Pay extra attention to authentication code."
    new_reviewers = []
    for r in DEFAULT_CONFIG.reviewers:
        if r.name == "bugs-and-security":
            new_reviewers.append(dc.replace(r, prompt_extra=extra))
        else:
            new_reviewers.append(r)
    cfg = dc.replace(DEFAULT_CONFIG, reviewers=new_reviewers)

    captured: dict[str, str] = {}

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=95), _FakeResult(cost=0.0, turns=0)
        captured["system_prompt"] = system_prompt
        return {"findings": []}, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query, config=cfg)

    await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec(name="bugs-and-security")],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    assert "You are bugs-and-security." in captured["system_prompt"]
    assert extra in captured["system_prompt"]
    # Extra appended AFTER the base prompt (order matters).
    assert captured["system_prompt"].index("You are") < captured["system_prompt"].index(extra)


@pytest.mark.asyncio
async def test_unknown_reviewer_name_runs_with_defaults() -> None:
    """A ReviewerSpec whose name has no override entry is enabled with no prompt_extra."""
    from coco_pr_review.config import DEFAULT_CONFIG

    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    captured: dict[str, str] = {}
    invoked = {"count": 0}

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=95), _FakeResult(cost=0.0, turns=0)
        invoked["count"] += 1
        captured["system_prompt"] = system_prompt
        return {"findings": []}, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query, config=DEFAULT_CONFIG)

    await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec(name="brand-new-reviewer")],  # not in DEFAULT_CONFIG
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    assert invoked["count"] == 1  # not skipped
    # No prompt_extra appendix.
    assert "Additional instructions" not in captured["system_prompt"]


@pytest.mark.asyncio
async def test_config_drives_job_timeout() -> None:
    """config.limits.job_timeout_sec is the actual timeout backstop."""
    import asyncio as _asyncio

    from coco_pr_review.config import DEFAULT_CONFIG
    import dataclasses as dc

    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    # Tiny timeout — reviewer hangs longer than the cap.
    cfg = dc.replace(
        DEFAULT_CONFIG,
        limits=dc.replace(DEFAULT_CONFIG.limits, job_timeout_sec=1),
    )

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        await _asyncio.sleep(5)  # exceed the 1s cap
        return None, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query, config=cfg)

    result = await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec()],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    assert result.aborted is True
    assert result.abort_reason == "job timeout"


@pytest.mark.asyncio
async def test_all_reviewer_replicas_failing_aborts_run() -> None:
    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        raise RuntimeError("reviewer exploded")

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)

    result = await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec()],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
        replicas={"bugs-and-security": 2},
    )

    assert result.aborted is True
    assert result.abort_reason == "all reviewer replicas failed"
    assert result.candidate_count == 0
    assert result.deduped_count == 0
    assert result.findings == []


@pytest.mark.asyncio
async def test_reviewer_partial_failure_still_succeeds_when_one_replica_completes() -> None:
    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator

    calls = {"reviewer": 0}
    finding = _make_finding(title="Real bug")

    async def fake_run_one_query(*, system_prompt: str, user_prompt: str, **_: Any) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            return _make_verification(confidence=95), _FakeResult(cost=0.0, turns=0)

        calls["reviewer"] += 1
        if calls["reviewer"] == 1:
            raise RuntimeError("first reviewer replica failed")
        return {"findings": [finding]}, _FakeResult(cost=0.0, turns=0)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)

    result = await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec()],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
        replicas={"bugs-and-security": 2},
    )

    assert result.aborted is False
    assert result.abort_reason is None
    assert result.candidate_count == 1
    assert result.deduped_count == 1
    assert len(result.findings) == 1


@pytest.mark.asyncio
async def test_output_schema_forwarded_to_run_one_query() -> None:
    """Reviewer calls forward REVIEWER_OUTPUT_SCHEMA; verifier calls forward VERIFIER_OUTPUT_SCHEMA."""
    from coco_pr_review.orchestration.base import BudgetGate, NoOpProgressSink
    from coco_pr_review.orchestration.python_fanout import PythonFanoutOrchestrator
    from coco_pr_review.schema import REVIEWER_OUTPUT_SCHEMA, VERIFIER_OUTPUT_SCHEMA

    captured: list[tuple[str, Any]] = []

    async def fake_run_one_query(
        *, system_prompt: str, user_prompt: str, output_schema: Any = None, **kwargs: Any
    ) -> tuple[Any, Any]:
        if "verify" in system_prompt.lower():
            captured.append(("verifier", output_schema))
            return _make_verification(confidence=90), _FakeResult(cost=0.0, turns=1)
        captured.append(("reviewer", output_schema))
        return {"findings": [_make_finding(title="Schema bug")]}, _FakeResult(cost=0.0, turns=1)

    orch = PythonFanoutOrchestrator(run_one_query=fake_run_one_query)
    await orch.run(
        pr_context=_make_pr_context(),
        reviewers=[_make_reviewer_spec()],
        verifier=_make_verifier_spec(),
        budget=BudgetGate(max_usd=10.0),
        progress=NoOpProgressSink(),
    )

    reviewer_schemas = [schema for role, schema in captured if role == "reviewer"]
    verifier_schemas = [schema for role, schema in captured if role == "verifier"]

    assert reviewer_schemas, "expected at least one reviewer call"
    assert verifier_schemas, "expected at least one verifier call"
    assert all(schema is REVIEWER_OUTPUT_SCHEMA for schema in reviewer_schemas)
    assert all(schema is VERIFIER_OUTPUT_SCHEMA for schema in verifier_schemas)

