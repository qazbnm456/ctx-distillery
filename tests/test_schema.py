"""`ctx_distillery.schema.assemble` — the supplementary-file (`references/`/`scripts/`) half.

Hand-rolled trace-event dicts, matching `test_rubric.py`'s own convention for building event lists
without a real `TraceRecorder` — a pure predicate over events needs no file at all.
"""

from __future__ import annotations

import pytest
from rlm_harness.trace import EVENT_RESULT, EVENT_TOOL_CALL

from ctx_distillery.schema import AssembledExtraFile, DistillCandidate, DistillPlan, assemble

_SKILL_DRAFT = (
    "---\n"
    "name: merge-freeze-checklist\n"
    "description: How to run a merge freeze.\n"
    "---\n"
    "Announce the freeze, then confirm no PRs target main until it lifts.\n"
)


def _skill_call(artifact_id, *, step_id=0, draft=_SKILL_DRAFT, ok=True, errors=()):
    return {
        "type": EVENT_TOOL_CALL,
        "step_id": step_id,
        "payload": {
            "tool": "draft_skill_file",
            "ok": ok,
            "artifact_id": artifact_id,
            "draft": draft,
            "errors": list(errors),
        },
    }


def _memory_call(artifact_id, *, step_id=0, draft="stuff", ok=True, errors=()):
    return {
        "type": EVENT_TOOL_CALL,
        "step_id": step_id,
        "payload": {
            "tool": "draft_memory_file",
            "ok": ok,
            "artifact_id": artifact_id,
            "draft": draft,
            "errors": list(errors),
        },
    }


def _extra_call(artifact_id, relative_path, *, step_id=0, draft="content", ok=True, errors=()):
    return {
        "type": EVENT_TOOL_CALL,
        "step_id": step_id,
        "payload": {
            "tool": "draft_skill_extra_file",
            "ok": ok,
            "artifact_id": artifact_id,
            "relative_path": relative_path,
            "draft": draft,
            "errors": list(errors),
        },
    }


def _result(plan: dict):
    return {"type": EVENT_RESULT, "payload": {"output": plan}}


def _run_start(meta):
    return {"type": "run_start", "step_id": 0, "payload": {"meta": meta}}


def _plan_dict(*candidates: dict) -> dict:
    return DistillPlan(candidates=[DistillCandidate(**c) for c in candidates]).model_dump()


def _skill_candidate(artifact_id):
    return _plan_dict(
        {
            "action": "promote_to_skill",
            "artifact_id": artifact_id,
            "key_fields": {"scope": "project"},
        }
    )


# -- extra_files: attached only to promote_to_skill, keyed by relative_path --------------------


def test_a_good_extra_file_lands_in_extra_files_and_adds_no_problem():
    events = [
        _skill_call("a1"),
        _extra_call("a1", "references/checklist.md"),
        _result(_skill_candidate("a1")),
    ]
    plan = assemble(events, DistillPlan.model_validate(_skill_candidate("a1")))
    candidate = plan.candidates[0]
    assert set(candidate.extra_files) == {"references/checklist.md"}
    extra = candidate.extra_files["references/checklist.md"]
    assert isinstance(extra, AssembledExtraFile)
    assert extra.draft == "content" and extra.draft_ok is True
    assert candidate.problems == []


def test_multiple_extra_files_under_one_artifact_id_all_attach():
    events = [
        _skill_call("a1"),
        _extra_call("a1", "references/one.md", draft="one"),
        _extra_call("a1", "scripts/setup.sh", draft="#!/bin/sh\necho hi\n"),
    ]
    plan = assemble(events, DistillPlan.model_validate(_skill_candidate("a1")))
    candidate = plan.candidates[0]
    assert set(candidate.extra_files) == {"references/one.md", "scripts/setup.sh"}
    assert candidate.extra_files["references/one.md"].draft == "one"
    assert candidate.extra_files["scripts/setup.sh"].draft == "#!/bin/sh\necho hi\n"


def test_a_retry_of_one_relative_path_replaces_only_that_file():
    events = [
        _skill_call("a1"),
        _extra_call("a1", "references/one.md", draft="first attempt", step_id=1),
        _extra_call("a1", "scripts/setup.sh", draft="script", step_id=2),
        _extra_call("a1", "references/one.md", draft="second attempt", step_id=3),
    ]
    plan = assemble(events, DistillPlan.model_validate(_skill_candidate("a1")))
    candidate = plan.candidates[0]
    assert candidate.extra_files["references/one.md"].draft == "second attempt"
    assert candidate.extra_files["scripts/setup.sh"].draft == "script"  # untouched by the retry


