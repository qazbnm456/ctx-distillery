"""`ctx_distillery.rubric` — the ATLAS TF/TA/TG/PA facts, sourced from a run's trace.

Corrected per an implementation-plan audit: the main case matrix follows
`tests/test_session.py`'s REAL established convention (a hand-rolled `_tool_call()`-style dict
helper), not `TraceRecorder` — `tests/test_apply.py` (the originally-cited precedent) doesn't
build event lists at all. The ONE exception is `test_plan_from_events_round_trips_through_a_real_recorder`,
which needs a genuine `TraceRecorder` + `record_result` round trip (confirming
`DistillPlan.model_validate(payload["output"])` actually survives real pydantic serialization) —
that test alone builds a real recorder; the rest of this file does not generalize it.
"""

from __future__ import annotations

from rlm_kit.rubric import Criterion, CriterionFact, RubricCriteria
from rlm_kit.trace import EVENT_RESULT, EVENT_RUN_START, EVENT_TOOL_CALL, TraceRecorder, load_events

from ctx_distillery.rubric import (
    _CATEGORY_LENS,
    CRITERION_CATEGORIES,
    criteria_facts,
    default_rubric,
    plan_from_events,
    rubric_from_meta,
    rubric_to_meta,
    trace_facts,
    validate_rubric,
)
from ctx_distillery.task import DistillCandidate, DistillPlan

_DRAFT = (
    "---\n"
    "name: merge-freeze-policy\n"
    "description: Merges are frozen during a release.\n"
    "metadata:\n"
    "  type: project\n"
    "---\n"
    "Merges into main are frozen for the duration of a release.\n"
)


def _tool_call(tool, artifact_id=None, *, step_id=0, draft=_DRAFT, ok=True, errors=(), circuit_broken=False):
    """Same hand-rolled shape as `test_session.py`'s `_tool_call`, plus `step_id` (the trace envelope
    field `trace_facts` orders on) and `circuit_broken` (a drafting-call payload field)."""
    return {
        "type": EVENT_TOOL_CALL,
        "step_id": step_id,
        "payload": {
            "tool": tool,
            "ok": ok,
            "artifact_id": artifact_id,
            "draft": draft,
            "errors": list(errors),
            "circuit_broken": circuit_broken,
        },
    }


def _result(plan: dict):
    """A hand-rolled `result` event carrying a plan's `model_dump()`-shaped output — the ONE fact
    `plan_from_events` reads (`payload["output"]`), built by hand rather than through a real
    `TraceRecorder`, per the file docstring."""
    return {"type": EVENT_RESULT, "payload": {"output": plan}}


def _plan_dict(*candidates: dict) -> dict:
    return DistillPlan(candidates=[DistillCandidate(**c) for c in candidates]).model_dump()


# -- default_rubric / validate_rubric ---------------------------------------------------------


def test_default_rubric_covers_all_four_categories_with_observable_descriptions():
    rubric = default_rubric()
    assert isinstance(rubric, RubricCriteria)
    assert {c.category for c in rubric.criteria} == set(CRITERION_CATEGORIES)
    assert validate_rubric(rubric) == []


def test_validate_rubric_flags_a_missing_category():
    rubric = RubricCriteria(
        criteria=[Criterion(name="x", category="TF", description="a plan candidate exists")]
    )
    issues = validate_rubric(rubric)
    assert any("categories not represented" in i for i in issues)


# -- rubric_to_meta / rubric_from_meta round trip (via run_start, hand-rolled) ----------------


def test_rubric_to_meta_round_trips_through_run_start_meta():
    rubric = default_rubric()
    events = [{"type": EVENT_RUN_START, "payload": {"meta": {"rubric": rubric_to_meta(rubric)}}}]
    recovered = rubric_from_meta(events)
    assert [c.name for c in recovered.criteria] == [c.name for c in rubric.criteria]
    assert {c.category for c in recovered.criteria} == set(CRITERION_CATEGORIES)


def test_rubric_from_meta_is_empty_when_no_run_start_carries_one():
    assert rubric_from_meta([]).criteria == []


# -- plan_from_events --------------------------------------------------------------------------


