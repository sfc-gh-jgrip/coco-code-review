"""Tests for `coco_pr_review.retry` — exponential backoff for SDK transient errors."""
from __future__ import annotations

import pytest


class _TransientError(Exception):
    """Stand-in for a wrapped transient SDK error."""


class _HardError(Exception):
    """Stand-in for a wrapped hard SDK error."""


def test_classify_sdk_error_returns_transient_for_rate_limit() -> None:
    """Tracer bullet: rate_limit is the canonical transient SDK error class."""
    from coco_pr_review.retry import classify_sdk_error

    assert classify_sdk_error("rate_limit") == "transient"


def test_classify_sdk_error_returns_hard_for_billing_error() -> None:
    """Billing errors won't recover from retry — must escalate to human."""
    from coco_pr_review.retry import classify_sdk_error

    assert classify_sdk_error("billing_error") == "hard"
    assert classify_sdk_error("authentication_failed") == "hard"
    assert classify_sdk_error("invalid_request") == "hard"


@pytest.mark.asyncio
async def test_retry_succeeds_after_one_transient_failure(monkeypatch) -> None:
    """Decorator catches the first TransientError, sleeps, retries, returns success."""
    from coco_pr_review import retry as retry_mod

    # Suppress real sleeping so tests are instant
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(retry_mod.asyncio, "sleep", fake_sleep)

    attempts = 0

    @retry_mod.retry_on_transient(transient_exc=_TransientError)
    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _TransientError("rate_limit")
        return "ok"

    result = await flaky()

    assert result == "ok"
    assert attempts == 2
    assert sleeps == [1.0]  # one sleep at the first delay


@pytest.mark.asyncio
async def test_retry_does_not_retry_hard_errors(monkeypatch) -> None:
    """A non-transient exception type propagates immediately — no sleep, no retry."""
    from coco_pr_review import retry as retry_mod

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(retry_mod.asyncio, "sleep", fake_sleep)

    attempts = 0

    @retry_mod.retry_on_transient(transient_exc=_TransientError)
    async def hard_fail() -> None:
        nonlocal attempts
        attempts += 1
        raise _HardError("billing_error")

    with pytest.raises(_HardError):
        await hard_fail()

    assert attempts == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_retry_re_raises_after_exhausting_attempts(monkeypatch) -> None:
    """After `delays` exhausted, the last transient exception is re-raised."""
    from coco_pr_review import retry as retry_mod

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(retry_mod.asyncio, "sleep", fake_sleep)

    attempts = 0

    @retry_mod.retry_on_transient(transient_exc=_TransientError, delays=(1.0, 3.0, 9.0))
    async def always_flaky() -> None:
        nonlocal attempts
        attempts += 1
        raise _TransientError(f"attempt {attempts}")

    with pytest.raises(_TransientError, match="attempt 4"):
        await always_flaky()

    # 1 initial + 3 retries = 4 total attempts; 3 sleeps with the configured backoff.
    assert attempts == 4
    assert sleeps == [1.0, 3.0, 9.0]
