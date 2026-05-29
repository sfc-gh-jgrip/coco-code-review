"""SDK adapter — wraps Cortex Code Agent SDK message streams.

Provides `run_one_query` which iterates an async message stream, classifies
errors into transient (retry-worthy) vs. hard (propagate immediately), and
extracts structured output from the terminal ResultMessage.

Error classification uses the same subtypes as `coco_pr_review.retry`:
  transient: rate_limit, server_error, unknown
  hard:      billing_error, authentication_failed, invalid_request
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from coco_pr_review.retry import classify_sdk_error


# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class TransientSdkError(Exception):
    """Retryable SDK failure — network hiccup, rate limit, timeout."""


class HardSdkError(Exception):
    """Non-retryable SDK failure — auth, billing, bad schema."""


# ---------------------------------------------------------------------------
# Error classification helper
# ---------------------------------------------------------------------------


def _raise_classified(subtype: str, detail: str | None = None) -> None:
    """Raise the appropriate exception type for an SDK error subtype.

    ``detail`` carries the SDK's human-readable error text (e.g. the
    ResultMessage ``result`` field) so failures surface a real cause instead of
    only the opaque subtype.
    """
    message = f"{subtype}: {detail}" if detail else subtype
    classification = classify_sdk_error(subtype)
    if classification == "hard":
        raise HardSdkError(message)
    # Default to transient — safer to retry than to abort.
    raise TransientSdkError(message)


# ---------------------------------------------------------------------------
# Core adapter
# ---------------------------------------------------------------------------


async def run_one_query(
    *,
    message_stream: AsyncIterator[Any],
) -> tuple[Any, Any]:
    """Consume an SDK message stream and return (structured_output, result_message).

    Iterates the async stream of messages from a ``query()`` call.  When a
    message with ``is_error=True`` or ``error`` attribute is encountered, the
    error is classified and the appropriate exception is raised.

    On success (the terminal ResultMessage), returns a tuple of:
      - ``structured_output``: parsed JSON from the result, or the fallback
        from ``json.loads(result.result)`` if structured_output is None.
        Returns ``{}`` if both paths fail (soft-fail to zero findings).
      - The ResultMessage itself (callers read ``.total_cost_usd``,
        ``.num_turns``, etc.)

    Parameters
    ----------
    message_stream : AsyncIterator
        The async iterable returned by ``query()``.  Each element is either
        an AssistantMessage (mid-stream) or a ResultMessage (terminal).

    Returns
    -------
    tuple[Any, ResultMessage]
        (parsed_output, result_message)

    Raises
    ------
    TransientSdkError
        On rate-limit, server error, or unknown transient failures.
    HardSdkError
        On billing errors, auth failures, or invalid requests.
    """
    result_message = None

    async for msg in message_stream:
        # Mid-stream assistant message with an error field.
        if hasattr(msg, "error") and msg.error is not None:
            _raise_classified(msg.error)

        # Terminal result message.
        if hasattr(msg, "is_error"):
            if msg.is_error:
                subtype = getattr(msg, "subtype", "unknown") or "unknown"
                detail = getattr(msg, "result", None)
                _raise_classified(subtype, detail)
            # Success terminal message.
            result_message = msg

    if result_message is None:
        # Stream ended without a result — treat as transient.
        raise TransientSdkError("stream_ended_without_result")

    # Extract structured output with fallback chain.
    output = getattr(result_message, "structured_output", None)
    if output is None:
        # Fallback: try parsing the plaintext result as JSON.
        raw = getattr(result_message, "result", None)
        if raw is not None:
            try:
                output = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                # Soft-fail: return empty dict → zero findings downstream.
                output = {}
        else:
            output = {}

    return (output, result_message)
