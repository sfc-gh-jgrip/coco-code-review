"""Tests for `coco_pr_review.orchestration.sdk_adapter` — transient/hard error bridge.

Drives `run_one_query` with fake async-iterables of SDK messages to verify
correct error classification and result extraction without hitting the real CLI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import pytest


# ---------------------------------------------------------------------------
# Fake SDK message types — mirrors the real SDK shapes used by the adapter.
# ---------------------------------------------------------------------------


@dataclass
class FakeAssistantMessage:
    """Mimics cortex_code_agent_sdk.types.AssistantMessage for error-mid-stream tests."""

    content: list = field(default_factory=list)
    model: str = "claude-sonnet-4-6"
    error: str | None = None
    usage: dict | None = None


@dataclass
class FakeResultMessage:
    """Mimics cortex_code_agent_sdk.types.ResultMessage."""

    subtype: str = "success"
    is_error: bool = False
    num_turns: int = 1
    total_cost_usd: float | None = 0.01
    structured_output: Any = None
    result: str | None = None
    duration_ms: int = 100
    duration_api_ms: int = 80
    session_id: str = "test"
    usage: dict | None = None
    stop_reason: str | None = None
    permission_denials: list | None = None


async def _fake_stream(messages: list) -> AsyncIterator:
    """Yields messages as an async iterator — simulates `query()` output."""
    for msg in messages:
        yield msg


# ---------------------------------------------------------------------------
# Tests: error classification via run_one_query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_one_query_raises_transient_on_rate_limit_result() -> None:
    """ResultMessage(is_error=True, subtype='rate_limit') → TransientSdkError."""
    from coco_pr_review.orchestration.sdk_adapter import TransientSdkError, run_one_query

    error_result = FakeResultMessage(is_error=True, subtype="rate_limit")
    stream = _fake_stream([error_result])

    with pytest.raises(TransientSdkError):
        await run_one_query(
            message_stream=stream,
        )


@pytest.mark.asyncio
async def test_run_one_query_raises_hard_on_billing_error_result() -> None:
    """ResultMessage(is_error=True, subtype='billing_error') → HardSdkError."""
    from coco_pr_review.orchestration.sdk_adapter import HardSdkError, run_one_query

    error_result = FakeResultMessage(is_error=True, subtype="billing_error")
    stream = _fake_stream([error_result])

    with pytest.raises(HardSdkError):
        await run_one_query(
            message_stream=stream,
        )


@pytest.mark.asyncio
async def test_run_one_query_returns_structured_output_on_success() -> None:
    """A successful stream ending in ResultMessage returns (structured_output, result)."""
    from coco_pr_review.orchestration.sdk_adapter import run_one_query

    finding = {"file": "x.py", "start_line": 1, "end_line": 2, "severity": "warning",
               "category": "correctness", "title": "bug", "evidence": "x", "comment": "y"}

    ok_result = FakeResultMessage(
        is_error=False,
        structured_output=finding,
        total_cost_usd=0.003,
        num_turns=2,
    )
    assistant_msg = FakeAssistantMessage()
    stream = _fake_stream([assistant_msg, ok_result])

    output, result = await run_one_query(message_stream=stream)

    assert output == finding
    assert result.total_cost_usd == 0.003
    assert result.num_turns == 2


@pytest.mark.asyncio
async def test_run_one_query_raises_transient_on_mid_stream_assistant_error() -> None:
    """AssistantMessage.error='rate_limit' mid-stream → TransientSdkError."""
    from coco_pr_review.orchestration.sdk_adapter import TransientSdkError, run_one_query

    error_msg = FakeAssistantMessage(error="rate_limit")
    stream = _fake_stream([error_msg])

    with pytest.raises(TransientSdkError):
        await run_one_query(
            message_stream=stream,
        )


@pytest.mark.asyncio
async def test_run_one_query_raises_hard_on_mid_stream_assistant_hard_error() -> None:
    """AssistantMessage.error='authentication_failed' mid-stream → HardSdkError."""
    from coco_pr_review.orchestration.sdk_adapter import HardSdkError, run_one_query

    error_msg = FakeAssistantMessage(error="authentication_failed")
    stream = _fake_stream([error_msg])

    with pytest.raises(HardSdkError):
        await run_one_query(
            message_stream=stream,
        )


# ---------------------------------------------------------------------------
# Tests: composition with retry_on_transient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transient_sdk_error_is_caught_by_retry_decorator(monkeypatch) -> None:
    """TransientSdkError composes with retry_on_transient — retries succeed on second call."""
    from coco_pr_review.orchestration.sdk_adapter import TransientSdkError
    from coco_pr_review import retry as retry_mod

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(retry_mod.asyncio, "sleep", fake_sleep)

    attempts = 0

    @retry_mod.retry_on_transient(transient_exc=TransientSdkError)
    async def flaky_call() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TransientSdkError("rate_limit")
        return "ok"

    result = await flaky_call()
    assert result == "ok"
    assert attempts == 2
    assert len(sleeps) == 1


@pytest.mark.asyncio
async def test_hard_sdk_error_is_not_retried(monkeypatch) -> None:
    """HardSdkError propagates immediately through retry_on_transient."""
    from coco_pr_review.orchestration.sdk_adapter import HardSdkError, TransientSdkError
    from coco_pr_review import retry as retry_mod

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(retry_mod.asyncio, "sleep", fake_sleep)

    @retry_mod.retry_on_transient(transient_exc=TransientSdkError)
    async def hard_fail() -> None:
        raise HardSdkError("billing_error")

    with pytest.raises(HardSdkError):
        await hard_fail()

    assert sleeps == []


# ---------------------------------------------------------------------------
# Tests: extract_json — fence/prose-tolerant JSON recovery
# ---------------------------------------------------------------------------


def test_extract_json_plain_object() -> None:
    from coco_pr_review.orchestration.sdk_adapter import extract_json

    assert extract_json('{"findings": []}') == {"findings": []}


def test_extract_json_json_fenced() -> None:
    from coco_pr_review.orchestration.sdk_adapter import extract_json

    raw = '```json\n{"findings": [{"title": "bug"}]}\n```'
    assert extract_json(raw) == {"findings": [{"title": "bug"}]}


def test_extract_json_bare_fenced() -> None:
    from coco_pr_review.orchestration.sdk_adapter import extract_json

    raw = '```\n{"findings": []}\n```'
    assert extract_json(raw) == {"findings": []}


def test_extract_json_prose_preamble_then_fence() -> None:
    from coco_pr_review.orchestration.sdk_adapter import extract_json

    raw = (
        "No conventions files exist in this repository, so there is nothing to flag.\n\n"
        '```json\n{"findings": []}\n```'
    )
    assert extract_json(raw) == {"findings": []}


def test_extract_json_brace_substring_fallback() -> None:
    from coco_pr_review.orchestration.sdk_adapter import extract_json

    raw = 'Here is the result: {"findings": [{"title": "x"}]} -- done.'
    assert extract_json(raw) == {"findings": [{"title": "x"}]}


def test_extract_json_raises_when_no_json() -> None:
    import json

    from coco_pr_review.orchestration.sdk_adapter import extract_json

    with pytest.raises(json.JSONDecodeError):
        extract_json("there is no json here at all")


@pytest.mark.asyncio
async def test_run_one_query_recovers_fenced_json_when_structured_output_missing() -> None:
    """structured_output=None + fenced-JSON result → findings recovered, not dropped."""
    from coco_pr_review.orchestration.sdk_adapter import run_one_query

    finding = {
        "file": "demo_bug.py",
        "start_line": 10,
        "end_line": 10,
        "severity": "blocker",
        "category": "correctness",
        "title": "Division by zero when values list is empty",
        "evidence": "    return total / len(values)",
        "comment": "len(values) is 0 for an empty list.",
    }
    import json as _json

    fenced = "```json\n" + _json.dumps({"findings": [finding]}) + "\n```"
    ok_result = FakeResultMessage(is_error=False, structured_output=None, result=fenced)
    stream = _fake_stream([FakeAssistantMessage(), ok_result])

    output, _ = await run_one_query(message_stream=stream)

    assert output == {"findings": [finding]}


@pytest.mark.asyncio
async def test_run_one_query_raises_when_structured_output_missing_and_result_unparseable() -> None:
    """structured_output=None + non-JSON result → fail closed (StructuredOutputError)."""
    from coco_pr_review.orchestration.sdk_adapter import (
        StructuredOutputError,
        run_one_query,
    )

    bad_result = FakeResultMessage(
        is_error=False,
        structured_output=None,
        result="I could not find any issues, sorry!",
    )
    stream = _fake_stream([FakeAssistantMessage(), bad_result])

    with pytest.raises(StructuredOutputError):
        await run_one_query(message_stream=stream)


@pytest.mark.asyncio
async def test_run_one_query_raises_when_structured_output_missing_and_result_none() -> None:
    """structured_output=None + result=None → fail closed (StructuredOutputError)."""
    from coco_pr_review.orchestration.sdk_adapter import (
        StructuredOutputError,
        run_one_query,
    )

    bad_result = FakeResultMessage(is_error=False, structured_output=None, result=None)
    stream = _fake_stream([FakeAssistantMessage(), bad_result])

    with pytest.raises(StructuredOutputError):
        await run_one_query(message_stream=stream)
