"""`ctx_distillery.schema.assemble` — the supplementary-file (`references/`/`scripts/`) half.

Hand-rolled trace-event dicts, matching `test_rubric.py`'s own convention for building event lists
without a real `TraceRecorder` — a pure predicate over events needs no file at all.
"""

from __future__ import annotations

from rlm_kit.trace import EVENT_RESULT, EVENT_TOOL_CALL

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
