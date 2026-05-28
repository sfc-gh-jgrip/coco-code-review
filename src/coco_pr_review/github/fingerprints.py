"""Finding fingerprints and the GitHub comment-marker parser.

Each inline review comment we post embeds a fingerprint marker in its body so
that on subsequent runs we can identify and dedupe net-new findings vs. ones
we've already posted (preserving user replies on threads we already created).

Marker format inside a GitHub comment body:

    <!-- coco-pr-review:fp=<sha256-hex> -->

The fingerprint is computed from (file, start_line, end_line, title, evidence)
joined by ``|``. We deliberately exclude ``comment`` and ``suggested_fix`` so
that small re-phrasings don't generate duplicate posts.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Protocol

_MARKER_RE = re.compile(r"<!--\s*coco-pr-review:fp=([0-9a-f]{64})\s*-->")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    """Strip whitespace and convert CRLF to LF before fingerprint hashing."""
    return s.replace("\r\n", "\n").strip()


# ---------------------------------------------------------------------------
# Fingerprint computation
# ---------------------------------------------------------------------------


def fingerprint(
    *,
    file: str,
    start_line: int,
    end_line: int,
    title: str,
    evidence: str,
) -> str:
    """SHA256 hex digest of the canonical fingerprint key.

    Each string component is normalized (strip + CRLF→LF) before joining.
    """
    key = (
        f"{_normalize(file)}|{start_line}-{end_line}"
        f"|{_normalize(title)}|{_normalize(evidence)}"
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# Alias matching the naming convention used by publisher and tests.
hash_finding_fingerprint = fingerprint


# ---------------------------------------------------------------------------
# Marker formatting and parsing
# ---------------------------------------------------------------------------


def format_fingerprint_marker(hex_str: str) -> str:
    """Return the HTML comment marker embedding the given fingerprint hex."""
    return f"<!-- coco-pr-review:fp={hex_str} -->"


def parse_marker(comment_body: str) -> str | None:
    """Extract the fingerprint hash from a GitHub comment body, or None if absent."""
    match = _MARKER_RE.search(comment_body)
    return match.group(1) if match else None


# Alias for the longer, more descriptive name used in newer code.
parse_fingerprint_from_comment = parse_marker


# ---------------------------------------------------------------------------
# Bot-login-aware fingerprint collection
# ---------------------------------------------------------------------------


class _CommentLike(Protocol):
    """Minimal interface for a GitHub comment object (PullRequestComment)."""

    @property
    def body(self) -> str: ...

    @property
    def user(self) -> "_UserLike": ...


class _UserLike(Protocol):
    @property
    def login(self) -> str: ...


def collect_existing_fingerprints(
    comments: Iterable[_CommentLike],
    bot_login: str,
) -> set[str]:
    """Parse fingerprint markers from bot-authored comments only.

    Non-bot comments are excluded so that a user who quote-replies a bot
    comment (copying the marker) does not inadvertently suppress a legitimate
    finding on re-run.
    """
    seen: set[str] = set()
    for comment in comments:
        if comment.user.login != bot_login:
            continue
        fp = parse_marker(comment.body)
        if fp is not None:
            seen.add(fp)
    return seen