def test_plan_from_events_reconstructs_the_last_result_events_plan():
    plan = _plan_dict({"action": "promote_to_memory", "artifact_id": "abc123"})
    events = [_result({"candidates": []}), _result(plan)]  # LAST result wins
    recovered = plan_from_events(events)
    assert isinstance(recovered, DistillPlan)
    assert recovered.candidates[0].artifact_id == "abc123"


def test_plan_from_events_is_none_with_no_result_event():
    assert plan_from_events([]) is None


def test_plan_from_events_is_none_with_a_malformed_result_output():
    """FIXED per adversarial review: a well-formed dict with the WRONG shape (missing a required
    field, unlike "not a dict at all" or "no result event") used to propagate a raw
    `pydantic.ValidationError` uncaught — reproduced end-to-end via the eval CLI, where ONE
    malformed trace in a glob took the entire scoring batch down. Must degrade to None, matching
    `assemble(events, None)`'s own "none of them raise" philosophy."""
    malformed = {"candidates": [{"artifact_id": "x"}]}  # missing the REQUIRED `action` field
    assert plan_from_events([_result(malformed)]) is None


def test_plan_from_events_ignores_a_non_dict_trace_line():
    """A SECOND, distinct malformed-trace mode from the `ValidationError` case above: a line that is
    valid JSON but not an object at all (`rlm_kit.trace.load_events` does no shape validation).

    It raised ORDER-DEPENDENTLY, which is why it hid: `reversed()` returns at the first `result`
    event, so a bad line BEFORE the last result was never visited and the bug looked absent, while
    one AFTER it (trailing garbage, a truncated tail, a concatenated file) crashed. Both orderings
    are asserted, or this test would pass against the unfixed code.
    """
    plan = _plan_dict({"action": "promote_to_memory", "artifact_id": "abc123"})
    before = plan_from_events([42, None, "x", [1, 2, 3], _result(plan)])
    after = plan_from_events([_result(plan), 42, None, "x", [1, 2, 3]])
    assert isinstance(before, DistillPlan) and isinstance(after, DistillPlan)
    assert before.candidates[0].artifact_id == after.candidates[0].artifact_id == "abc123"


def test_plan_from_events_is_none_when_the_trace_is_only_non_dict_lines():
    """No result event to find, and the scan must not raise on the way to saying so."""
    assert plan_from_events([42, None, "x", [1, 2, 3]]) is None


def test_plan_from_events_round_trips_through_a_real_recorder(tmp_path):
    """The one genuine round trip: a REAL `TraceRecorder.record_result` call, then re-read via
    `load_events` — confirms pydantic nested-model serialization actually survives the trip, which a
    hand-rolled dict could silently fail to prove (see the file docstring)."""
    plan = DistillPlan(
        candidates=[
            DistillCandidate(
                action="promote_to_skill",
                artifact_id="s1",
                key_fields={"scope": "global", "note": "nested field survives too"},
            )
        ]
    )
    trace_path = str(tmp_path / "trace.jsonl")
    with TraceRecorder(trace_path, run_id="r0") as rec:
        rec.record_result(plan)
    events = load_events(trace_path, run_id="r0")

    recovered = plan_from_events(events)
    assert isinstance(recovered, DistillPlan)
    assert recovered.candidates[0].action == "promote_to_skill"
    assert recovered.candidates[0].artifact_id == "s1"
    assert recovered.candidates[0].key_fields == {"scope": "global", "note": "nested field survives too"}


# -- trace_facts: the main case matrix (hand-rolled tool_call dicts) --------------------------


def test_an_all_keep_plan_has_no_non_keep_candidates_and_no_promotions_backed():
    plan = _plan_dict({"action": "keep", "key_fields": {"reason": "still true"}})
    facts = trace_facts([_result(plan)])
    assert facts["n_candidates"] == 1
    assert facts["n_non_keep"] == 0
    assert facts["n_backed_promotions"] == 0
    assert facts["plan_problems"] == []


