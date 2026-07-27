"""`ctx_distillery_eval.score` — offline, with the default `StubJudge` (fixed, deterministic scores).

Hand-rolled trace-event dicts, matching the shape `rlm_kit.trace` actually records (`EVENT_RESULT`'s
`payload["output"]`) — fully offline, no dspy/model dependency, mirroring the root package's own
`tests/test_rubric.py` convention for building event lists without a real `TraceRecorder`.
"""

from __future__ import annotations

from ctx_distillery_eval.judge import EvalScore, StubJudge
from ctx_distillery_eval.score import aggregate, render_plan, score_run
from rlm_kit.trace import EVENT_RESULT

from ctx_distillery.task import DistillCandidate, DistillPlan


def _result(plan: dict) -> dict:
    return {"type": EVENT_RESULT, "payload": {"output": plan}}


def _plan_dict(*candidates: dict) -> dict:
    return DistillPlan(candidates=[DistillCandidate(**c) for c in candidates]).model_dump()


def test_score_run_uses_the_stub_judge_by_default():
    events = [_result(_plan_dict({"action": "keep", "key_fields": {"reason": "still true"}}))]
    row = score_run("r0", "trace.jsonl", events, ["some transcript text"])
    assert row.run_id == "r0" and row.trace_path == "trace.jsonl"
    assert isinstance(row.score, EvalScore)
    assert row.score.notes == "stub judge — fixed deterministic scores"


def test_score_run_passes_a_custom_judge_through():
    events = [_result(_plan_dict())]
    seen = {}

    def spy_judge(plan_text, transcript_texts):
        seen["plan_text"] = plan_text
        seen["transcript_texts"] = transcript_texts
        return EvalScore(TF=1, TA=2, TG=3, PA=4, notes="spy")

    row = score_run("r0", "trace.jsonl", events, ["transcript A", "transcript B"], judge=spy_judge)
    assert row.score.notes == "spy"
    assert seen["transcript_texts"] == ["transcript A", "transcript B"]
    assert "no candidates" in seen["plan_text"]


def test_score_run_handles_a_missing_result_event_as_no_plan():
    """No `result` event at all -> `_plan_from_events` returns None -> `assemble` reports a
    run-level problem instead of raising; the judge still gets called with SOMETHING to read."""
    row = score_run("r0", "trace.jsonl", [], ["transcript text"])
    assert isinstance(row.score, EvalScore)


def test_score_run_handles_a_malformed_result_output_without_raising():
    """FIXED per adversarial review: a `result` event whose `output` is a dict but the WRONG shape
    (missing the required `action` field) used to propagate a raw `pydantic.ValidationError`
    uncaught — reproduced end-to-end, where scoring a glob of one good + one malformed trace killed
    the whole batch. Must degrade to a run-level problem, exactly like a missing result event."""
    malformed = _result({"candidates": [{"artifact_id": "x"}]})
    row = score_run("r0", "trace.jsonl", [malformed], ["transcript text"])
    assert isinstance(row.score, EvalScore)


def _tool_call_stub(tool, artifact_id, draft="---\nname: x\ndescription: d\n---\nbody\n"):
    from rlm_kit.trace import EVENT_TOOL_CALL

    return {
        "type": EVENT_TOOL_CALL,
        "payload": {"tool": tool, "ok": True, "artifact_id": artifact_id, "draft": draft, "errors": []},
    }


def test_render_plan_lists_every_candidate_with_its_key_fields_and_draft():
    plan = DistillPlan(
        candidates=[
            DistillCandidate(action="promote_to_memory", artifact_id="abc123"),
            DistillCandidate(action="prune", key_fields={"target_path": "/memory/stale.md"}),
        ]
    )
    events = [_tool_call_stub("draft_memory_file", "abc123")]
    from ctx_distillery.session import assemble

    assembled = assemble(events, plan)
    text = render_plan(assembled)
    assert "action=promote_to_memory" in text
    assert "action=prune" in text
    assert "target_path" in text


def test_render_plan_says_so_when_there_are_no_candidates():
    from ctx_distillery.session import AssembledPlan

    assert "no candidates" in render_plan(AssembledPlan())


def test_aggregate_computes_means_reward_free():
    rows = [
        score_run("r1", "a.jsonl", [_result(_plan_dict())], ["t"], judge=StubJudge(tf=8, ta=6, tg=7, pa=9)),
        score_run("r2", "b.jsonl", [_result(_plan_dict())], ["t"], judge=StubJudge(tf=4, ta=6, tg=7, pa=5)),
    ]
    report = aggregate(rows)
    assert len(report.rows) == 2
    assert report.means == {"TF": 6.0, "TA": 6.0, "TG": 7.0, "PA": 7.0}


def test_aggregate_of_zero_rows_has_empty_means():
    report = aggregate([])
    assert report.rows == [] and report.means == {}
