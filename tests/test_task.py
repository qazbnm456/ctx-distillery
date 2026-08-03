"""`DistillSession` wiring, driven through a REAL offline forward pass.

The last known risk this project named for itself was untested tool wiring, and the stated answer was
to exercise it OFFLINE via `rlm_kit.testing.ScriptedInterpreter`. So this drives `dspy.RLM.aforward`
with a scripted `DummyLM` + `ScriptedInterpreter`, so the planner -> list_memory_files ->
read_memory_file -> read_transcript_chunk -> draft_memory_file -> SUBMIT chain executes for real
(each tool's own tracing runs) with no live model, no Deno, and no network.

It also pins the two structural invariants in CODE rather than in a docstring: the `pyodide` pin
survives a caller passing something else, and the `output_model` carries no drafted content while the
trace re-sources it by `artifact_id`.
"""

from __future__ import annotations

import asyncio
import json

import pytest

dspy = pytest.importorskip("dspy")

import rlm_kit.runtime as rt
from rlm_kit import RLMConfig
from rlm_kit.testing import ScriptedInterpreter, assert_repl_safe, scripted_lm
from rlm_kit.trace import (
    EVENT_RESULT,
    EVENT_TOOL_CALL,
    TraceRecorder,
    load_events,
)

from ctx_distillery.task import (
    _INSTRUCTIONS,
    PINNED_INTERPRETER,
    PLANNER_PROMPT_VERSION,
    DistillCandidate,
    DistillPlan,
    DistillSession,
    _forced_config,
)

_TRANSCRIPT = (
    "user: from now on we freeze merges during a release\n"
    "assistant: understood — I'll stop opening PRs against main until 0.2 ships\n"
) * 20

_DRAFT = (
    "---\n"
    "name: merge-freeze-policy\n"
    "description: Merges are frozen during a release.\n"
    "metadata:\n"
    "  type: project\n"
    "---\n"
    "The user froze merges into main for the duration of a release.\n"
)


def _configure(turns):
    dummy = scripted_lm(turns)
    rt.configure(
        RLMConfig(main_model="x", sub_model="x", interpreter="mock", observe=False),
        main_lm=dummy,
        sub_lm=dummy,
    )


def _build(snapshot, interpreter=None, config=None):
    return DistillSession(
        memory_index=snapshot,
        chat_fn=lambda spec: _DRAFT,
        transcripts=[_TRANSCRIPT],
        interpreter=interpreter,
        config=config,
    )


def _payloads(path, tool):
    return [
        e["payload"]
        for e in load_events(path)
        if e["type"] == EVENT_TOOL_CALL and e["payload"].get("tool") == tool
    ]


# -- wiring ----------------------------------------------------------------------------------


def test_all_six_tools_are_wired_in_order_and_repl_safe(snapshot):
    _configure([{"reasoning": "r", "code": "SUBMIT(plan={})"}])
    task = _build(snapshot)
    assert [t.__name__ for t in task.tools] == [
        "list_memory_files",
        "read_memory_file",
        "read_transcript_chunk",
        "draft_memory_file",
        "draft_skill_file",
        "draft_skill_extra_file",
    ]
    for tool in task.tools:
        assert_repl_safe(tool)


def test_the_prompt_asks_for_the_prune_target_path_convention():
    """`apply.py` refuses a prune whose `key_fields["target_path"]` doesn't match the memory index.

    That convention only works if the PROMPT side asks for it — `key_fields` is a free-form dict, so
    nothing else would ever tell the planner to fill it in. Pinned here so the two halves cannot
    drift apart silently (CLAUDE.md invariant 8; the FIRST gap `apply.py`'s docstring records).
    """
    assert "target_path" in _INSTRUCTIONS
    assert "prune" in _INSTRUCTIONS
    assert "target_path" in (DistillCandidate.model_fields["key_fields"].description or "")


