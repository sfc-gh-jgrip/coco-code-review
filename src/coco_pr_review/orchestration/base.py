"""Orchestrator base types — dataclasses, ABC, BudgetGate, ProgressSink.

Defines the core contract for PR review orchestration: the data shapes that
flow between reviewers, verifiers, and the fan-out driver.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """A single review finding, mirroring FINDING_SCHEMA fields."""

    file: str
    start_line: int
    end_line: int
    severity: str  # "blocker" | "warning" | "nit"
    category: str  # "correctness" | "security" | "perf" | "style" | "test"
    title: str
    evidence: str
    comment: str
    suggested_fix: str | None = None
    confidence: int | None = None
    verifier_reasoning: str | None = None
    # True when the verifier judged this a real defect that lives OUTSIDE the
    # PR's changed lines (i.e. not introduced by this PR). Such findings are
    # routed to the check-run annotations + sticky summary only — never posted
    # as inline review comments, since GitHub rejects inline comments on lines
    # that are not part of the diff.
    pre_existing: bool = False


@dataclass
class ChangedFile:
    """A file changed in the PR with its affected line ranges."""

    path: str
    line_ranges: list[tuple[int, int]]  # 1-indexed inclusive ranges
    patch: str | None = None  # raw unified-diff body for this file, if available


@dataclass
class PullRequestContext:
    """Everything the orchestrator needs to dispatch reviewers."""

    repo_root: Path
    changed_files: list[ChangedFile]
    unified_diff: str | None  # populated only when total changed lines < 500
    conventions_text: str | None  # raw maintainer-trusted text, or None


@dataclass
class PipelineStats:
    """Observability funnel for a review run.

    Makes a zero-finding result legible: it distinguishes "every reviewer ran
    and nothing survived the filters" from "the pipeline never produced
    candidates". Counts mirror the orchestrator's internal tallies.
    """

    reviewer_names: list[str]  # distinct active (non-disabled) reviewers
    replicas_dispatched: int  # reviewer×replica coroutines launched
    replicas_succeeded: int
    replicas_failed: int  # == RunResult.reviewer_failures
    raw_candidates: int  # findings collected pre-dedupe
    deduped_candidates: int  # candidates sent to the verifier
    verified: int  # findings that survived every filter
    dropped_verifier_error: int
    dropped_unparseable: int
    dropped_low_confidence: int
    dropped_evidence_mismatch: int
    dropped_not_in_pr: int
    confidence_threshold: int
    # Count of real defects surfaced even though they live outside the PR's
    # changed lines (correctness/security only). These are kept, not dropped;
    # ``dropped_not_in_pr`` now only counts out-of-diff findings we chose to
    # discard (e.g. non-correctness/security categories).
    pre_existing: int = 0


@dataclass
class RunResult:
    """The outcome of an orchestrator run."""

    findings: list[Finding]
    candidate_count: int  # pre-dedupe, pre-verifier
    deduped_count: int  # post-dedupe, pre-verifier
    total_cost_usd: float  # None is NOT used here; callers pass 0.0 as fallback
    total_turns: int
    aborted: bool
    abort_reason: str | None
    # Count of reviewer replicas that failed (e.g. unparseable/invalid structured
    # output). Non-zero on a non-aborted run signals partial degradation: results
    # may be incomplete. Defaulted so existing callers/tests are unaffected.
    reviewer_failures: int = 0
    # Full analysis funnel for the summary comment. Defaulted to None so existing
    # callers/tests that construct RunResult positionally are unaffected.
    stats: PipelineStats | None = None


# ---------------------------------------------------------------------------
# BudgetGate
# ---------------------------------------------------------------------------


class BudgetGate:
    """Async-safe accumulator that tracks cost and turn expenditure.

    Uses ``asyncio.Lock`` for atomic updates to the running totals.
    """

    def __init__(self, max_usd: float, max_turns: int | None = None) -> None:
        self._max_usd = max_usd
        self._max_turns = max_turns
        self._total_usd: float = 0.0
        self._total_turns: int = 0
        self._lock = asyncio.Lock()

    async def register_cost(self, usd: float) -> None:
        """Atomically add cost. None/zero costs are safe to pass."""
        async with self._lock:
            self._total_usd += usd

    async def register_turns(self, n: int) -> None:
        """Atomically add turn count."""
        async with self._lock:
            self._total_turns += n

    def should_abort(self) -> bool:
        """Snapshot read of whether budget is exceeded.

        This is intentionally UNLOCKED. It is safe under asyncio's cooperative
        single-threaded model because Python float/int reads are atomic at the
        bytecode level and we only need an approximate "is it over?" check.

        WARNING: If this code is ever migrated to threads, this method MUST
        acquire the lock. Audit all call sites before changing this invariant.
        """
        if self._total_usd >= self._max_usd:
            return True
        if self._max_turns is not None and self._total_turns >= self._max_turns:
            return True
        return False

    def reason(self) -> str:
        """Human-readable abort reason string."""
        parts: list[str] = []
        if self._total_usd >= self._max_usd:
            parts.append(f"cost ${self._total_usd:.4f} >= limit ${self._max_usd:.4f}")
        if self._max_turns is not None and self._total_turns >= self._max_turns:
            parts.append(f"turns {self._total_turns} >= limit {self._max_turns}")
        return "; ".join(parts) or "budget not exceeded"


# ---------------------------------------------------------------------------
# ProgressSink
# ---------------------------------------------------------------------------


@runtime_checkable
class ProgressSink(Protocol):
    """Protocol for reporting orchestration progress."""

    def phase_started(self, name: str) -> None: ...
    def phase_completed(self, name: str, summary: str) -> None: ...
    def finding_emitted(self, finding: Finding) -> None: ...


class NoOpProgressSink:
    """Default sink that silently discards all events."""

    def phase_started(self, name: str) -> None:
        pass

    def phase_completed(self, name: str, summary: str) -> None:
        pass

    def finding_emitted(self, finding: Finding) -> None:
        pass


class RecordingProgressSink:
    """Test-friendly sink that records events in order for assertions."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def phase_started(self, name: str) -> None:
        self.events.append(("phase_started", name))

    def phase_completed(self, name: str, summary: str) -> None:
        self.events.append(("phase_completed", name, summary))

    def finding_emitted(self, finding: Finding) -> None:
        self.events.append(("finding_emitted", finding))


# ---------------------------------------------------------------------------
# Orchestrator ABC
# ---------------------------------------------------------------------------


class Orchestrator(ABC):
    """Abstract base for orchestration strategies (e.g. PythonFanout)."""

    @abstractmethod
    async def run(self, pr_context, reviewers, verifier, budget, progress) -> RunResult:
        """Execute the full review pipeline and return aggregated results."""
        ...


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OrchestratorError(Exception):
    """Base for orchestrator-specific errors."""


class BudgetExceededError(OrchestratorError):
    """Raised when the orchestrator's budget gate trips mid-execution."""
