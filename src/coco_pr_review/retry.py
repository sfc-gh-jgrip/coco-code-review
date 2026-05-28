"""Retry-with-backoff for transient Cortex SDK errors.

The SDK reports errors via `AssistantMessage.error` (a string subtype) and
`ResultMessage.is_error` + `subtype`. We classify these into transient
(retry-worthy) and hard (re-raise immediately) and provide a decorator that
wraps async coroutines.

Error classification (sourced from `cortex_code_agent_sdk.types.AssistantMessageError`):
  transient: rate_limit, server_error, unknown
  hard:      billing_error, authentication_failed, invalid_request

Hard errors won't recover from a retry; they need human intervention (top up
credits, fix auth, fix request shape). Retrying just burns budget and time.
"""
from __future__ import annotations

import asyncio
import functools
from typing import Awaitable, Callable, Literal, ParamSpec, TypeVar

ErrorClass = Literal["transient", "hard"]

_TRANSIENT = {"rate_limit", "server_error", "unknown"}
_HARD = {"billing_error", "authentication_failed", "invalid_request"}

DEFAULT_DELAYS = (1.0, 3.0, 9.0)

P = ParamSpec("P")
R = TypeVar("R")


def classify_sdk_error(subtype: str) -> ErrorClass:
    """Classify an SDK error subtype string as `transient` or `hard`."""
    if subtype in _TRANSIENT:
        return "transient"
    if subtype in _HARD:
        return "hard"
    # Default to transient — it's safer to retry an unknown error than to
    # explode on a recoverable one. The classifier widens over time as we
    # observe new subtypes in the wild.
    return "transient"


def retry_on_transient(
    *,
    transient_exc: type[BaseException],
    delays: tuple[float, ...] = DEFAULT_DELAYS,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Decorator: retry the wrapped async fn when it raises `transient_exc`.

    Sleeps `delays[i]` seconds before attempt `i+2`. Total attempts =
    `len(delays) + 1`. Hard errors (any other exception type) are NOT retried —
    they propagate immediately. After exhausting `delays`, the last
    `transient_exc` is re-raised.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_exc: BaseException | None = None
            for attempt_idx, delay in enumerate((*delays, None)):
                try:
                    return await fn(*args, **kwargs)
                except transient_exc as exc:
                    last_exc = exc
                    if delay is None:
                        # Final attempt failed — re-raise.
                        break
                    await asyncio.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
