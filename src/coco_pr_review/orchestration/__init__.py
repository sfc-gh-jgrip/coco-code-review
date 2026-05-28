"""Orchestration package — public API surface for PR review orchestration."""
from __future__ import annotations

from coco_pr_review.orchestration.base import (
    BudgetExceededError,
    BudgetGate,
    ChangedFile,
    Finding,
    NoOpProgressSink,
    Orchestrator,
    OrchestratorError,
    ProgressSink,
    PullRequestContext,
    RecordingProgressSink,
    RunResult,
)
from coco_pr_review.orchestration.sdk_adapter import (
    HardSdkError,
    TransientSdkError,
    run_one_query,
)

__all__ = [
    "BudgetExceededError",
    "BudgetGate",
    "ChangedFile",
    "Finding",
    "HardSdkError",
    "NoOpProgressSink",
    "Orchestrator",
    "OrchestratorError",
    "ProgressSink",
    "PullRequestContext",
    "RecordingProgressSink",
    "RunResult",
    "TransientSdkError",
    "run_one_query",
]
