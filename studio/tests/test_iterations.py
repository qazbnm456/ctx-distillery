"""`build_iterations` — the Trajectory drawer's data layer, and its endpoint.

Hand-rolled event dicts here are fine (the same licence `test_mapper.py` takes): `build_iterations`
is a pure function over `{type, ts, step_id, payload}` shapes already pinned by rlm-kit's trace/v1
contract, and every tool payload below is transcribed from the REAL recorded shapes — a live run of
the product against a real model in a real pyodide sandbox, plus the `record_tool_call(...)` calls in
`ctx_distillery/tools/`. The endpoint tests at the bottom DO use a real `TraceRecorder`, because what
they pin is the loader path (404 / 502 / non-dict line), not the payload shape.

Two measured numbers drive the timing tests and are asserted by name, not approximated:

* a REAL live run's `main_step` ts span is **20.4s** -> `per_turn_timing = True`;
* the OFFLINE scripted harness (`ScriptedInterpreter` + `scripted_lm`, what this workspace's tests
  run on) spans **0.0019s** -> `per_turn_timing = False`.

The second is the COMMON case here, so `test_per_turn_timing_false_...` pins that `timeline` stays
FULLY populated in it — a drawer that surfaced tools only through turn grouping would render nothing
on every trace the suite can produce.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from ctx_distillery_studio import app as appmod
from ctx_distillery_studio.iterations import build_iterations
from fastapi.testclient import TestClient
from rlm_kit.trace import TraceRecorder, record_tool_call

client = TestClient(appmod.app)

# ---- sentinels: every value that must NEVER reach the drawer ---------------------------------
# Each appears in EXACTLY ONE place in the fixtures below (a tool_call payload or run_start.meta), so
# the leak test can assert over the WHOLE serialized envelope rather than a hand-picked subset.

RESOLVED_PATH = "/Users/operator/.claude/projects/-Users-operator-proj/memory/stale.md"
REFUSAL_NOTE = (
    "refused: '/Users/operator/.ssh/id_rsa' is not in this run's memory index. "
    "Call list_memory_files() and pass one of the paths it returns."
)
PROJECT_DIR = "/Users/operator/Documents/secret-client-project"
DRAFT = (
    "---\n"
    "name: merge-freeze-policy\n"
    "description: Merges are frozen during a release.\n"
    "metadata:\n"
    "  type: project\n"
    "---\n"
    "DRAFTEDBODYSENTINEL — merges into main are frozen for the duration of a release.\n"
)
EVIDENCE = "user: REDACTEDTRANSCRIPTSENTINEL — the release process is frozen from the Friday before a tag."

RUBRIC = [
    {"name": "plan_carries_real_judgement", "description": "d", "weight": 1.0, "category": "TF"},
    {"name": "evidence_gathered_before_drafting", "description": "d", "weight": 1.0, "category": "TA"},
    {"name": "candidates_backed_by_real_drafts", "description": "d", "weight": 1.0, "category": "TG"},
    {"name": "plan_structurally_well_formed", "description": "d", "weight": 1.0, "category": "PA"},
]
META = {
    "transcripts": 1,
    "memory_artifacts": 0,
    "rubric": RUBRIC,
    "project_dir": PROJECT_DIR,
    "planner": "gpt_oss_120B",
    "drafter": "gpt_oss_120B",
    "interpreter": "pyodide",
    "max_iterations": 8,
    "max_llm_calls": 6,
}


def _e(step_id: int, type_: str, ts: float, payload: dict) -> dict:
    return {"schema": "trace/v1", "run_id": "r0", "step_id": step_id, "ts": ts, "type": type_,
            "payload": payload}


# ---- the REAL recorded tool payloads ---------------------------------------------------------


def _list_memory_files(ok: bool = True) -> dict:
    return {"tool": "list_memory_files", "args": {}, "ok": ok, "count": 2, "kinds": ["memory", "skill"]}


def _read_memory_file() -> dict:
    return {"tool": "read_memory_file", "args": {"path": RESOLVED_PATH}, "ok": True,
            "name": "stale.md", "kind": "memory", "resolved_path": RESOLVED_PATH,
            "chars": 812, "truncated": False}


def _read_memory_file_refused() -> dict:
    return {"tool": "read_memory_file", "args": {"path": "/Users/operator/.ssh/id_rsa"}, "ok": False,
            "note": REFUSAL_NOTE}


def _read_transcript_chunk() -> dict:
    return {"tool": "read_transcript_chunk", "args": {"transcript_index": 1, "offset": 4000, "limit": 4000},
            "ok": True, "length": 3120, "total_length": 7120}


def _draft_memory_file() -> dict:
    return {"tool": "draft_memory_file",
            "args": {"topic": "Release freeze rule", "memory_type": "project", "evidence": EVIDENCE},
            "ok": True, "artifact_id": "2f6d40f59078", "kind": "memory", "draft": DRAFT,
            "errors": [], "reasoning": None, "endpoint_error": None, "circuit_broken": False}


def _draft_skill_file() -> dict:
    return {"tool": "draft_skill_file",
            "args": {"procedure": "Run pytest -q locally before pushing changes to ctx_distillery/",
                     "scope": "project", "evidence": EVIDENCE},
            "ok": False, "artifact_id": "d3b846aa69da", "kind": "skill", "draft": DRAFT,
            "errors": ["frontmatter `description` is missing or not a non-empty string"],
            "reasoning": None, "endpoint_error": "502 from the drafter endpoint", "circuit_broken": True}


# ---- the two measured traces -----------------------------------------------------------------
# LIVE: the real run's event mix — {run_start:1, tool_call:4, main_step:3, final:1, result:1,
# run_end:1}, tool_calls carrying LOWER step_ids than the main_steps (main_step flushes post-hoc), ts
# spanning 20.4s across the turns. NO read tools: transcripts arrive as REPL variables, so a small one
# is read directly and `min_read_step` is None. That is the COMMON case, not an edge case.

T0 = 1785226770.373


def live_trace() -> list[dict]:
    return [
        _e(0, "run_start", T0, {"meta": META}),
        _e(1, "tool_call", T0 + 10.574, _draft_memory_file()),
        _e(2, "tool_call", T0 + 13.096, _draft_memory_file()),
        _e(3, "tool_call", T0 + 14.573, _draft_memory_file()),
        _e(4, "tool_call", T0 + 19.150, _draft_skill_file()),
        _e(5, "main_step", T0 + 2.990, {"turn": 0, "reasoning": "print the transcripts first",
                                        "code": "pprint(transcripts)", "output": "['user: hi']"}),
        _e(6, "main_step", T0 + 8.307, {"turn": 1, "reasoning": "draft the three rules",
                                        "code": "draft_memory_file(...)", "output": "ok"}),
        _e(7, "main_step", T0 + 23.416, {"turn": 2, "reasoning": "submit the plan",
                                         "code": "SUBMIT(plan)", "output": "FINAL"}),
        _e(8, "final", T0 + 23.434, {"final_reasoning": "submitting"}),
        _e(9, "result", T0 + 23.434, {"output": {"candidates": []}}),
        _e(10, "run_end", T0 + 23.451, {"ok": True, "error": None}),
    ]


# OFFLINE: the same event kinds as the scripted harness produces — a 0.0019s span across turns.
def offline_trace() -> list[dict]:
    return [
        _e(0, "run_start", T0, {"meta": META}),
        _e(1, "tool_call", T0 + 0.0009, _list_memory_files()),
        _e(2, "tool_call", T0 + 0.0012, _read_memory_file()),
        _e(3, "tool_call", T0 + 0.0015, _read_transcript_chunk()),
        _e(4, "tool_call", T0 + 0.0017, _draft_memory_file()),
        _e(5, "main_step", T0 + 0.0005, {"turn": 0, "reasoning": "look around",
                                         "code": "list_memory_files()", "output": "[]"}),
        _e(6, "main_step", T0 + 0.0024, {"turn": 1, "reasoning": "submit",
                                         "code": "SUBMIT(plan)", "output": "FINAL"}),
        _e(7, "result", T0 + 0.0025, {"output": {"candidates": []}}),
        _e(8, "run_end", T0 + 0.0026, {"ok": True, "error": None}),
    ]


def _one(payload: dict, type_: str = "tool_call") -> dict:
    """The single timeline entry produced by one tool_call/sub_call, in a minimal live-stamped run."""
    out = build_iterations([
        _e(0, "run_start", T0, {"meta": META}),
        _e(1, type_, T0 + 1.5, payload),
        _e(2, "run_end", T0 + 2.0, {"ok": True}),
    ])
    assert len(out["timeline"]) == 1
    return out["timeline"][0]


def _strings(node) -> list[str]:
    """Every string VALUE anywhere in the envelope (dict keys are ours, not payload-derived)."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, (list, tuple)):
        return [s for v in node for s in _strings(v)]
    return []


