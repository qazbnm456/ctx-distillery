"""`read_transcript_chunk` — a bounds-checked, AUDITED window onto one transcript.

Why this tool exists at all, since the transcripts are ALREADY a REPL variable the planner can
slice in its own code: **auditability**. `docs/DESIGN.md` binds raw transcript text as an RLM INPUT
field (that unbounded-text-as-a-REPL-variable fit is the whole reason RLM suits this project), so
this tool is not a security boundary — the model has the full text either way. What it adds is a
recorded `tool_call` naming exactly which transcript, offset, and length the planner actually
examined, which is what lets a human later check "did the plan's cross-conversation overlap claim
really look at both transcripts?" instead of trusting the planner's own narration.

The list this closes over is the SAME redacted list `.arun(transcripts=...)` binds — see
`session.run_distillation`, which builds one redacted list and passes it to both. That single-list
construction is what makes "redaction happens before any LM exposure" a property of the code rather
than a claim.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from rlm_kit.trace import record_tool_call

#: Default window size, and the hard cap on one read. Large enough to read a real conversation turn
#: or two, small enough that a scan is visible in the trace as a sequence of deliberate reads.
DEFAULT_LIMIT = 4000
MAX_LIMIT = 20_000


def make_read_transcript_chunk_tool(
    transcripts: Sequence[str],
) -> Callable[[int, int, int], dict | str]:
    """Build `read_transcript_chunk(transcript_index, offset=0, limit=DEFAULT_LIMIT)`.

    Returns a dict on success and a short string on refusal — the same two-shape convention the
    sibling consumer's tools use, so the planner reads a failure as text instead of an exception.
    Never raises `IndexError`/`ValueError` into the REPL.
    """
    texts = [t if isinstance(t, str) else str(t) for t in transcripts]

    def read_transcript_chunk(
        transcript_index: int, offset: int = 0, limit: int = DEFAULT_LIMIT
    ) -> dict | str:
        """Read a window of one transcript's text, recording which window you read.

        ``transcript_index`` selects the transcript (0-based, in the order they were provided);
        ``offset`` is a character offset; ``limit`` caps the returned characters. Returns a
        ``{"transcript_index", "offset", "length", "text", "total_length"}`` dict, or a short
        explanatory string if the request is out of range."""
        if not isinstance(transcript_index, int) or isinstance(transcript_index, bool):
            note = f"refused: transcript_index must be an int, got {transcript_index!r}"
            record_tool_call(
                "read_transcript_chunk",
                args={"transcript_index": transcript_index, "offset": offset, "limit": limit},
                ok=False,
                note=note,
            )
            return note
        if not texts:
            note = "refused: this run has no transcripts"
            record_tool_call(
                "read_transcript_chunk",
                args={"transcript_index": transcript_index, "offset": offset, "limit": limit},
                ok=False,
                note=note,
            )
            return note
        if transcript_index < 0 or transcript_index >= len(texts):
            note = (
                f"refused: transcript_index {transcript_index} out of range "
                f"(this run has {len(texts)} transcript(s), valid 0..{len(texts) - 1})"
            )
            record_tool_call(
                "read_transcript_chunk",
                args={"transcript_index": transcript_index, "offset": offset, "limit": limit},
                ok=False,
                note=note,
            )
            return note
        text = texts[transcript_index]
        total = len(text)
        try:
            start = int(offset)
            window = int(limit)
        except (TypeError, ValueError):
            note = f"refused: offset/limit must be ints, got offset={offset!r} limit={limit!r}"
            record_tool_call(
                "read_transcript_chunk",
                args={"transcript_index": transcript_index, "offset": offset, "limit": limit},
                ok=False,
                note=note,
            )
            return note
        if start < 0 or window <= 0:
            note = f"refused: need offset >= 0 and limit > 0, got offset={start} limit={window}"
            record_tool_call(
                "read_transcript_chunk",
                args={"transcript_index": transcript_index, "offset": start, "limit": window},
                ok=False,
                note=note,
            )
            return note
        if start >= total:
            note = (
                f"refused: offset {start} is past the end of transcript {transcript_index} "
                f"(total_length {total})"
            )
            record_tool_call(
                "read_transcript_chunk",
                args={"transcript_index": transcript_index, "offset": start, "limit": window},
                ok=False,
                note=note,
                total_length=total,
            )
            return note
        window = min(window, MAX_LIMIT)
        chunk = text[start:start + window]
        # Record WHICH window was read — never the text itself (that is the audit point).
        record_tool_call(
            "read_transcript_chunk",
            args={"transcript_index": transcript_index, "offset": start, "limit": window},
            ok=True,
            length=len(chunk),
            total_length=total,
        )
        return {
            "transcript_index": transcript_index,
            "offset": start,
            "length": len(chunk),
            "text": chunk,
            "total_length": total,
        }

    return read_transcript_chunk
