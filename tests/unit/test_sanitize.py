"""Tests for `coco_pr_review.sanitize` — secret redaction before posting."""
from __future__ import annotations


def test_redact_redacts_aws_access_key_id() -> None:
    """Tracer bullet: an AWS access key is redacted."""
    from coco_pr_review.sanitize import redact

    text = "Found credential: AKIAIOSFODNN7EXAMPLE in src/config.py"
    out = redact(text)

    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED]" in out


def test_redact_redacts_github_classic_pat() -> None:
    """Classic GitHub PATs start with `ghp_` and are 40 chars total (`ghp_` + 36)."""
    from coco_pr_review.sanitize import redact

    pat = "ghp_" + "a1B2c3D4e5F6g7H8i9J0kLmNoPqRsTuVwXyZ"  # 4 + 36 = 40
    text = f"My token is {pat} please don't leak it"
    out = redact(text)

    assert pat not in out
    assert "[REDACTED]" in out


def test_redact_redacts_jwt_shaped_tokens() -> None:
    """JWTs are 3 base64url-encoded segments separated by dots; header always starts `eyJ`."""
    from coco_pr_review.sanitize import redact

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    out = redact(f"Authorization: Bearer {jwt}")

    assert jwt not in out
    assert "[REDACTED]" in out


def test_redact_redacts_github_finegrained_pat() -> None:
    """Fine-grained PATs use `github_pat_` prefix + 82 char body (different format than classic)."""
    from coco_pr_review.sanitize import redact

    pat = "github_pat_" + "11AVHG4SI" + "0" * 73  # 11 + 9 + 73 = 93 — pad to >= 82 body chars
    out = redact(f"token {pat} suffix")

    assert pat not in out
    assert "[REDACTED]" in out


def test_redact_leaves_plain_text_untouched() -> None:
    """No false positives on long alphanumeric strings (commit SHAs, URLs, hashes)."""
    from coco_pr_review.sanitize import redact

    text = "Build 7f8a9b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7 of branch main is broken; see https://github.com/owner/repo/commit/abc123."
    assert redact(text) == text


def test_redact_redacts_multiple_secrets_in_one_string() -> None:
    """All matches are scrubbed, not just the first."""
    from coco_pr_review.sanitize import redact

    text = "Both secrets: AKIAIOSFODNN7EXAMPLE and ghp_a1B2c3D4e5F6g7H8i9J0kLmNoPqRsTuVwXyZ"
    out = redact(text)

    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "ghp_a1B2c3D4e5F6g7H8i9J0kLmNoPqRsTuVwXyZ" not in out
    assert out.count("[REDACTED]") == 2


def test_redact_applies_extra_patterns() -> None:
    """Consumer-supplied extra regex patterns are applied additively."""
    from coco_pr_review.sanitize import redact

    text = "Internal secret token: SF-INT-abcdef123456 in config"
    out = redact(text, extra_patterns=[r"SF-INT-[a-z0-9]+"])

    assert "SF-INT-abcdef123456" not in out
    assert "[REDACTED]" in out


def test_redact_redacts_secret_envvar_assignments() -> None:
    """Sensitive-named env-vars (`API_KEY=...`, `*_TOKEN=...`, `*_SECRET=...`, `PASSWORD=...`) get redacted.

    Conservative: only redacts when the key signals secrecy. Innocent vars like
    `PATH=/usr/bin` or `LANG=en_US` are NOT redacted (avoiding false positives).
    """
    from coco_pr_review.sanitize import redact

    leaky = (
        "Found in config file:\n"
        "API_KEY=sk_live_abc123def456ghi789\n"
        "DATABASE_PASSWORD=super-secret-pw\n"
        "PATH=/usr/local/bin\n"
        "LANG=en_US.UTF-8\n"
    )
    out = redact(leaky)

    assert "sk_live_abc123def456ghi789" not in out
    assert "super-secret-pw" not in out
    assert "/usr/local/bin" in out  # not redacted
    assert "en_US.UTF-8" in out      # not redacted