# ---- the envelope ----------------------------------------------------------------------------


def test_the_envelope_is_the_siblings_shape_verbatim():
    out = build_iterations(live_trace())
    assert set(out) == {"started_at", "total_s", "timing_note", "per_turn_timing", "initial",
                        "iterations", "timeline"}
    assert out["started_at"] == T0
    assert out["total_s"] == 23.451


def test_an_empty_trace_returns_the_envelope_without_raising():
    out = build_iterations([])
    assert out["started_at"] is None and out["total_s"] is None
    assert out["iterations"] == [] and out["timeline"] == []
    assert out["per_turn_timing"] is False
    assert out["initial"]["project"] is None and out["initial"]["rubric_criteria"] is None


# ---- `initial`: bespoke, and never the project path ------------------------------------------


def test_initial_surfaces_counts_roles_the_pinned_interpreter_budgets_and_the_rubric_count():
    initial = build_iterations(live_trace())["initial"]
    assert initial == {
        "project": "secret-client-project",          # BASENAME — never the absolute path
        "transcripts": 1,
        # This live trace predates `transcript_index`, so the COMPOSITION is unknown rather than
        # zero — an old trace carries no identity list at all, and `sessions=0 subagents=0` would
        # be a positive claim it never made.
        "sessions": None,
        "subagents": None,
        "memory_artifacts": 0,
        "models": {"planner": "gpt_oss_120B", "drafter": "gpt_oss_120B"},
        "interpreter": "pyodide",                    # invariant 1's pin, visible per run
        "rubric_criteria": 4,
        "max_iterations": 8,
        "max_llm_calls": 6,
    }