def test_a_backed_promotion_counts_toward_n_backed_promotions():
    plan = _plan_dict({"action": "promote_to_memory", "artifact_id": "abc123"})
    events = [_tool_call("draft_memory_file", "abc123", step_id=1), _result(plan)]
    facts = trace_facts(events)
    assert facts["n_non_keep"] == 1
    assert facts["n_backed_promotions"] == 1
    assert facts["n_candidate_problems"] == 0


def test_an_unbacked_fabricated_promotion_is_not_counted_as_backed_and_is_a_candidate_problem():
    """The fabrication case `session.assemble` itself already tests: an artifact_id no tool call
    ever produced."""
    plan = _plan_dict({"action": "promote_to_memory", "artifact_id": "ghost"})
    facts = trace_facts([_result(plan)])
    assert facts["n_backed_promotions"] == 0
    assert facts["n_candidate_problems"] == 1


def test_a_prune_with_a_target_path_is_counted_as_named():
    plan = _plan_dict({"action": "prune", "key_fields": {"target_path": "/memory/stale.md"}})
    facts = trace_facts([_result(plan)])
    assert facts["prune_targets_named"] == 1
    assert facts["n_candidate_problems"] == 0


def test_a_prune_without_a_target_path_is_not_counted_as_named():
    """`trace_facts`'s `prune_targets_named` is a structural presence check only — `session.assemble`
    itself doesn't flag a missing target_path as a candidate problem, so only the presence count
    moves, not `n_candidate_problems`."""
    plan = _plan_dict({"action": "prune", "key_fields": {"reason": "stale, no target named"}})
    facts = trace_facts([_result(plan)])
    assert facts["prune_targets_named"] == 0
    assert facts["n_candidate_problems"] == 0


def test_a_promote_to_skill_with_an_invalid_scope_is_counted_by_n_bad_skill_scope():
    plan = _plan_dict(
        {
            "action": "promote_to_skill",
            "artifact_id": "s1",
            "key_fields": {"scope": "not-a-real-scope"},
        }
    )
    events = [_tool_call("draft_skill_file", "s1", step_id=1), _result(plan)]
    facts = trace_facts(events)
    assert facts["n_bad_skill_scope"] == 1


def test_a_promote_to_skill_with_a_valid_scope_is_not_counted_as_bad():
    plan = _plan_dict(
        {"action": "promote_to_skill", "artifact_id": "s1", "key_fields": {"scope": "project"}}
    )
    events = [_tool_call("draft_skill_file", "s1", step_id=1), _result(plan)]
    facts = trace_facts(events)
    assert facts["n_bad_skill_scope"] == 0


def test_a_promote_to_skill_missing_scope_entirely_is_counted_as_bad():
    plan = _plan_dict({"action": "promote_to_skill", "artifact_id": "s1", "key_fields": {}})
    events = [_tool_call("draft_skill_file", "s1", step_id=1), _result(plan)]
    facts = trace_facts(events)
    assert facts["n_bad_skill_scope"] == 1


def test_a_circuit_broken_drafting_call_is_surfaced():
    plan = _plan_dict({"action": "promote_to_memory", "artifact_id": "abc123"})
    events = [
        _tool_call("draft_memory_file", "abc123", step_id=1, circuit_broken=True),
        _result(plan),
    ]
    facts = trace_facts(events)
    assert facts["any_circuit_broken"] is True


def test_no_circuit_broken_when_no_drafting_call_tripped_it():
    plan = _plan_dict({"action": "promote_to_memory", "artifact_id": "abc123"})
    events = [_tool_call("draft_memory_file", "abc123", step_id=1), _result(plan)]
    facts = trace_facts(events)
    assert facts["any_circuit_broken"] is False


# -- trace_facts: TA's min-step_id ordering facts ----------------------------------------------


def test_min_read_step_and_min_draft_step_are_the_minimum_step_id_per_kind():
    events = [
        _tool_call("read_transcript_chunk", step_id=5),
        _tool_call("list_memory_files", step_id=2),
        _tool_call("draft_memory_file", "abc123", step_id=7),
        _tool_call("draft_skill_file", "s1", step_id=4),
        _result(_plan_dict()),
    ]
    facts = trace_facts(events)
    assert facts["min_read_step"] == 2      # min(5, 2)
    assert facts["min_draft_step"] == 4     # min(7, 4)
    assert facts["min_read_step"] < facts["min_draft_step"]  # evidence really did precede drafting


