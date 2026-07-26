"""`assemble` + `run_distillation` — the read side of the label/bytes contract, and redaction.

`assemble`'s job is that a plan's `artifact_id` label can never drift from the bytes it describes: the
text always comes from the drafting `tool_call` event, and a candidate that cannot be backed by one is
reported as a problem instead of trusted. `run_distillation`'s job is the redaction property — a
secret planted in a RAW transcript must not appear anywhere in the trace.
"""

from __future__ import annotations

import asyncio
import json

import pytest

dspy = pytest.importorskip("dspy")

import rlm_kit.runtime as rt
from rlm_kit import RLMConfig
from rlm_kit.testing import ScriptedInterpreter, scripted_lm
from rlm_kit.trace import EVENT_TOOL_CALL, load_events

from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter
from ctx_distillery.session import (
    AssembledPlan,
    assemble,
    render_memory_index,
    run_distillation,
)
from ctx_distillery.task import DistillCandidate, DistillPlan

_SECRET = "sk-abcdefghijklmnopqrstuvwx1234567890"

_DRAFT = (
    "---\n"
    "name: merge-freeze-policy\n"
    "description: Merges are frozen during a release.\n"
    "metadata:\n"
    "  type: project\n"
    "---\n"
    "Merges into main are frozen for the duration of a release.\n"
)


def _tool_call(tool, artifact_id, *, draft=_DRAFT, ok=True, errors=()):
    return {
        "type": EVENT_TOOL_CALL,
        "payload": {
            "tool": tool,
            "ok": ok,
            "artifact_id": artifact_id,
            "draft": draft,
            "errors": list(errors),
        },
    }


def _plan(*candidates):
    return DistillPlan(candidates=[DistillCandidate(**c) for c in candidates])


# -- assemble --------------------------------------------------------------------------------


def test_a_matching_artifact_id_is_assembled_from_the_event():
    events = [_tool_call("draft_memory_file", "abc123")]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "abc123"}))
    assert isinstance(out, AssembledPlan) and out.problems == []
    candidate = out.candidates[0]
    assert candidate.draft == _DRAFT and candidate.draft_ok is True
    assert candidate.problems == []


def test_the_last_call_for_an_artifact_id_wins():
    events = [
        _tool_call("draft_memory_file", "abc123", draft="old\n"),
        _tool_call("draft_memory_file", "abc123", draft=_DRAFT),
    ]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "abc123"}))
    assert out.candidates[0].draft == _DRAFT


def test_a_missing_tool_call_is_a_problem_not_an_exception():
    out = assemble([], _plan({"action": "promote_to_memory", "artifact_id": "ghost"}))
    candidate = out.candidates[0]
    assert candidate.draft is None
    assert any("never drafted" in p for p in candidate.problems)


def test_a_mismatched_kind_is_reported_distinctly_from_a_fabricated_id():
    """The id exists — but it was drafted by the OTHER tool, which means something different."""
    events = [_tool_call("draft_skill_file", "abc123")]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "abc123"}))
    problems = out.candidates[0].problems
    assert any("drafted by draft_skill_file" in p for p in problems)
    assert not any("never drafted" in p for p in problems)


def test_a_skill_candidate_assembles_from_the_skill_tool():
    events = [_tool_call("draft_skill_file", "s1", draft="---\nname: s\ndescription: d\n---\nb\n")]
    out = assemble(events, _plan({"action": "promote_to_skill", "artifact_id": "s1"}))
    assert out.candidates[0].draft_ok is True and out.candidates[0].problems == []


@pytest.mark.parametrize("action", ["keep", "prune"])
def test_keep_and_prune_need_no_artifact(action):
    out = assemble([], _plan({"action": action, "key_fields": {"reason": "stale"}}))
    candidate = out.candidates[0]
    assert candidate.draft is None and candidate.problems == []
    assert candidate.key_fields == {"reason": "stale"}


@pytest.mark.parametrize("action", ["keep", "prune"])
def test_a_keep_or_prune_candidate_carrying_an_artifact_id_is_flagged(action):
    events = [_tool_call("draft_memory_file", "abc123")]
    out = assemble(events, _plan({"action": action, "artifact_id": "abc123"}))
    assert any("only ['promote_to_memory', 'promote_to_skill'] draft" in p
               for p in out.candidates[0].problems)


def test_a_promotion_with_no_artifact_id_is_flagged():
    out = assemble([], _plan({"action": "promote_to_memory"}))
    assert any("carries no artifact_id" in p for p in out.candidates[0].problems)


def test_a_failed_format_check_is_carried_through_with_its_errors():
    events = [_tool_call("draft_memory_file", "abc123", ok=False, errors=["bad metadata.type"])]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "abc123"}))
    candidate = out.candidates[0]
    assert candidate.draft_ok is False
    assert any("bad metadata.type" in p for p in candidate.problems)


def test_an_empty_draft_is_flagged():
    events = [_tool_call("draft_memory_file", "abc123", draft="  \n")]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "abc123"}))
    assert any("empty draft" in p for p in out.candidates[0].problems)