def test_initial_surfaces_the_transcript_composition_when_the_trace_carries_one():
    """`transcripts: 43` alone cannot distinguish one session from one session plus 42 subagents.
    Read through `mapper.transcript_composition` — the ONE implementation, imported rather than
    copied, because two copies of a shape guard is how one of them drifts."""
    meta = {
        "transcripts": 3,
        "transcript_index": [
            {"kind": "session", "id": "s1", "session": "s1", "parent": "session:s1"},
            {"kind": "subagent", "id": "a1", "session": "s1", "parent": "session:s1"},
            {"kind": "subagent", "id": "a2", "session": "s1", "parent": "workflow:wf_1"},
        ],
    }
    initial = build_iterations([_e(0, "run_start", T0, {"meta": meta})])["initial"]
    assert initial["transcripts"] == 3
    assert initial["sessions"] == 1 and initial["subagents"] == 2


@pytest.mark.parametrize("index", ["nope", 42, {"kind": "session"}, [42, None]])
def test_initial_degrades_the_composition_to_None_on_a_malformed_identity_list(index):
    """Invariant 10's "never 500 on a malformed trace", applied to the new key. `[42, None]` is the
    per-ELEMENT half — every element filtered leaves 0/0, which is a real (empty) list rather than
    a malformed one, so it reports zeros; the non-list shapes report None."""
    meta = {"transcripts": 1, "transcript_index": index}
    initial = build_iterations([_e(0, "run_start", T0, {"meta": meta})])["initial"]
    expected = (0, 0) if isinstance(index, list) else (None, None)
    assert (initial["sessions"], initial["subagents"]) == expected


def test_initial_has_no_dead_diff_sentry_fields():
    """diff-sentry's `initial` run against a ctx-distillery trace yields six always-null fields. This
    project has no `source`/`instructions` analogue at all — the transcripts are never in the trace,
    only COUNTED — and inventing one would be fabrication."""
    initial = build_iterations(live_trace())["initial"]
    for dead in ("change", "source", "instructions", "baseline_indicators", "emit_on", "task"):
        assert dead not in initial


def test_initial_degrades_to_nulls_when_run_start_carries_no_meta():
    out = build_iterations([_e(0, "run_start", T0, {}), _e(1, "run_end", T0 + 1, {"ok": True})])
    assert out["initial"]["models"] == {"planner": None, "drafter": None}
    assert out["initial"]["interpreter"] is None and out["initial"]["rubric_criteria"] is None


def test_a_root_project_dir_basenames_to_nothing_rather_than_a_slash():
    out = build_iterations([_e(0, "run_start", T0, {"meta": {"project_dir": "/"}})])
    assert out["initial"]["project"] is None


# ---- timing: the two MEASURED spans ----------------------------------------------------------


