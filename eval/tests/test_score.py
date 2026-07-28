"""`ctx_distillery_eval.score` — offline, with the default `StubJudge` (fixed, deterministic scores).

Hand-rolled trace-event dicts, matching the shape `rlm_kit.trace` actually records (`EVENT_RESULT`'s
`payload["output"]`) — fully offline, no dspy/model dependency, mirroring the root package's own
`tests/test_rubric.py` convention for building event lists without a real `TraceRecorder`.
"""

from __future__ import annotations

from ctx_distillery_eval.judge import EvalScore, JudgeVerdict, StubJudge
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

    def spy_judge(plan_text, transcript_texts, reference=""):
        seen["plan_text"] = plan_text
        seen["transcript_texts"] = transcript_texts
        seen["reference"] = reference
        return JudgeVerdict(ok=True, score=EvalScore(TF=1, TA=2, TG=3, PA=4, notes="spy"))

    row = score_run("r0", "trace.jsonl", events, ["transcript A", "transcript B"], judge=spy_judge)
    assert row.score.notes == "spy"
    assert seen["transcript_texts"] == ["transcript A", "transcript B"]
    assert "no candidates" in seen["plan_text"]
    assert seen["reference"] == ""  # no taskset -> no reference, and the v1 prompt is rendered


def test_score_run_forwards_a_taskset_reference_to_the_judge():
    """The judge-only ground truth reaches the judge and nothing else. It is passed POSITIONALLY, so
    a `Judge` that only accepts two arguments is a broken `Judge` — the protocol says three."""
    seen = {}

    def spy_judge(plan_text, transcript_texts, reference=""):
        seen["reference"] = reference
        return JudgeVerdict(ok=True, score=EvalScore(TF=1, TA=2, TG=3, PA=4, notes="spy"))

    score_run("r0", "t.jsonl", [_result(_plan_dict())], ["t"], judge=spy_judge,
              reference="expected: promote the merge freeze")
    assert seen["reference"] == "expected: promote the merge freeze"


# -- the unscored path (parity pass 4) ------------------------------------------------------------


def test_score_run_records_an_unscored_row_when_the_judge_fails():
    """The point of widening `Judge` to return a `JudgeVerdict`: a live judge that fails (endpoint
    down, off-schema reply, tripped breaker) lands as a row with NO score and the verdict's reason —
    never a fake 0, and never an exception that kills the rest of the batch."""
    def failing_judge(plan_text, transcript_texts, reference=""):
        return JudgeVerdict(ok=False, reason="judge endpoint error: connection refused")

    row = score_run("r0", "trace.jsonl", [_result(_plan_dict())], ["t"], judge=failing_judge)
    assert row.score is None and row.unscored is True
    assert row.unscored_reason == "judge endpoint error: connection refused"


def test_score_run_supplies_a_reason_for_a_judge_that_fails_without_one():
    """`EvalRow` REFUSES a blank unscored row, so a third-party `Judge` returning `ok=False` with an
    empty reason would raise a `ValidationError` here instead of degrading. Belt and braces."""
    row = score_run("r0", "trace.jsonl", [_result(_plan_dict())], ["t"],
                    judge=lambda p, t, r="": JudgeVerdict(ok=False))
    assert row.score is None and row.unscored_reason == "judge returned no score"


def test_score_run_treats_an_ok_verdict_with_no_score_as_unscored():
    """`ok=True` with `score=None` is not a score — the two conditions are checked together."""
    row = score_run("r0", "trace.jsonl", [_result(_plan_dict())], ["t"],
                    judge=lambda p, t, r="": JudgeVerdict(ok=True, score=None, reason="empty"))
    assert row.score is None and row.unscored_reason == "empty"


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


def test_score_run_survives_a_non_dict_trace_line():
    """A DIFFERENT failure mode from the malformed-`output` case above, and it has to be said out
    loud because the two look alike: that one is a well-formed dict of the WRONG SHAPE (caught by
    `plan_from_events`'s `ValidationError` degrade); this one is a trace LINE that is valid JSON but
    not an object at all (`42`, `null`, `"x"`, `[1,2,3]`), which `rlm_kit.trace.load_events` passes
    through unfiltered. It raised in `ctx_distillery.session.assemble`, not in the plan
    reconstruction — `plan_from_events` had already returned by then."""
    events = [42, _result(_plan_dict({"action": "keep"})), None, "x", [1, 2, 3]]
    row = score_run("r0", "trace.jsonl", events, ["transcript text"])
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


