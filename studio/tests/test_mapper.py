"""The trace-event -> SSE mapping (the public event surface), verified per event type. Pure — no
server, mirroring `diff_sentry_studio.mapper`'s own test-file separation. Hand-rolled event dicts
here are fine (unlike `test_app.py`) because `to_event` is a pure function over `{type, payload}`
shapes already pinned by `rlm_kit`'s own trace/v1 contract — no `TraceRecorder` round trip is needed
to exercise its branches."""

import pytest
from ctx_distillery_studio.mapper import _scalar_fields, to_event, transcript_composition


def test_run_start_carries_transcripts_memory_artifacts_and_a_rubric_hint():
    ev = to_event(
        {
            "type": "run_start",
            "payload": {
                "meta": {
                    "transcripts": 2,
                    "memory_artifacts": 5,
                    "rubric": [
                        {"name": "n1", "category": "TF", "weight": 1.0, "description": "d"},
                        {"name": "n2", "category": "TA", "weight": 1.0, "description": "d"},
                    ],
                }
            },
        }
    )
    assert ev["event"] == "distill.run.created"
    assert ev["data"]["transcripts"] == 2 and ev["data"]["memory_artifacts"] == 5
    assert ev["data"]["rubric"] == {"categories": ["TA", "TF"], "criteria": 2}


def test_run_start_rubric_hint_empty_when_absent():
    ev = to_event({"type": "run_start", "payload": {"meta": {}}})
    assert ev["data"]["rubric"] == {"categories": [], "criteria": 0}
    assert ev["data"]["transcripts"] is None and ev["data"]["memory_artifacts"] is None


def test_run_start_carries_the_transcript_COMPOSITION_when_the_trace_has_one():
    """A bare `transcripts=43` cannot say what those entries were, and a jump from 1 to 43 because
    subagent transcripts were included is otherwise silent semantic drift in the feed."""
    ev = to_event(
        {
            "type": "run_start",
            "payload": {
                "meta": {
                    "transcripts": 3,
                    "transcript_index": [
                        {"kind": "session", "id": "s1", "session": "s1", "parent": "session:s1"},
                        {"kind": "subagent", "id": "a1", "session": "s1", "parent": "session:s1"},
                        {"kind": "subagent", "id": "a2", "session": "s1", "parent": "workflow:wf_1"},
                    ],
                }
            },
        }
    )
    assert ev["data"]["transcripts"] == 3
    assert ev["data"]["sessions"] == 1 and ev["data"]["subagents"] == 2


@pytest.mark.parametrize(
    "meta",
    [
        {},                                              # an OLD trace: no identity list at all
        {"transcript_index": "nope"},                    # a non-LIST
        {"transcript_index": {"kind": "session"}},       # a dict, not a list
        {"transcript_index": None},
        "not a dict at all",
    ],
)
def test_an_absent_or_malformed_identity_list_degrades_to_None_never_to_zero(meta):
    """Both halves matter and they are different failures. ABSENT is an old trace; MALFORMED is a
    corrupted or foreign one, which invariant 10 says must degrade rather than 500 the endpoint.
    Neither may render `sessions=0 subagents=0` — that is a positive claim the trace never made.
    """
    assert transcript_composition(meta) == {"sessions": None, "subagents": None}


def test_a_non_dict_ELEMENT_inside_the_identity_list_is_filtered_not_fatal():
    """The per-element guard, the same shape `trace_io.dict_events` applies one level up: a
    `.get(...)` on `42` is an `AttributeError`, i.e. a genuine 500 out of a replay endpoint."""
    meta = {"transcript_index": [
        42, None, ["x"], {"kind": "session"}, {"kind": "subagent"}, {"no": "kind"},
    ]}
    assert transcript_composition(meta) == {"sessions": 1, "subagents": 1}


def test_main_step_is_a_plan_step_carrying_the_planners_own_reasoning():
    ev = to_event({"type": "main_step", "payload": {"turn": 3, "reasoning": "read first", "code": "x = 1"}})
    assert ev == {
        "event": "distill.plan.step",
        "data": {"turn": 3, "reasoning": "read first", "has_code": True},
    }


def test_main_step_has_code_false_when_no_code_was_emitted():
    ev = to_event({"type": "main_step", "payload": {"turn": 0, "reasoning": "thinking", "code": None}})
    assert ev["data"]["has_code"] is False


def test_sub_call_is_a_sub_lm_call_preferring_processed_over_raw():
    # rlm-kit's sub-LM records input/processed/raw — NOT question/answer.
    ev = to_event(
        {"type": "sub_call", "payload": {"input": "escalate this", "processed": "cleaned", "raw": "dirty"}}
    )
    assert ev["event"] == "distill.sub_lm.call"
    assert ev["data"] == {"input": "escalate this", "processed_or_raw": "cleaned"}


def test_sub_call_falls_back_to_raw_when_no_processed_output():
    ev = to_event({"type": "sub_call", "payload": {"input": "escalate this", "raw": "dirty"}})
    assert ev["data"]["processed_or_raw"] == "dirty"


def test_evidence_read_tools_are_surfaced_with_scalar_fields():
    for tool in ("list_memory_files", "read_memory_file", "read_transcript_chunk"):
        ev = to_event(
            {
                "type": "tool_call",
                "payload": {
                    "tool": tool,
                    "args": {"path": "irrelevant"},
                    "ok": True,
                    "count": 3,
                    "kinds": ["memory", "skill"],
                    "chars": 120,
                },
            }
        )
        assert ev["event"] == "distill.evidence.read"
        assert ev["data"]["tool"] == tool
        assert ev["data"]["count"] == 3 and ev["data"]["chars"] == 120
        # dict/list-shaped fields (args, kinds) are dropped — scalars only.
        assert "args" not in ev["data"] and "kinds" not in ev["data"]