def test_per_turn_timing_is_true_on_the_measured_live_span_and_back_maps_turn_index():
    out = build_iterations(live_trace())
    turn_ts = [2.990, 8.307, 23.416]
    assert round(turn_ts[-1] - turn_ts[0], 1) == 20.4      # the measured real-run span
    assert out["per_turn_timing"] is True
    assert out["timing_note"].startswith("Per-turn timing is live")
    assert [it["index"] for it in out["iterations"]] == [0, 1, 2]
    assert [it["rel_s"] for it in out["iterations"]] == [2.99, 8.307, 23.416]
    assert [it["duration_s"] for it in out["iterations"]] == [5.317, 15.109, 0.035]
    # All four drafting calls happened between turn 1's parse (8.307s) and turn 2's (23.416s).
    assert [t["turn_index"] for t in out["timeline"]] == [1, 1, 1, 1]


def test_per_turn_timing_is_false_on_the_measured_offline_span_but_the_timeline_stays_complete():
    """THE case this workspace's own tests all land on. `turn_index` back-mapping runs only
    `if per_turn`, so it is absent everywhere here — and `timeline` must still be fully populated,
    or the drawer renders nothing on every trace the suite can produce."""
    out = build_iterations(offline_trace())
    assert round(0.0024 - 0.0005, 4) == 0.0019            # the measured offline-harness span
    assert out["per_turn_timing"] is False
    assert out["timing_note"].startswith("Per-turn timing isn't available")
    # Turns still carry all their CONTENT — only the timing enrichment is withheld.
    assert [it["turn"] for it in out["iterations"]] == [0, 1]
    assert [it["index"] for it in out["iterations"]] == [0, 1]
    assert all("rel_s" not in it and "duration_s" not in it for it in out["iterations"])
    # The timeline is COMPLETE: every tool call, in order, with seq/rel_s/duration_s — just no link.
    assert len(out["timeline"]) == 4
    assert [t["seq"] for t in out["timeline"]] == [0, 1, 2, 3]
    assert [t["label"] for t in out["timeline"]] == [
        "list memory", "read memory", "read transcript", "draft memory"]
    assert all(isinstance(t["rel_s"], float) and isinstance(t["duration_s"], float)
               for t in out["timeline"])
    assert all("turn_index" not in t for t in out["timeline"])


def test_a_single_turn_never_claims_per_turn_timing():
    out = build_iterations([
        _e(0, "run_start", T0, {"meta": META}),
        _e(1, "main_step", T0 + 30.0, {"turn": 0, "reasoning": "one shot", "code": "c", "output": "o"}),
        _e(2, "run_end", T0 + 31.0, {"ok": True}),
    ])
    assert out["per_turn_timing"] is False                # len(step_ts) >= 2 is required


def test_duration_s_is_gap_attributed_planner_think_plus_tool_exec():
    """No per-call instrumentation exists anywhere in this project, so a tool entry's `duration_s` is
    the gap since the PREVIOUS live event — planner-think + tool-exec, not tool-exec alone. Pinned
    here so nobody later re-labels it as a tool latency."""
    out = build_iterations(live_trace())
    assert [t["duration_s"] for t in out["timeline"]] == [10.574, 2.522, 1.477, 4.577]
    assert out["timeline"][0]["duration_s"] == 10.574     # gap since run_start, not a 10s tool call


# ---- the five tools' rows, field by field ----------------------------------------------------


def test_list_memory_files_row():
    """Asserted as an EXACT dict: an allowlist row is defined by what it does NOT contain, and a
    field-by-field `in` check would pass just as happily if a future edit added `args` back."""
    assert _one(_list_memory_files()) == {
        "entry_kind": "tool", "tool": "list_memory_files", "ok": True, "duration_s": 1.5,
        "label": "list memory", "count": 2, "kinds": ["memory", "skill"], "seq": 0, "rel_s": 1.5,
    }


def test_read_memory_file_row_carries_the_artifact_never_the_resolved_path():
    entry = _one(_read_memory_file())
    assert entry["label"] == "read memory"
    assert entry["name"] == "stale.md" and entry["kind"] == "memory"
    assert entry["chars"] == 812 and entry["truncated"] is False
    assert "resolved_path" not in entry
    assert RESOLVED_PATH not in json.dumps(entry)
    assert "args" not in entry and "path" not in entry