def test_a_bad_extra_file_is_kept_for_visibility_but_blocks_via_problems():
    """Mirrors the main draft's own parity property: kept even when `ok=False`, so a reviewer can
    see what was attempted — but ALSO reported on `problems`, which is what makes
    `apply._blocking_problem`'s existing "any problems -> refuse" check block the whole candidate."""
    events = [
        _skill_call("a1"),
        _extra_call("a1", "references/bad.md", draft="", ok=False, errors=["empty draft"]),
    ]
    plan = assemble(events, DistillPlan.model_validate(_skill_candidate("a1")))
    candidate = plan.candidates[0]
    assert candidate.extra_files["references/bad.md"].draft == ""
    assert candidate.extra_files["references/bad.md"].draft_ok is False
    assert any("references/bad.md" in p for p in candidate.problems)


def test_extra_files_are_never_attached_to_a_promote_to_memory_candidate():
    """Extras only mean something for a skill — a memory candidate has no supplementary-file
    concept, so a stray draft_skill_extra_file call is inert for it even if ids happened to match
    (they never do in practice: each drafting call mints its own uuid)."""
    events = [
        _memory_call("m1"),
        _extra_call("m1", "references/one.md"),  # never reachable in practice, but must be inert
    ]
    plan_dict = _plan_dict({"action": "promote_to_memory", "artifact_id": "m1", "key_fields": {}})
    plan = assemble(events, DistillPlan.model_validate(plan_dict))
    assert plan.candidates[0].extra_files == {}


def test_an_extra_file_for_an_artifact_id_no_candidate_references_is_simply_unused():
    events = [
        _skill_call("a1"),
        _extra_call("a1", "references/one.md"),
        _extra_call("orphan-id", "references/never-used.md"),
    ]
    plan = assemble(events, DistillPlan.model_validate(_skill_candidate("a1")))
    assert set(plan.candidates[0].extra_files) == {"references/one.md"}


# -- AssembledPlan.harness: read from run_start.meta, never from the plan's own claim -----------


def test_assemble_populates_harness_from_run_start_meta():
    events = [_run_start({"harness": "codex"}), _result(_plan_dict())]
    assembled = assemble(events, DistillPlan.model_validate(_plan_dict()))
    assert assembled.harness == "codex"


def test_assemble_harness_is_none_without_a_run_start_event():
    events = [_result(_plan_dict())]
    assembled = assemble(events, DistillPlan.model_validate(_plan_dict()))
    assert assembled.harness is None


def test_assemble_harness_is_none_when_run_start_carries_no_harness_key():
    events = [_run_start({"transcripts": 3}), _result(_plan_dict())]
    assembled = assemble(events, DistillPlan.model_validate(_plan_dict()))
    assert assembled.harness is None


# --------------------------------------------------------------------------------------------------
# A `prune` must name its target, and `assemble` is where a reviewer finds out.
#
# `apply.py` has always refused a prune with no `target_path`
# (`tests/test_apply.py::test_a_prune_with_no_target_path_is_refused`). Until this check existed,
# that refusal was the FIRST anyone heard of it: `assemble` reported `problems=[]`, so
# `ctx-distillery show` rendered an unapplicable candidate as though it were fine and the operator
# learned otherwise only after approving it. Found by a live run whose planner keyed its prune on
# `existing_memory_path` instead.
# --------------------------------------------------------------------------------------------------


def test_a_prune_without_target_path_is_flagged_where_it_is_reviewed() -> None:
    plan = DistillPlan(candidates=[DistillCandidate(action="prune", key_fields={"reason": "stale"})])
    (candidate,) = assemble([], plan).candidates
    assert candidate.problems, "an unapplicable prune must not render as a clean candidate"
    assert "target_path" in candidate.problems[0]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_present_but_unusable_target_path_is_flagged_too(value) -> None:
    """`apply` refuses these as well, so the review surface must not call them fine. A bare key with
    an empty value is exactly what a model produces when it knows the field is expected but has
    nothing to put in it."""
    plan = DistillPlan(
        candidates=[DistillCandidate(action="prune", key_fields={"target_path": value})]
    )
    (candidate,) = assemble([], plan).candidates
    assert candidate.problems and "target_path" in candidate.problems[0]


def test_a_prune_that_names_its_target_is_left_alone() -> None:
    """The check must not fire on the normal case — that is what makes the flag mean something."""
    plan = DistillPlan(
        candidates=[DistillCandidate(action="prune", key_fields={"target_path": "/m/a.md"})]
    )
    assert assemble([], plan).candidates[0].problems == []


def test_keep_needs_no_target_path() -> None:
    """`keep` writes nothing, so it has no target to name. Flagging it would make every well-formed
    plan carry a problem."""
    plan = DistillPlan(candidates=[DistillCandidate(action="keep", key_fields={"reason": "still true"})])
    assert assemble([], plan).candidates[0].problems == []
