"""`ctx_distillery.trace_io` — the ONE dict-shape guard between a JSONL trace and its consumers.

Hand-rolled lists for `dict_events` (a pure predicate over a list needs no file at all), but REAL
files under `tmp_path` for `load_trace` — reading bytes off disk is plain host-side file I/O, the
same reasoning `tests/test_apply.py` gives for running against real files. The recorder-built
fixtures use `TraceRecorder.record` rather than hand-written JSON so the `run_id`/`step_id`/`type`
envelope is the actual trace/v1 shape rlm-harness writes, not a guess at it; the non-dict lines are then
appended by hand, exactly the "recorder-built, then edited by hand" pattern
`studio/tests/test_app.py::test_replay_of_a_truncated_trace_still_ends_with_a_synthesized_completed`
already established.
"""

from __future__ import annotations

import json

import pytest
from rlm_harness.trace import EVENT_RESULT, TraceRecorder, load_events

from ctx_distillery.trace_io import (
    dict_events,
    load_trace,
    run_start_meta,
    transcript_composition,
    transcript_facts,
)

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
    passing `run_id=` down: `rlm_harness.trace.load_events`'s own filter is `event.get("run_id") ==
    run_id`, an unguarded `.get` on exactly the lines this module exists to drop — so delegating
    would put the `AttributeError` UPSTREAM of our filter, where nothing in `ctx_distillery` could
    reach it. That is stated here rather than asserted against `load_events` directly: the rlm-harness
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


def test_dict_events_coerces_a_non_dict_payload_instead_of_crashing_every_consumer():
    """A well-formed JSON OBJECT with a non-dict `payload` used to 500 the studio. REGRESSION TEST.

    The line filter above was only half the job. Every consumer in this workspace unwraps with
    `(event.get("payload") or {}).get(...)`, which absorbs `None` but NOT a truthy non-dict —
    `("oops" or {})` is `"oops"`, and `.get` on a `str` raises `AttributeError`. So an entry that
    PASSES the dict-shape filter (it really is a JSON object) still crashed `rubric.trace_facts`,
    `schema.assemble` and `GET /v1/runs/{id}/iterations`, and killed the SSE generator on its first
    event so a replay returned an empty stream. Found by an adversarial review of the drawer pass.

    Coerced, not dropped: the envelope (`type`/`step_id`/`ts`/`run_id`) is still true and still
    orders the trace, and a payload-less event is a shape every consumer already handles.
    """
    events = dict_events(
        [
            {"type": "tool_call", "step_id": 1, "payload": "oops"},
            {"type": "tool_call", "step_id": 2, "payload": [1, 2, 3]},
            {"type": "tool_call", "step_id": 3, "payload": 7},
            {"type": "tool_call", "step_id": 4, "payload": None},
            {"type": "main_step", "step_id": 5},  # legitimately payload-less
            {"type": "tool_call", "step_id": 6, "payload": {"tool": "real"}},
        ]
    )
    assert len(events) == 6, "a bad payload must not drop the event — the envelope is still true"
    assert events[0]["payload"] == {} and events[1]["payload"] == {} and events[2]["payload"] == {}
    assert events[3]["payload"] == {}, "None was already absorbed by `or {}`, but normalize it too"
    assert "payload" not in events[4], "an absent payload stays absent, not invented"
    assert events[5]["payload"] == {"tool": "real"}, "a good payload is untouched"
    # The envelope survives coercion — that is the whole reason not to drop the event.
    assert [e["step_id"] for e in events] == [1, 2, 3, 4, 5, 6]


def test_dict_events_coerces_a_non_dict_META_one_level_deeper():
    """The SAME failure, one level down, and the one the payload fix did not reach. REGRESSION TEST.

    `rubric_from_meta` unwraps `payload["meta"].get("rubric")`, so a line whose payload IS a dict but
    whose `meta` is a string sails through both filters above and raises the identical
    `AttributeError: 'str' object has no attribute 'get'`. It cost `GET /v1/runs/{id}` a 500 the
    moment that endpoint began serving the rubric criteria, and it had ALREADY cost
    `ctx-distillery export` an entire bundle for one bad line (reported rather than crashed, but the
    run's data is lost the same way). `meta` is the only payload key in this workspace that is itself
    unwrapped with `.get`, which is why it is normalized by name rather than by recursing.
    """
    events = dict_events(
        [
            {"type": "run_start", "step_id": 0, "payload": {"meta": "nope"}},
            {"type": "run_start", "step_id": 1, "payload": {"meta": [1, 2]}},
            {"type": "run_start", "step_id": 2, "payload": {"meta": None}},
            {"type": "run_start", "step_id": 3, "payload": {"meta": {"planner": "x"}, "other": 1}},
            {"type": "run_start", "step_id": 4, "payload": {"no_meta": True}},
        ]
    )
    assert len(events) == 5, "a bad meta must not drop the event either"
    assert events[0]["payload"]["meta"] == {}
    assert events[1]["payload"]["meta"] == {}
    assert events[2]["payload"]["meta"] == {}
    assert events[3]["payload"] == {"meta": {"planner": "x"}, "other": 1}, "a good meta is untouched"
    assert events[4]["payload"] == {"no_meta": True}, "an absent meta stays absent, not invented"


def test_rubric_from_meta_survives_a_non_dict_meta_end_to_end():
    """The consumer the normalization above exists for — asserted through the REAL function, not
    only against `dict_events`' output, so deleting the coercion cannot stay green here."""
    from ctx_distillery.rubric import rubric_from_meta

    events = [{"type": "run_start", "step_id": 0, "ts": 1.0, "payload": {"meta": "nope"}}]
    assert rubric_from_meta(events).criteria == []


def test_dict_events_does_not_mutate_the_caller_s_events():
    """Coercion returns a new dict — a shared events list must not be rewritten under the caller."""
    original = {"type": "tool_call", "step_id": 1, "payload": "oops"}
    dict_events([original])
    assert original["payload"] == "oops"


# -- run_start_meta / transcript_composition / transcript_facts --------------------------------
# The shared extraction point `rubric._run_start_transcripts` now delegates to, and the guard a
# THIRD consumer (`eval/`'s `score.score_run`) needed — moved here from `studio/`'s `mapper.py`
# (invariant 11). `tests/test_rubric.py`'s own `trace_facts` suite already pins that this refactor
# left `n_transcripts` byte-for-byte unchanged; these tests pin the shared functions directly.


def _run_start(meta):
    return {"type": "run_start", "step_id": 0, "payload": {"meta": meta}}


def test_run_start_meta_finds_the_first_run_start_s_meta():
    events = [_run_start({"transcripts": 3}), {"type": "tool_call", "payload": {}}]
    assert run_start_meta(events) == {"transcripts": 3}


def test_run_start_meta_is_none_without_a_run_start_event_at_all():
    assert run_start_meta([{"type": "tool_call", "payload": {}}]) is None
    assert run_start_meta([]) is None


def test_run_start_meta_of_a_non_dict_meta_is_the_EMPTY_dict_not_none():
    """`dict_events` (called internally, before this function's own scan) already coerces a
    non-dict `payload.meta` to `{}` — the THIRD normalization its own docstring describes. So a
    malformed meta and a genuinely empty one are indistinguishable by the time this function's loop
    ever sees them, and both read as `{}` rather than `None`. That is harmless downstream:
    `transcript_composition({})` and `transcript_facts`'s own `.get("transcripts")` degrade to None
    either way — `None` here is reserved for "no `run_start` at all" / "no `meta` key at all",
    checked above."""
    assert run_start_meta([_run_start("nope")]) == {}


def test_run_start_meta_drops_non_dict_lines_before_scanning():
    """A non-dict line before the real `run_start` must not hide it (and must not raise)."""
    events = [42, None, _run_start({"transcripts": 1})]
    assert run_start_meta(events) == {"transcripts": 1}


def test_transcript_composition_degrades_to_none_never_zero():
    assert transcript_composition(None) == {"sessions": None, "subagents": None}
    assert transcript_composition({}) == {"sessions": None, "subagents": None}
    assert transcript_composition({"transcript_index": "nope"}) == {
        "sessions": None,
        "subagents": None,
    }


def test_transcript_composition_counts_by_kind_and_ignores_junk_entries():
    meta = {
        "transcript_index": [
            {"kind": "session"},
            {"kind": "session"},
            {"kind": "subagent"},
            "junk",  # a non-dict entry is skipped, not counted as either kind
            {"kind": "unknown"},
        ]
    }
    assert transcript_composition(meta) == {"sessions": 2, "subagents": 1}


def test_transcript_facts_combines_the_count_and_the_composition_in_one_scan():
    events = [
        _run_start(
            {
                "transcripts": 3,
                "transcript_index": [
                    {"kind": "session"},
                    {"kind": "session"},
                    {"kind": "subagent"},
                ],
            }
        )
    ]
    assert transcript_facts(events) == {"n_transcripts": 3, "sessions": 2, "subagents": 1}


def test_transcript_facts_degrades_field_by_field_on_an_old_or_malformed_trace():
    assert transcript_facts([]) == {"n_transcripts": None, "sessions": None, "subagents": None}
    assert transcript_facts([_run_start({"transcripts": "two"})]) == {
        "n_transcripts": None,
        "sessions": None,
        "subagents": None,
    }
    # `n_transcripts` known, composition absent: a trace recorded before subagent ingestion shipped.
    assert transcript_facts([_run_start({"transcripts": 5})]) == {
        "n_transcripts": 5,
        "sessions": None,
        "subagents": None,
    }
