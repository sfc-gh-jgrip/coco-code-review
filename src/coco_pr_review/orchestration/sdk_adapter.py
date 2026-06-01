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
import logging
import re
from typing import Any, AsyncIterator

from coco_pr_review.retry import classify_sdk_error

logger = logging.getLogger(__name__)

# Matches a fenced code block, optionally tagged ```json. The body is captured
# lazily so the FIRST complete block wins.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(raw: str) -> Any:
    """Best-effort parse of a JSON value from possibly fenced or prefixed text.

    Cortex models frequently wrap structured output in a Markdown ``` ```json ```
    fence, sometimes after a prose preamble, so a bare ``json.loads`` fails. This
    tries, in order:

      1. Direct ``json.loads`` of the stripped text.
      2. The first ``` ```json ``` (or bare ```` ``` ````) fenced block that parses.
      3. The outermost ``{ ... }`` substring.

    Raises ``json.JSONDecodeError`` when no candidate parses.
    """
    text = raw.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    for match in _FENCE_RE.finditer(text):
        candidate = match.group(1).strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise json.JSONDecodeError("no JSON object found in result text", text, 0)


def _read_paths_from_message(msg: Any) -> list[str]:
    """Extract file paths opened by ``Read`` tool calls in one stream message.

    Reviewer/verifier agents pull file context via the ``Read`` tool. Each such
    call appears as a ``tool_use`` content block on an ``AssistantMessage`` with
    ``input.file_path``. We harvest those paths so the orchestrator can report
    how much context the reviewers actually read (vs. working from the diff
    alone). Best-effort and defensive: blocks may be objects or dicts, and any
    unexpected shape is skipped silently — this is observability, never a gate.
    """
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return []
    paths: list[str] = []
    for block in content:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        if block_type != "tool_use" or name != "Read":
            continue
        tool_input = getattr(block, "input", None) or (
            block.get("input") if isinstance(block, dict) else None
        )
        if isinstance(tool_input, dict):
            file_path = tool_input.get("file_path")
            if isinstance(file_path, str) and file_path:
                paths.append(file_path)
    return paths


# ---------------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------------


class TransientSdkError(Exception):
    """Retryable SDK failure — network hiccup, rate limit, timeout."""


class HardSdkError(Exception):
    """Non-retryable SDK failure — auth, billing, bad schema."""


class StructuredOutputError(Exception):
    """The terminal ResultMessage carried no usable structured output.

    Raised when ``structured_output`` is absent AND the plaintext result cannot
    be recovered as JSON. This is a fail-closed signal: callers MUST treat it as
    "the model's output could not be parsed", never as "the model found nothing"
    (an empty-but-valid result). Conflating the two silently hides a clean PR
    behind an infrastructure failure (or vice versa).
    """


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
        from ``extract_json(result.result)`` if structured_output is None.
        Raises ``StructuredOutputError`` if both paths fail (fail-closed — a
        parse failure is NEVER silently treated as zero findings).
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
    StructuredOutputError
        When the result carries no usable structured output and the plaintext
        result cannot be recovered as JSON (fail-closed).
    """
    result_message = None
    files_read: list[str] = []

    async for msg in message_stream:
        # Mid-stream assistant message with an error field.
        if hasattr(msg, "error") and msg.error is not None:
            _raise_classified(msg.error)

        # Harvest any Read tool-call file paths for context observability.
        files_read.extend(_read_paths_from_message(msg))

        # Terminal result message.
        if hasattr(msg, "is_error"):
            if msg.is_error:
                subtype = getattr(msg, "subtype", "unknown") or "unknown"
                detail = getattr(msg, "result", None)
                if not detail:
                    # The result text is often empty for execution errors; fall
                    # back to the richer fields so the real cause is visible.
                    detail = (
                        f"stop_reason={getattr(msg, 'stop_reason', None)} "
                        f"permission_denials={getattr(msg, 'permission_denials', None)} "
                        f"num_turns={getattr(msg, 'num_turns', None)} "
                        f"duration_ms={getattr(msg, 'duration_ms', None)}"
                    )
                _raise_classified(subtype, detail)
            # Success terminal message.
            result_message = msg

    if result_message is None:
        # Stream ended without a result — treat as transient.
        raise TransientSdkError("stream_ended_without_result")

    # Extract structured output with fallback chain.
    output = getattr(result_message, "structured_output", None)
    if output is None:
        # Fallback: try parsing the plaintext result as JSON. This is a DEGRADED
        # path — schema enforcement did not populate structured_output, so we are
        # recovering best-effort from fenced/prose text.
        raw = getattr(result_message, "result", None)
        if raw is not None:
            try:
                output = extract_json(raw)
                logger.warning(
                    "degraded: recovered JSON from plaintext result because schema "
                    "enforcement did not populate structured_output "
                    "(fence/prose tolerant, raw_len=%d).",
                    len(raw) if isinstance(raw, str) else -1,
                )
            except (json.JSONDecodeError, TypeError) as exc:
                # Fail closed: an unparseable result is NOT "zero findings".
                preview = raw[:500] if isinstance(raw, str) else repr(raw)[:500]
                logger.error(
                    "structured_output missing AND plaintext result is not valid JSON; "
                    "failing closed (no silent zero-findings). raw_result_preview=%r",
                    preview,
                )
                raise StructuredOutputError(
                    f"unparseable result text (preview={preview!r})"
                ) from exc
        else:
            logger.error(
                "structured_output missing and result text is None; failing closed "
                "(stop_reason=%s num_turns=%s).",
                getattr(result_message, "stop_reason", None),
                getattr(result_message, "num_turns", None),
            )
            raise StructuredOutputError(
                "structured_output missing and result text is None"
            )

    # Attach the distinct set of files the agent Read during this query so the
    # orchestrator can report context breadth. Best-effort: if the SDK message
    # object forbids attribute assignment, skip silently (observability only).
    try:
        result_message.files_read = sorted(set(files_read))
    except (AttributeError, TypeError):
        pass

    return (output, result_message)