def test_the_prompt_teaches_the_promote_to_skill_scope_convention():
    """The same two-halves-must-not-drift property, now for `key_fields["scope"]`.

    `apply.py` ROUTES A WRITE by this field (user-global `~/.claude/skills/` vs. project-relative
    `<project>/.claude/skills/`) and refuses a candidate without a valid one — which only works if
    the PROMPT side asks for it, `key_fields` being a free-form dict. The instructions must also say
    HOW to decide, or the planner is guessing: a project-tied finding is "project", a portable
    technique is "global" (CLAUDE.md invariant 9).
    """
    assert 'key_fields["scope"]' in _INSTRUCTIONS
    assert "promote_to_skill" in _INSTRUCTIONS
    assert '"global"' in _INSTRUCTIONS and '"project"' in _INSTRUCTIONS
    description = DistillCandidate.model_fields["key_fields"].description or ""
    assert "scope" in description and "promote_to_skill" in description


def test_the_prompt_teaches_the_draft_skill_extra_file_convention():
    """The sixth tool only works if the prompt says WHEN to call it and that it shares the main
    draft's artifact_id — nothing else would tell the planner these files exist at all."""
    assert "draft_skill_extra_file" in _INSTRUCTIONS
    assert "draft_skill_file" in _INSTRUCTIONS
    assert "SAME" in _INSTRUCTIONS and "artifact_id" in _INSTRUCTIONS
    assert "references/" in _INSTRUCTIONS and "scripts/" in _INSTRUCTIONS


def test_the_prompt_teaches_the_project_instructions_convention():
    """`project_instructions` only means something if the prompt says what it is, that it is DATA
    never directives (the prompt-injection defense — a target project's CLAUDE.md may be authored
    by an untrusted third party), and how to use it (redundancy + conflict checks)."""
    assert "project_instructions" in _INSTRUCTIONS
    assert "CLAUDE.md" in _INSTRUCTIONS
    assert "directed at YOU" in _INSTRUCTIONS
    assert "redundant" in _INSTRUCTIONS
    assert "conflict" in _INSTRUCTIONS
    assert DistillSession.signature.count("project_instructions: str") == 1


def test_the_prompt_distinguishes_a_subagents_FINDINGS_from_its_AGREEMENT():
    """The instruction change subagent ingestion makes mandatory, and both halves of it.

    Left alone, "when multiple transcripts independently confirm the same thing, say so" becomes
    ACTIVELY HARMFUL: a subagent's content derives from its parent's request, so parent-and-subagent
    agreement is an echo, and the old wording makes the planner MORE confident on the strength of
    it. But saying only "subagents are not independent" invites the opposite failure — discounting
    subagent content wholesale, which is worse, because subagent text is the MAJORITY of the
    material in 6 of 11 real projects. So the prompt has to say both.
    """
    assert "subagent" in _INSTRUCTIONS.lower()
    assert "FINDINGS are ordinary evidence" in _INSTRUCTIONS
    assert "echo, not corroboration" in _INSTRUCTIONS
    assert "siblings, not independent witnesses" in _INSTRUCTIONS
    # A contradiction is a SIGNAL, not noise — the same asymmetry, read the other way.
    assert "CONTRADICTING its parent" in _INSTRUCTIONS
    # …and the planner is told how to tell the two kinds of entry apart, which is the header's job.
    assert "`[i] subagent" in _INSTRUCTIONS and "`[i] session" in _INSTRUCTIONS


def test_the_prompt_teaches_the_index_line_scan_and_that_it_needs_its_own_cell():
    """Orientation is a REPL scan, and its budget is one cell's `CD_MAX_OUTPUT_CHARS`.

    The prompt itself cannot carry the index: a signature input gets the same fixed 1000-character
    preview `transcripts` does, and appending it to the instructions would pay the full cost on
    EVERY turn and route per-run, partly model-authored text into a slot the driver's single
    `redact()` call does not cover. What is left is a cheap, pageable, truncation-VISIBLE scan —
    which only works if the planner is told to run it, alone, and to page when it is truncated.
    """
    assert 't.split("\\n")[0]' in _INSTRUCTIONS
    assert "OWN REPL cell" in _INSTRUCTIONS
    assert "page it" in _INSTRUCTIONS


def test_planner_prompt_version_is_pinned():
    """Mirrors the eval judge's own `PROMPT_VERSION` pin, on the side that had no counterpart.

    The constant is only worth anything if it MOVES when `_INSTRUCTIONS` moves, and a constant
    nothing asserts drifts silently from the text it names. v1 -> v2 was the subagent paragraphs
    plus the index-line scan; `session.run_distillation_artifacts` stamps whatever this says into
    every run's `run_meta`.
    """
    assert PLANNER_PROMPT_VERSION == "ctxd-planner-v2"