def test_draft_created_never_leaks_the_full_draft_text():
    ev = to_event(
        {
            "type": "tool_call",
            "payload": {
                "tool": "draft_memory_file",
                "artifact_id": "a1",
                "ok": True,
                "errors": [],
                "circuit_broken": False,
                "draft": "---\nname: x\n---\nsecret body text",
            },
        }
    )
    assert ev["event"] == "distill.draft.created"
    assert ev["data"] == {
        "tool": "draft_memory_file",
        "artifact_id": "a1",
        "relative_path": None,  # only draft_skill_extra_file ever carries one
        "ok": True,
        "errors": [],
        "circuit_broken": False,
    }
    assert "draft" not in ev["data"]
    assert "secret body text" not in str(ev["data"])


def test_draft_skill_file_is_also_a_draft_created_event():
    ev = to_event(
        {
            "type": "tool_call",
            "payload": {
                "tool": "draft_skill_file",
                "artifact_id": "s1",
                "ok": False,
                "errors": ["bad frontmatter"],
                "circuit_broken": True,
            },
        }
    )
    assert ev["event"] == "distill.draft.created"
    assert ev["data"]["ok"] is False and ev["data"]["circuit_broken"] is True
    assert ev["data"]["errors"] == ["bad frontmatter"]
    assert ev["data"]["relative_path"] is None


def test_draft_skill_extra_file_is_also_a_draft_created_event_carrying_relative_path():
    """The sixth tool joined `_DRAFT_TOOLS` once it existed — without it, a live run using it would
    have those calls silently DROPPED from the feed rather than merely under-detailed."""
    ev = to_event(
        {
            "type": "tool_call",
            "payload": {
                "tool": "draft_skill_extra_file",
                "artifact_id": "s1",
                "relative_path": "references/runbook.md",
                "kind": "reference",
                "ok": True,
                "errors": [],
                "circuit_broken": False,
                "draft": "secret reference body",
            },
        }
    )
    assert ev["event"] == "distill.draft.created"
    assert ev["data"]["tool"] == "draft_skill_extra_file"
    assert ev["data"]["relative_path"] == "references/runbook.md"
    assert "draft" not in ev["data"] and "secret reference body" not in str(ev["data"])


def test_an_unrecognized_tool_call_is_skipped_never_guessed_at():
    assert to_event({"type": "tool_call", "payload": {"tool": "mystery_tool", "ok": True}}) is None


def test_result_run_end_and_final():
    assert to_event({"type": "result", "payload": {"output": {}}}) == {"event": "distill.plan.done", "data": {}}
    assert to_event({"type": "run_end", "payload": {"ok": True}}) == {
        "event": "distill.run.completed",
        "data": {},
    }
    # `final` is SKIPPED: a real finished trace holds BOTH `final` (record_main_trajectory) and
    # `run_end` (the recorder's __exit__) — mapping both would emit the terminal event TWICE per
    # replay, and `final` lands BEFORE `result`. `run_end` is the sole terminal.
    assert to_event({"type": "final", "payload": {"final_reasoning": "done"}}) is None


def test_unknown_event_type_is_skipped():
    assert to_event({"type": "something_else", "payload": {}}) is None


def test_scalar_fields_drops_tool_ok_args_and_any_bulky_or_nested_value():
    out = _scalar_fields(
        {
            "tool": "read_memory_file",
            "ok": True,
            "args": {"path": "x"},
            "name": "conventions",
            "kind": "memory",
            "chars": 42,
            "truncated": False,
            "long": "x" * 500,
            "nested_list": [1, 2, 3],
        }
    )
    assert out == {"name": "conventions", "kind": "memory", "chars": 42, "truncated": False}


def test_scalar_fields_never_streams_a_filesystem_path_or_a_refusal_note():
    """A REGRESSION TEST for a real leak an adversarial review found in shipped code.

    This project's evidence reads run against the operator's OWN `~/.claude` store, so
    `read_memory_file` records `resolved_path` as an absolute path like
    `/Users/<you>/.claude/projects/-Users-<you>-<project>/memory/<file>.md`, and its refusal `note`
    embeds a model-supplied path verbatim inside a sentence. Both are SHORT strings, so the
    `_MAX_SCALAR` length guard never caught them — and `app.js` renders the whole data object into
    the live feed. The path identifies the machine and the account; nothing a plan reviewer needs.

    Two independent guards, and this test pins BOTH: the key names are dropped outright, and any
    value that LOOKS like a path is dropped whatever key it arrives under, so a future tool
    recording one under a new name cannot silently reopen the hole.
    """
    home_path = "/Users/somebody/.claude/projects/-Users-somebody-proj/memory/conventions.md"
    out = _scalar_fields(
        {
            "tool": "read_memory_file",
            "ok": True,
            "name": "conventions",
            "kind": "memory",
            "resolved_path": home_path,
            "note": f"no memory artifact at {home_path}",
            "chars": 812,
            "truncated": False,
        }
    )
    assert out == {"name": "conventions", "kind": "memory", "chars": 812, "truncated": False}
    assert home_path not in str(out)
    assert "somebody" not in str(out)

    # The by-shape half: a path under a key the drop-set has never heard of.
    assert _scalar_fields({"some_future_key": home_path}) == {}
    assert _scalar_fields({"rel": "./local/thing.md", "up": "../escape.md"}) == {}

    # ...without over-dropping: an ordinary sentence that merely CONTAINS a slash is not a path.
    assert _scalar_fields({"msg": "read 3 of 5 chunks a/b"}) == {"msg": "read 3 of 5 chunks a/b"}
