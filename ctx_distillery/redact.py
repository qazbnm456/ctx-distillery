"""Host-side transcript redaction — runs BEFORE any transcript text becomes LM context.

`CLAUDE.md` invariant (3): sensitive transcript content is redacted in the ingestion layer, not
left to the planner's judgement. `session.run_distillation` calls `redact_transcript` on every
transcript immediately after the adapter's single `ingest()`, and the redacted list is the ONLY one
threaded into `DistillSession` (both the constructor's `read_transcript_chunk` closure and the
`.arun(transcripts=...)` signature binding) — so there is no path left that hands the model raw text.

HONEST SCOPE: this is pattern-based and BEST-EFFORT, not a claim of complete secret detection. It
catches common credential SHAPES (see `_PATTERNS`); it cannot catch a secret that looks like prose,
a novel token format, or a password the user typed inline. A human should still eyeball a transcript
before sharing a plan widely. Matches are replaced by a LABELLED placeholder (`[REDACTED:api_key]`)
rather than deleted, so the plan can still say "a credential appeared here" without carrying it.
"""

from __future__ import annotations

import re

# (label, compiled pattern). Order matters only where one shape could nest inside another; the
# private-key block is listed first so its body is consumed whole rather than piecemeal.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # `Authorization: Bearer <token>` / `authorization: token <token>` header values.
    (
        "bearer_token",
        re.compile(
            r"(?i)(?<=authorization:\s)(?:bearer|token)\s+[A-Za-z0-9\-._~+/]{8,}=*",
        ),
    ),
    # Provider API keys: an `sk-`/`sk-ant-`/`pk-` prefix plus a long opaque tail.
    ("api_key", re.compile(r"\b(?:sk|pk)-(?:[A-Za-z0-9]+-)?[A-Za-z0-9_\-]{20,}")),
    # AWS access key ids.
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    # GitHub personal-access / app tokens.
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    # Google API keys.
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    # An explicit `password = "..."` / `api_key: '...'` assignment — the value only.
    (
        "secret_assignment",   # == _ASSIGNMENT_LABEL, defined below (its replacement is special)
        re.compile(
            r"(?i)(?<=\b)(?P<key>password|passwd|secret|api[_-]?key|access[_-]?token)"
            r"(?P<sep>\s*[:=]\s*)(?P<quote>[\"']?)(?P<value>[^\s\"',;]{6,})(?P=quote)"
        ),
    ),
]


#: The one pattern whose replacement keeps surrounding text (the `key = ` part), so a reviewer can
#: still see WHAT was set without seeing the value.
_ASSIGNMENT_LABEL = "secret_assignment"


def _placeholder(label: str) -> str:
    return f"[REDACTED:{label}]"


def _replace_assignment(match: re.Match[str]) -> str:
    """Keep `key`, the separator, and the quoting; replace only the value."""
    quote = match.group("quote")
    return (
        f"{match.group('key')}{match.group('sep')}{quote}"
        f"{_placeholder(_ASSIGNMENT_LABEL)}{quote}"
    )


def redact_transcript(text: str) -> str:
    """Return `text` with common secret shapes replaced by `[REDACTED:<label>]` placeholders.

    Deterministic and idempotent-ish: a placeholder contains no pattern that would re-match, so
    redacting twice yields the same string. Never raises — a non-string input is coerced with
    `str()` so an odd transcript entry degrades instead of breaking ingestion.
    """
    out = text if isinstance(text, str) else str(text)
    for label, pattern in _PATTERNS:
        if label == _ASSIGNMENT_LABEL:
            out = pattern.sub(_replace_assignment, out)
        else:
            out = pattern.sub(_placeholder(label), out)
    return out


def redact_all(texts: list[str]) -> list[str]:
    """Redact a whole transcript list — the shape `run_distillation` needs."""
    return [redact_transcript(t) for t in texts]
