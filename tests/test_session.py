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
    DistillArtifacts,
    assemble,
    render_memory_index,
    run_distillation,
    run_distillation_artifacts,
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


def _tool_call(tool, artifact_id, *, draft=_DRAFT, ok=True, errors=(), **extra):
    """`extra` carries the two infra fields `drafting.py` records beside `ok` —
    `endpoint_error` / `circuit_broken` — which is how `assemble` tells the THREE causes of
    `ok=False` apart (see `schema._not_ok_problem`)."""
    return {
        "type": EVENT_TOOL_CALL,
        "payload": {
            "tool": tool,
            "ok": ok,
            "artifact_id": artifact_id,
            "draft": draft,
            "errors": list(errors),
            **extra,
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
    assert any("failed its format check" in p for p in candidate.problems)


def test_an_endpoint_failure_is_not_reported_as_a_format_check_failure():
    """`make_model_tool` sets `ok=False` for THREE causes, and this one never reaches the validator.

    Reproduced before fixing: a bare connection failure rendered as ``artifact 'a1' failed its
    format check: Connection refused`` — blaming the model for an infrastructure fault, in the exact
    text `studio/`'s PLAN panel shows a human deciding what to apply (and, via
    `apply._blocking_problem`, in the refusal reason too).
    """
    events = [_tool_call("draft_memory_file", "a1", draft="", ok=False,
                         errors=["Connection refused"], endpoint_error="Connection refused")]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "a1"}))
    problems = out.candidates[0].problems
    cause = next(p for p in problems if "Connection refused" in p)
    assert "failed its format check" not in cause
    assert "endpoint failed" in cause and "no format check ever ran" in cause


def test_a_circuit_break_is_not_reported_as_a_format_check_failure():
    """The breaker short-circuits BEFORE the model is called at all (`raw=""`), so neither the
    drafter nor the validator produced this outcome. `rubric.py`'s TA criterion already names the
    breaker honestly; this is the same vocabulary on the human-visible surface."""
    events = [_tool_call("draft_skill_file", "s1", draft="", ok=False,
                         errors=["circuit breaker: 3 consecutive invalid outputs"],
                         circuit_broken=True)]
    out = assemble(events, _plan({"action": "promote_to_skill", "artifact_id": "s1"}))
    cause = next(p for p in out.candidates[0].problems if "circuit breaker" in p)
    assert "failed its format check" not in cause
    assert "tripped the circuit breaker" in cause and "never called" in cause


def test_an_endpoint_failure_that_STRINGIFIED_TO_NOTHING_is_still_an_endpoint_failure():
    """`endpoint_error` is `Optional[str]` and rlm-kit sets `str(exc)` — which is `''` for a whole
    family of real faults: `httpx.ConnectTimeout`/`ReadTimeout`/`ConnectError`, `TimeoutError`,
    `OSError`, `RemoteDisconnected`. A truthiness test dropped every one of them through to the
    validator branch, rendering a dropped connection as ``failed its format check: no detail
    recorded`` — invariant 12's harm, arrived at through an empty string instead of a wrong branch.
    """
    events = [_tool_call("draft_memory_file", "a1", draft="", ok=False, endpoint_error="")]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "a1"}))
    cause = next(p for p in out.candidates[0].problems if "never drafted" in p)
    assert "failed its format check" not in cause
    assert "endpoint failed" in cause and "no format check ever ran" in cause


def test_a_circuit_break_outranks_an_endpoint_error_in_the_problem_line():
    """The documented precedence, PINNED — swapping the two branches used to leave everything green.

    `circuit_broken` wins because it is the stronger claim (the model was never called at all).
    `make_model_tool` never sets both, so only a hand-written payload gets here; the point is that
    the ORDER is a decision this module states, and `rl_export._draft_cause`'s twin chain is already
    pinned the same way by `test_run_metrics_causes_partition_the_aggregate`.
    """
    events = [_tool_call("draft_memory_file", "a1", draft="", ok=False,
                         errors=["both"], endpoint_error="502", circuit_broken=True)]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "a1"}))
    cause = next(p for p in out.candidates[0].problems if "never drafted" in p)
    assert "tripped the circuit breaker" in cause
    assert "endpoint failed" not in cause


def test_all_three_ok_false_causes_still_agree_that_the_draft_is_not_ok():
    """The LABELS differ; `draft_ok` deliberately does not. It answers "did this call yield usable
    bytes", which is the same answer for all three — and is what `apply_plan` and the studio's
    `applyBlocker` both key on."""
    for extra in ({}, {"endpoint_error": "boom"}, {"circuit_broken": True}):
        events = [_tool_call("draft_memory_file", "a1", ok=False, errors=["x"], **extra)]
        out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "a1"}))
        assert out.candidates[0].draft_ok is False and out.candidates[0].problems