def test_a_refused_read_reads_as_ok_false_and_never_carries_the_note():
    """The refusal `note` embeds a MODEL-SUPPLIED path verbatim mid-sentence — no path-shaped-value
    heuristic can catch that, which is exactly why `mapper._SCALAR_DROP` drops it by NAME. Same here.
    A reviewer sees `ok: false`; the reason stays in the trace file, readable in context."""
    entry = _one(_read_memory_file_refused())
    assert entry["ok"] is False
    assert "note" not in entry
    assert "id_rsa" not in json.dumps(entry)


def test_read_transcript_chunk_row_is_the_audit_window():
    """`#index @offset +length of total_length` — the whole reason `tools/transcript_reader.py`
    exists ("did the plan's cross-conversation claim really look at both transcripts?"), and nothing
    surfaced it before this drawer. The tool never records the text it read; neither does this row."""
    entry = _one(_read_transcript_chunk())
    assert entry["label"] == "read transcript"
    assert entry["transcript_index"] == 1 and entry["offset"] == 4000 and entry["limit"] == 4000
    assert entry["length"] == 3120 and entry["total_length"] == 7120


def test_read_transcript_chunk_args_that_are_not_ints_are_dropped():
    """Those args are whatever the PLANNER passed — a refusal is recorded precisely because it passed
    junk. A str there must never reach the row (bools are refused too: `isinstance(True, int)`)."""
    entry = _one({"tool": "read_transcript_chunk", "ok": False,
                  "args": {"transcript_index": "/etc/passwd", "offset": True, "limit": None},
                  "note": "refused: transcript_index must be an int"})
    assert entry["transcript_index"] is None and entry["offset"] is None and entry["limit"] is None
    assert "passwd" not in json.dumps(entry)


def test_draft_memory_row_carries_draft_chars_never_the_draft_or_the_evidence():
    entry = _one(_draft_memory_file())
    assert entry["label"] == "draft memory"
    assert entry["artifact_id"] == "2f6d40f59078" and entry["kind"] == "memory"
    assert entry["topic"] == "Release freeze rule" and entry["memory_type"] == "project"
    assert entry["draft_chars"] == len(DRAFT)
    assert entry["errors"] == [] and entry["circuit_broken"] is False
    assert entry["endpoint_error"] is None
    assert "draft" not in entry and "evidence" not in entry and "reasoning" not in entry
    assert "DRAFTEDBODYSENTINEL" not in json.dumps(entry)
    assert "REDACTEDTRANSCRIPTSENTINEL" not in json.dumps(entry)


def test_draft_skill_row_carries_procedure_scope_and_the_failure_detail():
    entry = _one(_draft_skill_file())
    assert entry["label"] == "draft skill" and entry["kind"] == "skill"
    assert entry["scope"] == "project"
    assert entry["procedure"].startswith("Run pytest -q locally")
    assert entry["errors"] == ["frontmatter `description` is missing or not a non-empty string"]
    assert entry["circuit_broken"] is True
    assert entry["endpoint_error"] == "502 from the drafter endpoint"
    assert entry["draft_chars"] == len(DRAFT)
    assert "draft" not in entry


def test_the_payloads_own_kind_survives_because_the_envelope_field_was_renamed():
    """cve-reverser opens the entry as `{"kind": "tool", …}` and `setdefault`s the payload on top, so
    the payload's OWN `kind` is silently swallowed. Three of this project's five tools carry one."""
    for payload, expected in ((_read_memory_file(), "memory"), (_draft_memory_file(), "memory"),
                              (_draft_skill_file(), "skill")):
        entry = _one(payload)
        assert entry["entry_kind"] == "tool"
        assert entry["kind"] == expected


def test_an_unrecognized_tool_contributes_no_payload_fields():
    """The tool set is CLOSED (invariant 1), so this branch means a trace from a different build or a
    corrupted one — neither justifies auto-surfacing unvetted keys. No `_scalar_fields` sweep, no
    `_preview(args)`; the flag lets the UI say so out loud instead of rendering a mystery row."""
    entry = _one({"tool": "write_memory_file", "ok": True, "secret": RESOLVED_PATH,
                  "args": {"body": DRAFT}, "note": REFUSAL_NOTE})
    assert entry == {
        "entry_kind": "tool", "tool": "write_memory_file", "ok": True, "duration_s": 1.5,
        "label": "write_memory_file", "unrecognized": True, "seq": 0, "rel_s": 1.5,
    }


# ---- `sub_call`: the entry the design forgot -------------------------------------------------