def test_drafting_before_any_read_is_still_surfaced_as_a_raw_ordering_fact_not_a_bool():
    """A pure fact-surface (per `rlm_kit.rubric.criteria_facts`'s contract): `trace_facts` never
    collapses this into a met/unmet verdict — it hands back both raw step ids, even when drafting
    happened FIRST, so the comparison stays the reader's job."""
    events = [
        _tool_call("draft_memory_file", "abc123", step_id=0),
        _tool_call("read_transcript_chunk", step_id=3),
        _result(_plan_dict()),
    ]
    facts = trace_facts(events)
    assert facts["min_draft_step"] == 0
    assert facts["min_read_step"] == 3
    assert facts["min_draft_step"] < facts["min_read_step"]


def test_min_read_and_draft_step_are_none_when_no_such_calls_exist():
    facts = trace_facts([_result(_plan_dict())])
    assert facts["min_read_step"] is None
    assert facts["min_draft_step"] is None


# -- trace_facts: transcript COVERAGE (n_transcripts / n_transcripts_read) ----------------------


def _read(index, *, step_id=0):
    """A `read_transcript_chunk` call whose ARGS carry the index it read — the audit record."""
    event = _tool_call("read_transcript_chunk", step_id=step_id)
    event["payload"]["args"] = {"transcript_index": index, "offset": 0, "limit": 4000}
    return event


def _run_start(meta):
    return {"type": EVENT_RUN_START, "step_id": 0, "payload": {"meta": meta}}


def test_coverage_facts_count_the_input_and_the_DISTINCT_entries_actually_read():
    """The blind spot this closes: a run that read 2 of 414 transcripts and a run that read 2 of 2
    produced IDENTICAL facts. With subagent ingestion multiplying entries by up to 18.5x, "how much
    of the input did this plan actually look at" stopped being answerable from the rubric at all.

    Both are deterministic counts off the trace, and neither decides met/unmet — repeated reads of
    ONE entry are one entry of coverage, which is why the count is over the distinct set.
    """
    events = [
        _run_start({"transcripts": 43}),
        _read(0, step_id=1),
        _read(0, step_id=2),          # the same entry again — still one entry covered
        _read(7, step_id=3),
        _result(_plan_dict()),
    ]
    facts = trace_facts(events)
    assert facts["n_transcripts"] == 43
    assert facts["n_transcripts_read"] == 2


def test_coverage_facts_degrade_rather_than_raise_on_an_old_or_malformed_trace():
    """`trace_facts` is served over HTTP by the studio and run over a whole glob by the eval member,
    so a trace with no `run_start`, a non-dict `meta`, a non-int count, or junk in a tool's `args`
    must degrade — never raise, and never invent a number."""
    assert trace_facts([_result(_plan_dict())])["n_transcripts"] is None
    assert trace_facts([_run_start("nope"), _result(_plan_dict())])["n_transcripts"] is None
    assert trace_facts([_run_start({"transcripts": "two"})])["n_transcripts"] is None
    # `True` is an `int` in Python; a bool count is not a count.
    assert trace_facts([_run_start({"transcripts": True})])["n_transcripts"] is None

    junk = [_read("/etc/passwd", step_id=1), _read(None, step_id=2), _read(True, step_id=3)]
    assert trace_facts([*junk, _result(_plan_dict())])["n_transcripts_read"] == 0


def test_the_coverage_facts_are_deliberately_absent_from_every_criterion_lens():
    """They are facts about the run's INPUT, not evidence for any of the four criteria AS WORDED —
    TA asks whether evidence-gathering preceded drafting, not how much of the corpus was covered.
    Folding a new fact into an existing criterion would change what that criterion's `observed`
    claims without changing its description; wiring these up means writing a criterion for them.
    """
    lensed = {key for keys in _CATEGORY_LENS.values() for key in keys}
    assert "n_transcripts" not in lensed and "n_transcripts_read" not in lensed
    assert {"n_transcripts", "n_transcripts_read"} <= set(trace_facts([_result(_plan_dict())]))


