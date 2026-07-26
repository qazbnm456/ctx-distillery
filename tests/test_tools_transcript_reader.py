"""`read_transcript_chunk` — bounds checking (never an IndexError into the REPL) + the audit trail."""

from __future__ import annotations

import pytest
from rlm_kit.testing import assert_repl_safe
from rlm_kit.trace import EVENT_TOOL_CALL, TraceRecorder, load_events

from ctx_distillery.tools.transcript_reader import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    make_read_transcript_chunk_tool,
)

_A = "".join(f"line {i}\n" for i in range(200))
_B = "short second transcript\n"


@pytest.fixture
def tool():
    return make_read_transcript_chunk_tool([_A, _B])


def _payloads(path):
    return [
        e["payload"]
        for e in load_events(path)
        if e["type"] == EVENT_TOOL_CALL and e["payload"].get("tool") == "read_transcript_chunk"
    ]


def test_repl_safe(tool):
    assert_repl_safe(tool)


def test_reads_a_window_and_reports_the_totals(tool):
    out = tool(0, 0, 20)
    assert out["text"] == _A[:20]
    assert out["transcript_index"] == 0 and out["offset"] == 0
    assert out["length"] == 20 and out["total_length"] == len(_A)


def test_default_limit_applies_when_omitted(tool):
    out = tool(1)
    assert out["length"] == len(_B)          # shorter than the default window
    assert DEFAULT_LIMIT > len(_B)


def test_a_window_past_the_end_returns_what_exists(tool):
    out = tool(1, len(_B) - 5, 999)
    assert out["text"] == _B[-5:] and out["length"] == 5


def test_limit_is_capped(tool):
    out = tool(0, 0, MAX_LIMIT * 10)
    assert out["length"] == len(_A)          # the whole (shorter) transcript, no error


@pytest.mark.parametrize(
    "args, fragment",
    [
        ((2, 0, 10), "out of range"),
        ((-1, 0, 10), "out of range"),
        ((0, -5, 10), "offset >= 0"),
        ((0, 0, 0), "limit > 0"),
        ((0, 0, -3), "limit > 0"),
        ((0, 10_000_000, 10), "past the end"),
        (("nope", 0, 10), "must be an int"),
        ((True, 0, 10), "must be an int"),
    ],
)
def test_out_of_range_requests_are_refused_as_text(tool, args, fragment):
    out = tool(*args)
    assert isinstance(out, str) and out.startswith("refused:")
    assert fragment in out


def test_non_int_offset_or_limit_is_refused(tool):
    assert tool(0, "x", 10).startswith("refused:")   # type: ignore[arg-type]
    assert tool(0, 0, "x").startswith("refused:")    # type: ignore[arg-type]


def test_no_transcripts_is_refused_not_an_index_error():
    empty = make_read_transcript_chunk_tool([])
    assert empty(0).startswith("refused: this run has no transcripts")


def test_the_trace_records_which_window_was_read_but_not_the_text(tmp_path, tool):
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        tool(0, 30, 40)
        tool(9, 0, 10)
    read, refused = _payloads(trace)
    assert read["ok"] is True
    assert read["args"] == {"transcript_index": 0, "offset": 30, "limit": 40}
    assert read["length"] == 40 and read["total_length"] == len(_A)
    assert "line 5" not in str(read)          # the window, never the text
    assert refused["ok"] is False and "out of range" in refused["note"]


def test_the_tool_holds_a_snapshot_of_the_transcript_list():
    texts = ["one", "two"]
    tool = make_read_transcript_chunk_tool(texts)
    texts.clear()
    assert tool(1)["text"] == "two"
