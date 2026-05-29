"""PythonFanoutOrchestrator — reviewer fan-out → dedupe → verifier fan-out → filter.

Implements the full review pipeline:
  Phase A: Dispatch N replicas per reviewer via asyncio.gather, collect raw findings.
  Dedupe:  Collapse identical fingerprints across replicas of the same reviewer.
  Phase B: For each surviving candidate, dispatch a verifier query.
  Filter:  MANDATORY belt-and-braces — drop if confidence < threshold OR
           evidence_matches=False OR lines_in_pr=False.

The orchestrator does NOT post findings — it returns a RunResult.
Sanitization is the publisher's responsibility (later milestone).
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable

from coco_pr_review.config import CocoPRReviewConfig, DEFAULT_CONFIG, ReviewerOverride
from coco_pr_review.orchestration.base import (
    BudgetGate,
    ChangedFile,
    Finding,
    NoOpProgressSink,
    Orchestrator,
    ProgressSink,
    PullRequestContext,
    RunResult,
)
from coco_pr_review.prompts import wrap_untrusted
from coco_pr_review.reviewer_spec import ReviewerSpec
from coco_pr_review.schema import REVIEWER_OUTPUT_SCHEMA, VERIFIER_OUTPUT_SCHEMA

logger = logging.getLogger(__name__)

# Type alias for the injected query function.
# Signature: async (*, system_prompt, user_prompt, **kwargs) -> (structured_output, result)
RunOneQueryFn = Callable[..., Awaitable[tuple[Any, Any]]]


def _fingerprint(finding: dict[str, Any]) -> tuple:
    """Compute the dedupe fingerprint for a raw finding dict.

    Key: (file, start_line, end_line, title, evidence).
    Same key across replicas of the same reviewer → collapse to one.

    KNOWN V1 LIMITATION: If replicas produce the same defect but quote
    slightly different `evidence` strings, both survive dedupe.
    """
    return (
        finding.get("file"),
        finding.get("start_line"),
        finding.get("end_line"),
        finding.get("title"),
        finding.get("evidence"),
    )


def _build_changed_files_map(changed_files: list[ChangedFile]) -> str:
    """Render the changed-files map in the exact format the reviewer prompts expect.

    Format (one line per file):
        <path>: lines <start>-<end>, <start>-<end>
    """
    lines: list[str] = []
    for cf in changed_files:
        ranges = ", ".join(f"{s}-{e}" for s, e in cf.line_ranges)
        lines.append(f"{cf.path}: lines {ranges}")
    return "\n".join(lines)


def _build_reviewer_user_prompt(
    pr_context: PullRequestContext,
    changed_files_map: str,
) -> str:
    """Assemble the user prompt sent to each reviewer replica.

    Structure:
      1. Changed files map (plain text — derived from trusted PR metadata)
      2. Unified diff wrapped in UNTRUSTED markers (PR-author-controlled)
      3. Conventions text verbatim (maintainer-controlled, NOT wrapped)
    """
    parts: list[str] = []

    if changed_files_map:
        parts.append("## Changed files\n" + changed_files_map)

    # Diff is PR-author-controlled → untrusted.
    unified_diff = getattr(pr_context, "unified_diff", None) or getattr(pr_context, "diff", None)
    if unified_diff:
        parts.append("## Diff\n" + wrap_untrusted(unified_diff))

    # Conventions are maintainer-controlled → trusted, NOT wrapped.
    conventions = getattr(pr_context, "conventions_text", None) or getattr(pr_context, "conventions", None)
    if conventions:
        parts.append("## Conventions\n" + conventions)

    return "\n\n".join(parts)


def _build_verifier_user_prompt(
    candidate: dict[str, Any],
    changed_files_map: str,
) -> str:
    """Assemble the user prompt sent to the verifier for a single candidate.

    The candidate's `comment` and `evidence` came from the reviewer (model-
    controlled), so they are wrapped defensively via wrap_untrusted.
    """
    # Embed the finding as JSON for the verifier to inspect.
    finding_json = json.dumps(candidate, indent=2)
    parts = [
        "## Finding to verify\n" + wrap_untrusted(finding_json),
    ]
    if changed_files_map:
        parts.append("## Changed files\n" + changed_files_map)
    return "\n\n".join(parts)


def _raw_finding_to_dataclass(raw: dict[str, Any], verification: dict[str, Any]) -> Finding:
    """Merge raw finding dict with verifier output into a Finding dataclass."""
    return Finding(
        file=raw["file"],
        start_line=raw["start_line"],
        end_line=raw["end_line"],
        severity=raw["severity"],
        category=raw["category"],
        title=raw["title"],
        evidence=raw["evidence"],
        comment=raw["comment"],
        suggested_fix=raw.get("suggested_fix"),
        confidence=verification["confidence"],
        verifier_reasoning=verification.get("verifier_reasoning"),
    )


class PythonFanoutOrchestrator(Orchestrator):
    """Fan-out orchestrator using asyncio.gather for parallelism.

    Parameters
    ----------
    run_one_query : callable
        Injected query function matching the signature:
        async (*, system_prompt: str, user_prompt: str, **kwargs) -> (output, result)
        In production this wraps the SDK adapter; in tests it's a fake.
    config : CocoPRReviewConfig | None
        Resolved configuration (defaults → file → CLI). When ``None``,
        ``DEFAULT_CONFIG`` is used. The orchestrator reads ``config`` for:
          - ``limits.job_timeout_sec`` — overall run timeout backstop.
          - ``limits.max_findings_per_reviewer`` — defensive per-replica cap.
          - ``verifier.confidence_threshold`` — drop threshold (per-call
            ``run(confidence_threshold=...)`` still wins when supplied).
          - ``reviewers[]`` — per-name ``replicas`` count, ``enabled`` flag,
            and ``prompt_extra`` appendix. Reviewer specs whose ``name`` is
            not in the config are treated as enabled with no extras.
    """

    def __init__(
        self,
        *,
        run_one_query: RunOneQueryFn,
        config: CocoPRReviewConfig | None = None,
    ) -> None:
        self._run_one_query = run_one_query
        self._config = config if config is not None else DEFAULT_CONFIG

    async def run(
        self,
        pr_context: PullRequestContext,
        reviewers: list[ReviewerSpec],
        verifier: ReviewerSpec,
        budget: BudgetGate,
        progress: ProgressSink | None = None,
        *,
        replicas: dict[str, int] | None = None,
        confidence_threshold: int | None = None,
    ) -> RunResult:
        """Execute the full review pipeline.

        Wrapped in a job-timeout backstop sourced from
        ``config.limits.job_timeout_sec``. On timeout, returns a partial
        ``RunResult`` with ``aborted=True``, ``abort_reason="job timeout"``.

        ``replicas`` and ``confidence_threshold`` are per-call overrides; when
        ``None`` they fall back to the values in ``self._config``.
        """
        if progress is None:
            progress = NoOpProgressSink()

        resolved_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else self._config.verifier.confidence_threshold
        )

        try:
            return await asyncio.wait_for(
                self._run_inner(
                    pr_context=pr_context,
                    reviewers=reviewers,
                    verifier=verifier,
                    budget=budget,
                    progress=progress,
                    replicas=replicas,
                    confidence_threshold=resolved_threshold,
                ),
                timeout=self._config.limits.job_timeout_sec,
            )
        except asyncio.TimeoutError:
            return RunResult(
                findings=[],
                candidate_count=0,
                deduped_count=0,
                total_cost_usd=0.0,
                total_turns=0,
                aborted=True,
                abort_reason="job timeout",
            )

    async def _run_inner(
        self,
        pr_context: PullRequestContext,
        reviewers: list[ReviewerSpec],
        verifier: ReviewerSpec,
        budget: BudgetGate,
        progress: ProgressSink,
        replicas: dict[str, int] | None,
        confidence_threshold: int,
    ) -> RunResult:
        """Core pipeline logic, separated from the timeout wrapper."""
        # Build the {name: ReviewerOverride} index from config — used to
        # resolve replicas, `enabled`, and `prompt_extra` per reviewer.
        overrides_by_name: dict[str, ReviewerOverride] = {
            r.name: r for r in self._config.reviewers
        }

        # Resolve per-reviewer replica counts:
        #   1. Per-call `replicas=` override wins outright when supplied.
        #   2. Otherwise, fall back to config.reviewers[].replicas.
        #   3. Reviewers absent from both → 1 (handled by .get below).
        if replicas is not None:
            replica_counts = replicas
        else:
            replica_counts = {name: ov.replicas for name, ov in overrides_by_name.items()}

        # Filter out reviewers that are explicitly disabled in config.
        active_reviewers: list[ReviewerSpec] = []
        for spec in reviewers:
            override = overrides_by_name.get(spec.name)
            if override is not None and not override.enabled:
                logger.info("Reviewer %s disabled via config; skipping.", spec.name)
                continue
            active_reviewers.append(spec)

        max_findings_per_replica = self._config.limits.max_findings_per_reviewer

        changed_files = getattr(pr_context, "changed_files", []) or []
        changed_files_map = _build_changed_files_map(changed_files)
        user_prompt = _build_reviewer_user_prompt(pr_context, changed_files_map)

        total_cost: float = 0.0
        total_turns: int = 0

        # ---------------------------------------------------------------
        # Phase A: Reviewer fan-out
        # ---------------------------------------------------------------
        progress.phase_started("reviewer_fanout")

        async def _invoke_reviewer(reviewer: ReviewerSpec, _replica_idx: int) -> list[dict[str, Any]]:
            """Invoke one reviewer replica; return its findings list (may be empty)."""
            # Apply config-driven `prompt_extra` (maintainer-controlled, trusted).
            override = overrides_by_name.get(reviewer.name)
            if override is not None and override.prompt_extra:
                system_prompt = (
                    f"{reviewer.system_prompt}\n\n"
                    f"## Additional instructions\n{override.prompt_extra}"
                )
            else:
                system_prompt = reviewer.system_prompt

            output, result = await self._run_one_query(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            # Register cost and turns.
            cost = getattr(result, "total_cost_usd", None) or 0
            turns = getattr(result, "num_turns", None) or 0
            await budget.register_cost(cost)
            await budget.register_turns(turns)
            nonlocal total_cost, total_turns
            total_cost += cost
            total_turns += turns

            # Parse structured output.
            if output is None:
                # run_one_query soft-failed → zero findings for this replica.
                logger.warning(
                    "Reviewer %s replica %d returned None structured output; treating as zero findings.",
                    reviewer.name,
                    _replica_idx,
                )
                return []

            # Expect {"findings": [...]}.
            findings_list = output.get("findings", []) if isinstance(output, dict) else []
            # Defensive cap (server-side maxItems is the primary guard).
            return findings_list[:max_findings_per_replica]

        # Build the list of coroutines for all reviewer×replica pairs.
        reviewer_coros = []
        reviewer_replica_count = 0
        for reviewer in active_reviewers:
            n_replicas = replica_counts.get(reviewer.name, 1)
            for replica_idx in range(n_replicas):
                reviewer_coros.append(_invoke_reviewer(reviewer, replica_idx))
                reviewer_replica_count += 1

        # Gather with return_exceptions=True — failures are isolated.
        results = await asyncio.gather(*reviewer_coros, return_exceptions=True)

        # Collect all raw findings, logging failures.
        all_raw_findings: list[dict[str, Any]] = []
        reviewer_exception_count = 0
        reviewer_success_count = 0
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("Reviewer replica failed: %s", r)
                progress.phase_completed("reviewer_replica_error", str(r))
                reviewer_exception_count += 1
                continue
            reviewer_success_count += 1
            all_raw_findings.extend(r)

        if reviewer_replica_count > 0 and reviewer_success_count == 0:
            progress.phase_completed("reviewer_fanout", "all reviewer replicas failed")
            return RunResult(
                findings=[],
                candidate_count=0,
                deduped_count=0,
                total_cost_usd=total_cost,
                total_turns=total_turns,
                aborted=True,
                abort_reason="all reviewer replicas failed",
            )

        candidate_count = len(all_raw_findings)
        progress.phase_completed("reviewer_fanout", f"{candidate_count} candidates")

        # ---------------------------------------------------------------
        # Dedupe by fingerprint
        # ---------------------------------------------------------------
        seen_fingerprints: set[tuple] = set()
        deduped_findings: list[dict[str, Any]] = []
        for finding in all_raw_findings:
            fp = _fingerprint(finding)
            if fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                deduped_findings.append(finding)

        deduped_count = len(deduped_findings)

        # ---------------------------------------------------------------
        # Budget check before verifiers
        # ---------------------------------------------------------------
        if budget.should_abort():
            return RunResult(
                findings=[],
                candidate_count=candidate_count,
                deduped_count=deduped_count,
                total_cost_usd=total_cost,
                total_turns=total_turns,
                aborted=True,
                abort_reason="budget",
            )

        # ---------------------------------------------------------------
        # Phase B: Verifier fan-out
        # ---------------------------------------------------------------
        progress.phase_started("verifier_fanout")

        async def _invoke_verifier(candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
            """Verify one candidate; return (candidate, verification) or None on failure."""
            v_user_prompt = _build_verifier_user_prompt(candidate, changed_files_map)
            output, result = await self._run_one_query(
                system_prompt=verifier.system_prompt,
                user_prompt=v_user_prompt,
            )
            # Register cost and turns.
            cost = getattr(result, "total_cost_usd", None) or 0
            turns = getattr(result, "num_turns", None) or 0
            await budget.register_cost(cost)
            await budget.register_turns(turns)
            nonlocal total_cost, total_turns
            total_cost += cost
            total_turns += turns

            if output is None or not isinstance(output, dict):
                return None

            return (candidate, output)

        verifier_coros = [_invoke_verifier(c) for c in deduped_findings]
        verifier_results = await asyncio.gather(*verifier_coros, return_exceptions=True)

        # ---------------------------------------------------------------
        # Phase C: MANDATORY belt-and-braces filter
        # ---------------------------------------------------------------
        # Drop a verified finding if ANY of:
        #   - confidence < threshold (default 80)
        #   - evidence_matches == False
        #   - lines_in_pr == False
        # This is non-negotiable — pre-existing issues are out of scope.
        verified_findings: list[Finding] = []
        for vr in verifier_results:
            if isinstance(vr, BaseException):
                # Verifier exception → drop the finding.
                logger.warning("Verifier failed for one candidate: %s", vr)
                continue
            if vr is None:
                # Verifier returned unparseable output → drop.
                continue

            candidate, verification = vr

            # --- MANDATORY DROPS (no "optionally" hedge) ---
            confidence = verification.get("confidence", 0)
            if confidence < confidence_threshold:
                continue
            if verification.get("evidence_matches") is False:
                continue
            if verification.get("lines_in_pr") is False:
                continue

            # Finding survived all filters — emit it.
            finding = _raw_finding_to_dataclass(candidate, verification)
            verified_findings.append(finding)
            progress.finding_emitted(finding)

        progress.phase_completed("verifier_fanout", f"{len(verified_findings)} verified")

        return RunResult(
            findings=verified_findings,
            candidate_count=candidate_count,
            deduped_count=deduped_count,
            total_cost_usd=total_cost,
            total_turns=total_turns,
            aborted=False,
            abort_reason=None,
        )