def test_render_plan_is_the_shared_implementation_not_a_local_copy():
    """`render_plan` was DEFINED here until the CLI needed the identical rendering — a reviewer
    running `ctx-distillery show` should read exactly what the judge reads. It now lives in
    `ctx_distillery.render` and this module re-exports it, the same de-duplication `CLAUDE.md`
    invariant 11 already required of `plan_from_events` and `load_trace`. Pinned by identity so a
    future local copy is a failure here, not a silent divergence in what the judge is shown."""
    from ctx_distillery import render as shared

    assert render_plan is shared.render_plan


def test_render_plan_still_reports_a_run_level_problem_with_no_candidates():
    """FIXED while promoting this function: the no-candidates branch used to `return` early and drop
    the run-level problems line, so a run that died before SUBMIT was judged against a bare
    "proposed no candidates" that never said why. The judge (and the reviewer) now see the reason."""
    from ctx_distillery.session import assemble

    assembled = assemble([], None)
    text = render_plan(assembled)
    assert "no candidates" in text
    assert "no plan was produced by this run" in text


def test_aggregate_computes_means_reward_free():
    rows = [
        score_run("r1", "a.jsonl", [_result(_plan_dict())], ["t"], judge=StubJudge(tf=8, ta=6, tg=7, pa=9)),
        score_run("r2", "b.jsonl", [_result(_plan_dict())], ["t"], judge=StubJudge(tf=4, ta=6, tg=7, pa=5)),
    ]
    report = aggregate(rows)
    assert len(report.rows) == 2
    assert report.means == {"TF": 6.0, "TA": 6.0, "TG": 7.0, "PA": 7.0}
    assert report.n == 2 and report.n_unscored == 0


def test_aggregate_of_zero_rows_has_empty_means():
    report = aggregate([])
    assert report.rows == [] and report.means == {}
    assert report.n == 0 and report.n_unscored == 0


def test_aggregate_excludes_unscored_rows_from_the_mean_and_its_denominator():
    """The arithmetic half of "unscored, never a fake 0": an excluded row must leave the DENOMINATOR
    too. Counting it there is numerically identical to scoring it 0, which is the exact lie this
    shape exists to prevent — here, one 8.0 and one failure means a mean of 8.0, not 4.0."""
    rows = [
        score_run("r1", "a.jsonl", [_result(_plan_dict())], ["t"], judge=StubJudge(tf=8, ta=8, tg=8, pa=8)),
        score_run("r2", "b.jsonl", [_result(_plan_dict())], ["t"],
                  judge=lambda p, t, r="": JudgeVerdict(ok=False, reason="judge endpoint error: boom")),
    ]
    report = aggregate(rows)
    assert report.means == {"TF": 8.0, "TA": 8.0, "TG": 8.0, "PA": 8.0}
    assert report.n == 2 and report.n_unscored == 1
    assert len(report.rows) == 2  # the failed run is REPORTED, not dropped from the listing


def test_aggregate_of_only_unscored_rows_has_empty_means_not_zeros():
    rows = [score_run("r1", "a.jsonl", [_result(_plan_dict())], ["t"],
                      judge=lambda p, t, r="": JudgeVerdict(ok=False, reason="off-schema"))]
    report = aggregate(rows)
    assert report.means == {} and report.n == 1 and report.n_unscored == 1


def test_aggregate_records_the_judge_provenance_it_is_given():
    """Without `prompt_version` a number is not attributable to the prompt that produced it — which
    is the entire reason `judge.PROMPT_VERSION` exists."""
    report = aggregate([], judge_model="judge-model-x", prompt_version="atlas-ctxd-eval-v1")
    assert report.judge_model == "judge-model-x"
    assert report.prompt_version == "atlas-ctxd-eval-v1"


def test_ctx_distillery_eval_score_no_longer_defines_its_own_plan_from_events():
    """Regression guard, added for the Studio pass's step-0 refactor: `score.py` used to keep its OWN
    local `_plan_from_events` copy (duplicate reconstruction + `ValidationError`-degrade logic) rather
    than importing the now-public `ctx_distillery.rubric.plan_from_events`. If a future edit
    accidentally re-adds a local copy here, drift between the two reconstructions (e.g. a bug fixed in
    one but not the other, exactly as happened once already) would silently return."""
    import ctx_distillery_eval.score as score_mod

    import ctx_distillery.rubric as rubric_mod

    assert not hasattr(score_mod, "_plan_from_events")
    # `plan_from_events` IS a name in this module's namespace (imported), but it must be THE SAME
    # function object `ctx_distillery.rubric` defines — never a locally re-defined shadow.
    assert score_mod.plan_from_events is rubric_mod.plan_from_events