def test_sub_call_becomes_a_sub_lm_entry_named_for_this_project():
    """`timeline` mixes `tool_call` AND `sub_call`; dropping it would lose every escalation. The
    siblings hardcode `analyst`/`lifeline` — `mapper.py` already named this event
    `distill.sub_lm.call`, so the entry matches it."""
    entry = _one({"input": "memory or skill?", "processed": "skill", "raw": "  skill  ",
                  "name": "gpt_oss_120B"}, type_="sub_call")
    assert entry["entry_kind"] == "sub_lm" and entry["label"] == "sub-LM"
    assert entry["model"] == "gpt_oss_120B"
    assert entry["input"] == "memory or skill?"
    assert entry["output"] == "skill"                     # processed preferred over raw
    assert entry["error"] is None
    assert entry["duration_s"] == 1.5 and entry["rel_s"] == 1.5


def test_sub_call_falls_back_to_raw_and_carries_its_error():
    entry = _one({"input": "q", "raw": "r", "error": "endpoint timeout"}, type_="sub_call")
    assert entry["output"] == "r" and entry["error"] == "endpoint timeout"


def test_a_timeline_mixing_tools_and_sub_calls_keeps_one_seq_sequence():
    out = build_iterations([
        _e(0, "run_start", T0, {"meta": META}),
        _e(1, "tool_call", T0 + 1.0, _list_memory_files()),
        _e(2, "sub_call", T0 + 2.0, {"input": "q", "processed": "a"}),
        _e(3, "tool_call", T0 + 3.0, _draft_memory_file()),
        _e(4, "run_end", T0 + 4.0, {"ok": True}),
    ])
    assert [t["seq"] for t in out["timeline"]] == [0, 1, 2]
    assert [t["entry_kind"] for t in out["timeline"]] == ["tool", "sub_lm", "tool"]


# ---- the no-read-tools shape: the MEASURED common case ---------------------------------------


def test_a_run_with_no_read_tools_at_all_is_fully_rendered():
    """The measured live run called `draft_memory_file` x3 + `draft_skill_file` x1 and NO read tool
    (`min_read_step` is None): the transcripts arrive as REPL variables, so a small one is read
    directly. That is the common case, not an edge case — the drawer must be complete without a
    single evidence read."""
    out = build_iterations(live_trace())
    assert len(out["timeline"]) == 4
    assert {t["tool"] for t in out["timeline"]} == {"draft_memory_file", "draft_skill_file"}
    assert not any(t["tool"] in ("list_memory_files", "read_memory_file", "read_transcript_chunk")
                   for t in out["timeline"])
    assert len(out["iterations"]) == 3
    assert all(it["reasoning"] and it["code"] for it in out["iterations"])


# ---- the leak assertion, over the WHOLE envelope ---------------------------------------------


@pytest.mark.parametrize("trace", [live_trace(), offline_trace()], ids=["live", "offline"])
def test_nothing_sensitive_appears_anywhere_in_the_output(trace):
    """Every sentinel below lives in EXACTLY ONE place in the fixtures — a tool_call payload or
    `run_start.meta` — and the turns' own reasoning/code/output are innocuous, so this can assert
    over the entire serialized envelope rather than a hand-picked subset."""
    blob = json.dumps(build_iterations(trace), ensure_ascii=False)
    # The key-name sentinels are quoted so they match a JSON KEY, not any substring — `"note"` as a
    # bare substring would hit the envelope's own `timing_note`, and `"draft"` would hit `draft_chars`.
    # `"reasoning"` is NOT a sentinel: it is a legitimate key on every `iterations` turn (see
    # `test_a_turns_own_repl_text_is_carried_verbatim_by_design`). It is the DRAFTING tools' own
    # `reasoning` that is excluded, which the per-tool row tests assert directly.
    for sentinel in ('"resolved_path"', '"note"', '"evidence"', '"draft"', '"project_dir"', '"args"',
                     RESOLVED_PATH, REFUSAL_NOTE, "id_rsa", "DRAFTEDBODYSENTINEL",
                     "REDACTEDTRANSCRIPTSENTINEL", PROJECT_DIR, "/Users", "/private/tmp"):
        assert sentinel not in blob, f"{sentinel!r} leaked into the drawer"


@pytest.mark.parametrize("trace", [live_trace(), offline_trace()], ids=["live", "offline"])
def test_no_string_anywhere_in_the_output_is_a_filesystem_path(trace):
    for value in _strings(build_iterations(trace)):
        assert not value.startswith(("/", "~/", "\\", "./", "../")), value