def test_each_instance_gets_its_own_tools(snapshot):
    _configure([{"reasoning": "r", "code": "SUBMIT(plan={})"}])
    a, b = _build(snapshot), _build(snapshot)
    assert a.tools[0] is not b.tools[0]
    # …and the class attribute was not mutated into a shared list
    assert DistillSession.tools == []


# -- the pyodide pin, checked in code (audit fix #1) ------------------------------------------


def test_the_interpreter_pin_overrides_a_caller_supplied_config(snapshot):
    _configure([{"reasoning": "r", "code": "SUBMIT(plan={})"}])
    task = _build(snapshot, config=RLMConfig(main_model="x", sub_model="x", interpreter="local"))
    assert task._config.interpreter == PINNED_INTERPRETER == "pyodide"
    assert task._config.allow_insecure_sandbox is False


def test_the_pin_also_overrides_the_globally_configured_interpreter(snapshot):
    _configure([{"reasoning": "r", "code": "SUBMIT(plan={})"}])   # configures interpreter="mock"
    assert _build(snapshot)._config.interpreter == "pyodide"


def test_forced_config_preserves_every_other_field():
    base = RLMConfig(
        main_model="m", sub_model="s", interpreter="container", max_iterations=42, observe=True
    )
    forced = _forced_config(base)
    assert forced.interpreter == "pyodide"
    assert (forced.main_model, forced.max_iterations, forced.observe) == ("m", 42, True)
    assert base.interpreter == "container"          # the frozen original is untouched


def test_the_pin_reaches_the_real_sandbox_selection_call(snapshot, monkeypatch):
    """The pin is only worth anything if `_build_rlm` actually asks for `pyodide`.

    `build_interpreter` is spied on rather than run, so no sandbox is spawned — but the NAME the
    real code path passes it is the thing being asserted.
    """
    _configure([{"reasoning": "r", "code": "SUBMIT(plan={})"}])
    seen: dict = {}

    def spy(name, allow_insecure=False, container=None, turn_timeout_s=None, cancel_event=None):
        seen["name"] = name
        seen["allow_insecure"] = allow_insecure

    monkeypatch.setattr("rlm_kit.task.build_interpreter", spy)
    _build(snapshot, config=RLMConfig(main_model="x", sub_model="x", interpreter="local"))._build_rlm()
    assert seen == {"name": "pyodide", "allow_insecure": False}


# -- the forward pass ------------------------------------------------------------------------