def test_an_empty_draft_is_flagged():
    events = [_tool_call("draft_memory_file", "abc123", draft="  \n")]
    out = assemble(events, _plan({"action": "promote_to_memory", "artifact_id": "abc123"}))
    assert any("empty draft" in p for p in out.candidates[0].problems)


def test_no_plan_at_all_is_a_run_level_problem():
    out = assemble([], None)  # type: ignore[arg-type]
    assert out.candidates == [] and out.problems


@pytest.mark.parametrize("action", ["keep", "promote_to_memory"])
def test_assemble_ignores_non_dict_trace_lines(action):
    """`assemble`'s "none of them raise" was literally false for a malformed trace: `_draft_calls`
    scans EVERY event before the candidate loop, so a line that is valid JSON but not an object
    (`rlm_kit.trace.load_events` does no shape validation) raised a raw `AttributeError` for ANY
    non-`None` plan — including an all-`keep` plan with no artifact to assemble at all, which is why
    `keep` is parametrized here and not just the promotion. Only `assemble(events, None)` escaped,
    and only because it returns before touching `events`.
    """
    artifact_id = "abc123" if action == "promote_to_memory" else None
    events = [42, _tool_call("draft_memory_file", "abc123"), None, "x", [1, 2, 3]]
    out = assemble(events, _plan({"action": action, "artifact_id": artifact_id}))
    assert out.problems == [] and out.candidates[0].problems == []
    assert out.candidates[0].draft == (_DRAFT if action == "promote_to_memory" else None)


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


# -- run_distillation_artifacts: the same run, with what it was drawn from ----------------------


def test_run_distillation_artifacts_returns_the_redacted_transcripts_the_run_saw(memory_dir, tmp_path):
    """The reason this function exists: the judge (`eval/`'s `run`) must read the SAME redaction the
    planner did. Re-`ingest()`ing and re-`redact()`ing would score against a different one, and the
    trace records only offset/length metadata for a transcript, never the body — so a trace-sourced
    substitute would be EMPTY, not merely lossier."""
    _configure([{"reasoning": "submit", "code": "SUBMIT(plan={...})"}])
    adapter = ClaudeCodeAdapter(memory_dir, transcripts=["user: the key is " + _SECRET + "\n"])
    artifacts = asyncio.run(
        run_distillation_artifacts(
            adapter,
            lambda spec: _DRAFT,
            str(tmp_path / "trace.jsonl"),
            run_id="r0",
            interpreter=ScriptedInterpreter([{"plan": {"candidates": []}}]),
        )
    )
    assert isinstance(artifacts, DistillArtifacts)
    assert len(artifacts.transcripts) == 1
    assert _SECRET not in artifacts.transcripts[0]
    assert "[REDACTED:api_key]" in artifacts.transcripts[0]
    assert artifacts.run_id == "r0"
    assert artifacts.trace_path == str(tmp_path / "trace.jsonl")
    assert artifacts.events and all(isinstance(e, dict) for e in artifacts.events)
    assert isinstance(artifacts.plan, AssembledPlan)
    assert artifacts.memory_index == adapter.list_targets()


def test_run_distillation_artifacts_reports_a_generated_run_id(memory_dir, tmp_path):
    """A caller passing `run_id=None` could previously never learn which id was generated for it —
    and that id is the key every downstream reader (`load_trace(..., run_id=)`, the eval's task
    pairing, the studio's replay) is keyed on."""
    _configure([{"reasoning": "submit", "code": "SUBMIT(plan={...})"}])
    adapter = ClaudeCodeAdapter(memory_dir, transcripts=["t"])
    artifacts = asyncio.run(
        run_distillation_artifacts(
            adapter,
            lambda spec: _DRAFT,
            str(tmp_path / "trace.jsonl"),
            interpreter=ScriptedInterpreter([{"plan": {"candidates": []}}]),
        )
    )
    assert artifacts.run_id
    assert {e.get("run_id") for e in artifacts.events} == {artifacts.run_id}


def test_run_distillation_is_a_thin_wrapper_returning_exactly_the_same_plan(memory_dir, tmp_path):
    """`run_distillation`'s signature AND return type are UNCHANGED — the artifacts function was
    ADDED beside it, not folded into it, so no existing caller needed an edit."""

    def drive(fn, name):
        # Re-scripted per drive: `scripted_lm` hands out a FIXED list of turns, so the second run in
        # one test would otherwise fall off the end of the first's script.
        _configure([{"reasoning": "submit", "code": "SUBMIT(plan={...})"}])
        return asyncio.run(
            fn(
                ClaudeCodeAdapter(memory_dir, transcripts=["t"]),
                lambda spec: _DRAFT,
                str(tmp_path / f"{name}.jsonl"),
                run_id=name,
                interpreter=ScriptedInterpreter([{"plan": {"candidates": []}}]),
            )
        )

    plan = drive(run_distillation, "wrapper")
    artifacts = drive(run_distillation_artifacts, "full")
    assert isinstance(plan, AssembledPlan)
    assert plan == artifacts.plan