def test_a_path_shaped_topic_is_dropped_but_a_slash_bearing_sentence_survives():
    """The guard tests the value's SHAPE, never a substring — the same narrow rule
    `mapper._looks_like_a_path` uses, so the drawer and the feed cannot disagree about what a path is.
    A real drafted topic reads `run pytest from eval/`; dropping that would be useless prudishness."""
    payload = _draft_memory_file() | {"args": {"topic": "/Users/operator/notes.md",
                                               "memory_type": "project", "evidence": EVIDENCE}}
    assert _one(payload)["topic"] is None
    payload = _draft_memory_file() | {"args": {"topic": "run pytest from eval/ not the repo root",
                                               "memory_type": "project", "evidence": EVIDENCE}}
    assert _one(payload)["topic"] == "run pytest from eval/ not the repo root"


def test_a_long_label_is_truncated_not_dropped():
    """The siblings' `_scalar_fields` DROPS any string over its cap — the length-DEPENDENT behaviour
    the audit flagged on `draft`, where a reader cannot tell "absent" from "was too long". Presence
    here depends only on the value's shape."""
    payload = _draft_memory_file() | {"args": {"topic": "t" * 500, "memory_type": "project",
                                               "evidence": EVIDENCE}}
    topic = _one(payload)["topic"]
    assert len(topic) == 201 and topic.endswith("…")


def test_a_pathological_turn_output_is_capped_not_dropped():
    out = build_iterations([
        _e(0, "run_start", T0, {"meta": META}),
        _e(1, "main_step", T0 + 1, {"turn": 0, "reasoning": "r", "code": "c", "output": "x" * 20000}),
        _e(2, "main_step", T0 + 5, {"turn": 1, "reasoning": "r", "code": "c", "output": "o"}),
        _e(3, "run_end", T0 + 6, {"ok": True}),
    ])
    assert out["iterations"][0]["output"].endswith("…[truncated — full text in the trace]")
    assert len(out["iterations"][0]["output"]) == 16000 + len("\n…[truncated — full text in the trace]")


def test_a_turns_own_repl_text_is_carried_verbatim_by_design():
    """The one thing this module deliberately does NOT scrub, stated so the leak tests above are not
    misread as a promise that turn text is filtered. A turn's `output` is the REPL echo of whatever
    the planner printed — routinely INCLUDING a drafting tool's return value, drafted body and all —
    and surfacing it is the drawer's single largest information gain (`mapper.to_event` carries only
    `has_code: bool` and drops `output` entirely). The mitigation is RENDERING, not filtering: the
    frontend must use `el.textContent`, never `innerHTML` (invariant 10)."""
    echo = f"Memory draft result: {{'artifact_id': 'a1', 'ok': True, 'draft': {DRAFT!r}}}"
    out = build_iterations([
        _e(0, "run_start", T0, {"meta": META}),
        _e(1, "main_step", T0 + 1, {"turn": 0, "reasoning": "r", "code": "c", "output": echo}),
        _e(2, "run_end", T0 + 2, {"ok": True}),
    ])
    assert out["iterations"][0]["output"] == echo
    assert "DRAFTEDBODYSENTINEL" in out["iterations"][0]["output"]


# ---- malformed input: never raise ------------------------------------------------------------


def test_events_missing_ts_or_payload_never_raise():
    out = build_iterations([
        {"type": "run_start"},                                   # no ts, no payload
        {"type": "tool_call", "payload": _list_memory_files()},   # no ts -> not in the timeline
        {"type": "tool_call", "ts": T0 + 1},                      # no payload
        {"type": "main_step", "ts": T0 + 2, "payload": None},
        {"type": "run_end"},
    ])
    assert out["started_at"] is None
    assert len(out["timeline"]) == 1 and out["timeline"][0]["unrecognized"] is True
    assert out["timeline"][0]["rel_s"] is None and out["timeline"][0]["duration_s"] is None
    assert len(out["iterations"]) == 1


def test_a_non_int_args_blob_never_raises():
    entry = _one({"tool": "draft_memory_file", "ok": True, "args": "not a dict", "draft": DRAFT})
    assert entry["topic"] is None and entry["memory_type"] is None
    assert entry["draft_chars"] == len(DRAFT)


def test_turns_with_no_turn_number_sort_last_without_raising():
    out = build_iterations([
        _e(0, "run_start", T0, {"meta": META}),
        _e(1, "main_step", T0 + 1, {"turn": None, "reasoning": "?", "code": "c", "output": "o"}),
        _e(2, "main_step", T0 + 2, {"turn": 0, "reasoning": "first", "code": "c", "output": "o"}),
        _e(3, "run_end", T0 + 3, {"ok": True}),
    ])
    assert [it["turn"] for it in out["iterations"]] == [0, None]


