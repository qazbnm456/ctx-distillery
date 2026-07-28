"""`ctx_distillery.trace_io` — the ONE dict-shape guard between a JSONL trace and its consumers.

Hand-rolled lists for `dict_events` (a pure predicate over a list needs no file at all), but REAL
files under `tmp_path` for `load_trace` — reading bytes off disk is plain host-side file I/O, the
same reasoning `tests/test_apply.py` gives for running against real files. The recorder-built
fixtures use `TraceRecorder.record` rather than hand-written JSON so the `run_id`/`step_id`/`type`
envelope is the actual trace/v1 shape rlm-kit writes, not a guess at it; the non-dict lines are then
appended by hand, exactly the "recorder-built, then edited by hand" pattern
`studio/tests/test_app.py::test_replay_of_a_truncated_trace_still_ends_with_a_synthesized_completed`
already established.
"""

from __future__ import annotations

import json

import pytest
from rlm_kit.trace import EVENT_RESULT, TraceRecorder, load_events

from ctx_distillery.trace_io import dict_events, load_trace

#: Every JSON shape that is valid but NOT an object — the exact set `load_events` lets through.
NON_DICT_LINES = ("42", "null", '"x"', "[1, 2, 3]")


def _write_trace(path, *, run_ids=("r0",), extra_lines=()):
    """A real recorder trace (one `run_start` + one `result` per run id), plus raw appended lines."""
    for run_id in run_ids:
        with TraceRecorder(str(path), run_id=run_id, meta={}) as rec:
            rec.record(EVENT_RESULT, {"output": {"candidates": []}})
    if extra_lines:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(extra_lines) + "\n")
    return path


# -- dict_events: the pure predicate --------------------------------------------------------


def test_dict_events_keeps_dicts_and_drops_every_non_dict_json_shape():
    events = [{"a": 1}, 42, None, "x", [1, 2, 3], {"b": 2}]
    assert dict_events(events) == [{"a": 1}, {"b": 2}]  # order preserved


def test_dict_events_of_an_empty_list_is_empty():
    assert dict_events([]) == []


def test_dict_events_keeps_an_all_dict_list_unchanged():
    events = [{"type": "run_start"}, {"type": EVENT_RESULT}]
    assert dict_events(events) == events


# -- load_trace: the read side --------------------------------------------------------------


def test_load_trace_reads_a_real_recorder_trace(tmp_path):
    """Parity with `load_events` on a well-formed file — the guard costs nothing on the happy path."""
    path = _write_trace(tmp_path / "r0.jsonl")
    assert load_trace(str(path)) == load_events(str(path))
    assert [e["type"] for e in load_trace(str(path))] == ["run_start", EVENT_RESULT, "run_end"]


def test_load_trace_drops_non_dict_lines_from_a_real_file(tmp_path):
    path = _write_trace(tmp_path / "weird.jsonl", extra_lines=NON_DICT_LINES)
    raw = load_events(str(path))
    assert [e for e in raw if not isinstance(e, dict)] == [42, None, "x", [1, 2, 3]]  # they ARE there
    assert load_trace(str(path)) == [e for e in raw if isinstance(e, dict)]  # …and never come back


def test_load_trace_filters_by_run_id_without_delegating_to_load_events(tmp_path):
    """THE load-bearing case, and why `load_trace` re-implements the run_id filter rather than
    passing `run_id=` down: `rlm_kit.trace.load_events`'s own filter is `event.get("run_id") ==
    run_id`, an unguarded `.get` on exactly the lines this module exists to drop — so delegating
    would put the `AttributeError` UPSTREAM of our filter, where nothing in `ctx_distillery` could
    reach it. That is stated here rather than asserted against `load_events` directly: the rlm-kit
    pin tracks a branch, so pinning third-party raising behaviour would go red on an upstream fix
    that is a GOOD outcome, not a regression.
    """
    path = _write_trace(tmp_path / "two.jsonl", run_ids=("r0", "r1"), extra_lines=NON_DICT_LINES)
    only_r0 = load_trace(str(path), run_id="r0")
    assert only_r0, "the run really is in the file"
    assert {e["run_id"] for e in only_r0} == {"r0"}
    assert all(isinstance(e, dict) for e in only_r0)
    assert {e["run_id"] for e in load_trace(str(path), run_id="r1")} == {"r1"}


def test_load_trace_of_an_unmatched_run_id_is_empty_not_an_error(tmp_path):
    path = _write_trace(tmp_path / "r0.jsonl", extra_lines=NON_DICT_LINES)
    assert load_trace(str(path), run_id="nobody") == []


def test_load_trace_still_propagates_a_genuinely_unreadable_file(tmp_path):
    """Dropping a bad SHAPE and swallowing a bad FILE are different things: a torn, non-JSON line
    still raises, which is what keeps the studio's 502 guard (and its "never a 500" promise) live."""
    path = tmp_path / "bad.jsonl"
    path.write_text("not json at all\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_trace(str(path))


def test_load_trace_of_a_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        load_trace(str(tmp_path / "does-not-exist.jsonl"))


def test_load_trace_of_a_file_of_only_non_dict_lines_is_empty(tmp_path):
    path = tmp_path / "junk.jsonl"
    path.write_text("\n".join(NON_DICT_LINES) + "\n", encoding="utf-8")
    assert load_trace(str(path)) == []
    assert json.loads("42") == 42  # …and every one of those lines really was valid JSON