def test_no_plan_at_all_is_a_run_level_problem():
    out = assemble([], None)  # type: ignore[arg-type]
    assert out.candidates == [] and out.problems


def test_non_tool_call_events_are_ignored():
    events = [{"type": "main_step", "payload": {"tool": "draft_memory_file", "artifact_id": "x"}}]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "x"}))
    assert any("never drafted" in p for p in out.candidates[0].problems)


# -- render_memory_index ---------------------------------------------------------------------


def test_render_memory_index_lists_every_kind(snapshot):
    text = render_memory_index(snapshot)
    assert "[memory] project-conventions" in text
    assert "[index] MEMORY.md" in text


def test_render_memory_index_says_so_when_empty():
    assert "empty" in render_memory_index([])


# -- run_distillation ------------------------------------------------------------------------


def _configure(turns):
    dummy = scripted_lm(turns)
    rt.configure(
        RLMConfig(main_model="x", sub_model="x", interpreter="mock", observe=False),
        main_lm=dummy,
        sub_lm=dummy,
    )


def test_run_distillation_ingests_once_redacts_and_assembles(memory_dir, tmp_path):
    _configure([
        {"reasoning": "read the transcript", "code": "chunk = read_transcript_chunk(0)"},
        {"reasoning": "draft it", "code": "d = draft_memory_file(...)"},
        {"reasoning": "submit", "code": "SUBMIT(plan={...})"},
    ])
    raw_transcript = (
        "user: the release key is " + _SECRET + "\n"
        "user: also, merges are frozen during a release\n"
    )

    class CountingAdapter(ClaudeCodeAdapter):
        ingests = 0

        def ingest(self):
            type(self).ingests += 1
            return super().ingest()

    adapter = CountingAdapter(memory_dir, transcripts=[raw_transcript])
    held: dict = {}

    def step_chunk(tools, _variables):
        held["chunk"] = tools["read_transcript_chunk"](transcript_index=0)
        return str(held["chunk"])

    def step_draft(tools, _variables):
        held["draft"] = tools["draft_memory_file"](
            topic="the merge freeze", memory_type="project", evidence="frozen during a release"
        )
        return str(held["draft"])

    def step_submit(_tools, _variables):
        return {
            "plan": {
                "candidates": [
                    {
                        "action": "promote_to_memory",
                        "artifact_id": held["draft"]["artifact_id"],
                        "key_fields": {"transcripts": [0]},
                    },
                    {"action": "prune", "key_fields": {"reason": "credential paste"}},
                ]
            }
        }

    trace = str(tmp_path / "trace.jsonl")
    out = asyncio.run(
        run_distillation(
            adapter,
            lambda spec: _DRAFT,
            trace,
            run_id="r0",
            interpreter=ScriptedInterpreter([step_chunk, step_draft, step_submit]),
        )
    )

    assert CountingAdapter.ingests == 1                     # ingest() called EXACTLY once
    assert [c.action for c in out.candidates] == ["promote_to_memory", "prune"]
    assert out.candidates[0].draft == _DRAFT                # re-sourced from the trace
    assert out.candidates[0].problems == [] and out.problems == []

    # the redaction property: the planted secret is nowhere in the trace…
    events = load_events(trace, run_id="r0")
    blob = json.dumps(events)
    assert _SECRET not in blob
    assert "[REDACTED:api_key]" in blob                      # …but the placeholder made it through
    # …and the chunk the tool handed the planner was the redacted text
    assert _SECRET not in held["chunk"]["text"]
    assert "[REDACTED:api_key]" in held["chunk"]["text"]


def test_a_custom_redactor_is_honoured(memory_dir, tmp_path):
    _configure([{"reasoning": "submit", "code": "SUBMIT(plan={...})"}])
    adapter = ClaudeCodeAdapter(memory_dir, transcripts=["anything at all"])
    seen: list[str] = []

    def redact(text):
        seen.append(text)
        return "SCRUBBED"

    held: dict = {}

    def step_submit(tools, _variables):
        held["chunk"] = tools["read_transcript_chunk"](transcript_index=0)
        return {"plan": {"candidates": []}}

    out = asyncio.run(
        run_distillation(
            adapter,
            lambda spec: _DRAFT,
            str(tmp_path / "trace.jsonl"),
            redact=redact,
            run_id="r0",
            interpreter=ScriptedInterpreter([step_submit]),
        )
    )
    assert seen == ["anything at all"]
    assert held["chunk"]["text"] == "SCRUBBED"
    assert out.candidates == []


def test_run_distillation_writes_nothing_into_the_memory_store(memory_dir, tmp_path):
    _configure([{"reasoning": "submit", "code": "SUBMIT(plan={...})"}])
    before = sorted(p.name for p in memory_dir.iterdir())
    adapter = ClaudeCodeAdapter(memory_dir, transcripts=["t"])
    asyncio.run(
        run_distillation(
            adapter,
            lambda spec: _DRAFT,
            str(tmp_path / "trace.jsonl"),
            run_id="r0",
            interpreter=ScriptedInterpreter([{"plan": {"candidates": []}}]),
        )
    )
    assert sorted(p.name for p in memory_dir.iterdir()) == before