# -- non-dict trace lines: every rubric entry point degrades instead of raising ----------------


def test_trace_facts_ignores_non_dict_trace_lines():
    """`trace_facts` raised via `session.assemble` (whose `_draft_calls` scans EVERY event) before
    its own `e.get("type")` comprehensions were ever reached. The facts must come out identical to
    the same trace with the junk lines removed — dropped, never allowed to shift a count."""
    plan = _plan_dict({"action": "promote_to_memory", "artifact_id": "abc123"})
    clean = [_tool_call("read_transcript_chunk", step_id=0), _tool_call("draft_memory_file", "abc123", step_id=1), _result(plan)]
    dirty = [42, clean[0], None, clean[1], "x", clean[2], [1, 2, 3]]
    assert trace_facts(dirty) == trace_facts(clean)
    assert trace_facts(dirty)["n_backed_promotions"] == 1


def test_rubric_from_meta_ignores_a_non_dict_trace_line():
    """rlm-kit's own `rubric_from_meta` tolerates a malformed CRITERION entry inside
    `meta["rubric"]`, but its top-level `for e in events` loop is an unguarded `.get` — a different
    tolerance from the one it documents, and the one that took `criteria_facts` down."""
    rubric = default_rubric()
    events = [42, {"type": EVENT_RUN_START, "payload": {"meta": {"rubric": rubric_to_meta(rubric)}}}, None]
    assert [c.name for c in rubric_from_meta(events).criteria] == [c.name for c in rubric.criteria]


def test_criteria_facts_ignores_non_dict_trace_lines():
    """The composite path: `criteria_facts` -> `rubric_from_meta` AND `trace_facts`, both of which
    raised on a non-dict line, so this is the end-to-end degrade for the whole rubric surface."""
    facts = criteria_facts([42, _result(_plan_dict({"action": "keep"})), None, "x", [1, 2, 3]])
    assert [f.criterion for f in facts] == [c.name for c in default_rubric().criteria]


# -- criteria_facts: the lens actually slices trace_facts per category ------------------------


def test_criteria_facts_slices_each_category_through_its_own_lens_keys():
    plan = _plan_dict({"action": "promote_to_memory", "artifact_id": "abc123"})
    events = [
        _tool_call("read_transcript_chunk", step_id=0),
        _tool_call("draft_memory_file", "abc123", step_id=1),
        _result(plan),
    ]
    facts = criteria_facts(events, default_rubric().criteria)
    assert len(facts) == 4
    by_category = {f.category: f for f in facts}
    assert set(by_category) == set(CRITERION_CATEGORIES)
    for category, fact in by_category.items():
        assert isinstance(fact, CriterionFact)
        assert set(fact.observed) <= set(_CATEGORY_LENS[category])
    assert by_category["TA"].observed["min_read_step"] == 0
    assert by_category["TA"].observed["min_draft_step"] == 1
    assert by_category["TG"].observed["n_backed_promotions"] == 1


def test_criteria_facts_falls_back_to_default_rubric_when_the_trace_carries_none():
    events = [_result(_plan_dict())]
    facts = criteria_facts(events)
    assert [f.criterion for f in facts] == [c.name for c in default_rubric().criteria]


def test_criteria_facts_uses_the_runs_own_recorded_rubric_when_present():
    custom = RubricCriteria(
        criteria=[Criterion(name="only_one", category="TF", description="a plan exists")]
    )
    events = [
        {"type": EVENT_RUN_START, "payload": {"meta": {"rubric": rubric_to_meta(custom)}}},
        _result(_plan_dict()),
    ]
    facts = criteria_facts(events)
    assert [f.criterion for f in facts] == ["only_one"]


def test_a_category_absent_from_the_lens_yields_empty_observed_never_a_keyerror():
    criteria = [Criterion(name="mystery", category="ZZ", description="not in this module's lens")]
    facts = criteria_facts([_result(_plan_dict())], criteria)
    assert facts[0].observed == {}
