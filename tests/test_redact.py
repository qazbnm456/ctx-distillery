"""`redact_transcript` — pattern-based, best-effort, and BEFORE any LM exposure.

Table-driven per secret shape. The honest-scope point (this is not complete secret detection) is
documented in the module, not asserted here; what IS asserted is that each shape it claims to catch
is really removed, that a labelled placeholder replaces it, and that benign prose is untouched.
"""

import pytest

from ctx_distillery.redact import redact_all, redact_transcript

_KEY = "sk-abcdefghijklmnopqrstuvwx1234567890"


@pytest.mark.parametrize(
    "secret, label",
    [
        (_KEY, "api_key"),
        ("sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123", "api_key"),
        ("AKIAIOSFODNN7EXAMPLE", "aws_access_key_id"),
        ("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "github_token"),
        ("AIza" + "B" * 35, "google_api_key"),
    ],
)
def test_each_shape_is_removed_and_labelled(secret, label):
    out = redact_transcript(f"the user pasted {secret} into the chat")
    assert secret not in out
    assert f"[REDACTED:{label}]" in out


def test_private_key_block_is_removed_whole():
    text = (
        "here it is:\n-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA1234\nabcd\n-----END RSA PRIVATE KEY-----\nthanks"
    )
    out = redact_transcript(text)
    assert "MIIEowIBAAKCAQEA1234" not in out
    assert "BEGIN RSA PRIVATE KEY" not in out
    assert out == "here it is:\n[REDACTED:private_key]\nthanks"


def test_bearer_token_in_an_authorization_header():
    out = redact_transcript("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\n")
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in out
    assert "[REDACTED:bearer_token]" in out
    assert out.startswith("Authorization: ")  # the header NAME survives — the audit trail stays


@pytest.mark.parametrize(
    "line",
    [
        'password = "hunter2000"',
        "api_key: s3cr3t-value-here",
        "ACCESS_TOKEN=abcdef123456",
    ],
)
def test_secret_assignments_lose_only_the_value(line):
    out = redact_transcript(line)
    assert "[REDACTED:secret_assignment]" in out
    for token in ("hunter2000", "s3cr3t-value-here", "abcdef123456"):
        assert token not in out


def test_benign_transcript_is_untouched():
    text = (
        "user: can you refactor the parser?\n"
        "assistant: sure — I'll split tokenize() out of parse() first.\n"
        "user: also the CI job on 3.13 is red.\n"
    )
    assert redact_transcript(text) == text


def test_redaction_is_stable_under_a_second_pass():
    once = redact_transcript(f"key {_KEY} here")
    assert redact_transcript(once) == once


def test_redact_all_maps_the_whole_list():
    out = redact_all([f"a {_KEY}", "b plain"])
    assert _KEY not in out[0] and out[1] == "b plain"


def test_a_non_string_entry_degrades_instead_of_raising():
    assert redact_transcript(None) == "None"  # type: ignore[arg-type]