def test_scripted_forward_pass_records_the_whole_chain(snapshot, tmp_path):
    _configure([
        {"reasoning": "see what memory exists", "code": "index = list_memory_files()"},
        {"reasoning": "read the index file", "code": "body = read_memory_file(index[-1]['path'])"},
        {"reasoning": "read the transcript", "code": "chunk = read_transcript_chunk(0, 0, 200)"},
        {"reasoning": "draft the promotion", "code": "d = draft_memory_file(...)"},
        {"reasoning": "submit", "code": "SUBMIT(plan={...})"},
    ])
    held: dict = {}

    def step_list(tools, _variables):
        held["index"] = tools["list_memory_files"]()
        return str(held["index"])

    def step_read(tools, _variables):
        index_ref = next(e for e in held["index"] if e["kind"] == "index")
        return str(tools["read_memory_file"](path=index_ref["path"]))

    def step_chunk(tools, _variables):
        held["chunk"] = tools["read_transcript_chunk"](transcript_index=0, offset=0, limit=200)
        return str(held["chunk"])

    def step_draft(tools, _variables):
        held["draft"] = tools["draft_memory_file"](
            topic="the merge freeze",
            memory_type="project",
            evidence="the user said merges are frozen during a release",
        )
        return str(held["draft"])

    def step_submit(_tools, _variables):
        return {
            "plan": {
                "candidates": [
                    {
                        "action": "promote_to_memory",
                        "artifact_id": held["draft"]["artifact_id"],
                        "key_fields": {"transcripts": [0], "reason": "durable project policy"},
                    },
                    {
                        "action": "prune",
                        "key_fields": {"transcripts": [0], "reason": "repeated boilerplate turns"},
                    },
                ]
            }
        }

    interpreter = ScriptedInterpreter([step_list, step_read, step_chunk, step_draft, step_submit])
    task = _build(snapshot, interpreter=interpreter)
    path = str(tmp_path / "trace.jsonl")
    with TraceRecorder(path, run_id="r0", meta={"transcripts": 1}):
        result = asyncio.run(
            task.arun(
                transcripts=[_TRANSCRIPT],
                memory_index="- [index] MEMORY.md",
                project_instructions="",
            )
        )

    assert isinstance(result, DistillPlan)
    actions = [c.action for c in result.candidates]
    assert actions == ["promote_to_memory", "prune"]

    # the output_model carries judgement only — no drafted bytes anywhere in it
    dumped = result.model_dump()
    assert set(dumped) == {"candidates"}
    assert set(dumped["candidates"][0]) == {"action", "artifact_id", "key_fields"}
    assert "merge-freeze-policy" not in json.dumps(dumped)
    assert "metadata" not in json.dumps(dumped)

    # …and the draft IS re-sourceable from the trace, keyed by the candidate's artifact_id
    drafts = _payloads(path, "draft_memory_file")
    assert len(drafts) == 1
    assert drafts[0]["artifact_id"] == result.candidates[0].artifact_id
    assert drafts[0]["draft"] == _DRAFT and drafts[0]["ok"] is True

    # every read-only tool that ran recorded exactly one call
    assert len(_payloads(path, "list_memory_files")) == 1
    assert len(_payloads(path, "read_memory_file")) == 1
    assert _payloads(path, "read_memory_file")[0]["kind"] == "index"
    assert _payloads(path, "read_transcript_chunk")[0]["length"] == 200
    assert _payloads(path, "draft_skill_file") == []
    assert any(e["type"] == EVENT_RESULT for e in load_events(path))
    assert interpreter.calls                       # the loop really drove the injected double


def test_a_planner_that_reads_an_unlisted_path_is_refused_mid_run(snapshot, tmp_path):
    """The allowlist holds inside a real forward pass, not just in a direct unit call."""
    _configure([
        {"reasoning": "try to read something else", "code": "read_memory_file('/etc/passwd')"},
        {"reasoning": "submit", "code": "SUBMIT(plan={...})"},
    ])

    def step_read(tools, _variables):
        return str(tools["read_memory_file"](path="/etc/passwd"))

    task = _build(
        snapshot,
        interpreter=ScriptedInterpreter([
            step_read,
            {"plan": {"candidates": [{"action": "keep", "key_fields": {"reason": "refused"}}]}},
        ]),
    )
    path = str(tmp_path / "trace.jsonl")
    with TraceRecorder(path, run_id="r0"):
        result = asyncio.run(
            task.arun(transcripts=[_TRANSCRIPT], memory_index="(empty)", project_instructions="")
        )

    assert [c.action for c in result.candidates] == ["keep"]
    payload = _payloads(path, "read_memory_file")[0]
    assert payload["ok"] is False and "not in this run's memory index" in payload["note"]


def test_project_instructions_is_bound_as_a_real_repl_variable(snapshot, tmp_path):
    """The wiring the driver depends on: `project_instructions` must actually be a REPL variable
    the planner's own code can read, not just an argument nobody binds through to execution."""
    _configure([
        {"reasoning": "look at project_instructions", "code": "x = project_instructions"},
        {"reasoning": "submit", "code": "SUBMIT(plan={...})"},
    ])
    held: dict = {}

    def step_read(_tools, variables):
        held["seen"] = variables.get("project_instructions")
        return "ok"

    task = _build(snapshot, interpreter=ScriptedInterpreter([step_read, {"plan": {"candidates": []}}]))
    path = str(tmp_path / "trace.jsonl")
    with TraceRecorder(path, run_id="r0"):
        asyncio.run(
            task.arun(
                transcripts=[_TRANSCRIPT],
                memory_index="(empty)",
                project_instructions="# real project rules\n",
            )
        )
    assert held["seen"] == "# real project rules\n"