# ---- the endpoint ----------------------------------------------------------------------------


def test_iterations_endpoint_returns_the_breakdown_of_a_real_recorded_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    with TraceRecorder(str(tmp_path / "r0.jsonl"), run_id="r0", meta=META):
        record_tool_call(**_list_memory_files())
        record_tool_call(**_draft_memory_file())

    body = client.get("/v1/runs/r0/iterations").json()
    assert set(body) == {"started_at", "total_s", "timing_note", "per_turn_timing", "initial",
                         "iterations", "timeline"}
    assert body["initial"]["interpreter"] == "pyodide"
    assert body["initial"]["project"] == "secret-client-project"
    assert [t["label"] for t in body["timeline"]] == ["list memory", "draft memory"]
    assert body["timeline"][1]["draft_chars"] == len(DRAFT)
    assert "DRAFTEDBODYSENTINEL" not in json.dumps(body)
    assert PROJECT_DIR not in json.dumps(body)


def test_iterations_endpoint_404s_when_no_such_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    assert client.get("/v1/runs/does-not-exist/iterations").status_code == 404


def test_iterations_endpoint_502s_on_a_genuinely_corrupted_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    (tmp_path / "bad.jsonl").write_text("not json at all\n", encoding="utf-8")
    assert client.get("/v1/runs/bad/iterations").status_code == 502


def test_iterations_endpoint_never_500s_on_a_syntactically_valid_but_non_dict_jsonl_line(
    tmp_path, monkeypatch
):
    """The regression all three sibling studios still ship in THEIR `/iterations` path: their loader
    catches only `JSONDecodeError`, so a JSON-valid non-object line reaches a `.get(...)` and raises
    a raw `AttributeError` — a genuine 500. Routing through `_load_trace` inherits
    `trace_io.load_trace`'s dict-shape filter, so this endpoint never had the bug."""
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    (tmp_path / "weird.jsonl").write_text(
        '{"type": "run_start", "ts": 1.0, "step_id": 0, "payload": {"meta": {"interpreter": "pyodide"}}}\n'
        "42\nnull\n[1, 2, 3]\n\"x\"\n",
        encoding="utf-8",
    )
    resp = client.get("/v1/runs/weird/iterations")
    assert resp.status_code == 200, resp.text
    assert resp.json()["initial"]["interpreter"] == "pyodide"


def test_the_iterations_run_id_is_slug_sanitized_against_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    assert client.get("/v1/runs/..%2F..%2Fetc%2Fpasswd/iterations").status_code == 404


def test_the_iterations_endpoint_never_500s_on_a_non_dict_payload_or_meta(tmp_path, monkeypatch):
    """REGRESSION TEST for a real 500 an adversarial review reproduced on this very endpoint.

    `CLAUDE.md` invariant 10 and `studio/DESIGN.md` §5.6 both promise 404 / 502 / never 500 on a
    malformed trace. `_load_trace`'s dict filter is LINE-level, so a well-formed JSON object whose
    `payload` is `"oops"` — or whose `payload["meta"]` is a string, one level further in — sailed
    through and hit `.get(...)`. The payload half is fixed in `trace_io.dict_events` (shared, so
    every consumer inherits it); the nested `meta` half is fixed here and in `mapper.to_event`,
    because `meta` is a known nested dict rather than a generic envelope field.
    """
    import json

    from fastapi.testclient import TestClient

    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "bad.jsonl").write_text(
        "\n".join(
            json.dumps(e)
            for e in (
                {"type": "run_start", "step_id": 0, "ts": 1.0, "payload": {"meta": "nope"}},
                {"type": "tool_call", "step_id": 1, "ts": 2.0, "payload": "oops"},
                {"type": "main_step", "step_id": 2, "ts": 3.0, "payload": [1, 2]},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CTXD_TRACES_DIR", str(traces))

    import importlib

    import ctx_distillery_studio.app as appmod

    importlib.reload(appmod)
    client = TestClient(appmod.app, raise_server_exceptions=False)

    for path in ("/v1/runs/bad", "/v1/runs/bad/iterations", "/v1/runs/bad/events"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}, not 200"
    # The SSE stream must carry real events, not merely avoid raising — a generator that dies on its
    # first event returns 200 with an EMPTY body, which looked like success and was not.
    assert client.get("/v1/runs/bad/events").content, "the replay stream came back empty"
