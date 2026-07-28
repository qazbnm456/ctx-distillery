"""ATLAS rubric facts for a `DistillSession` run — reward-free, deterministic, trace-sourced.

Follows `CLAUDE.md`'s known-simplification bullet on this module and the same convention
`rlm_kit.rubric`'s docstring describes: rlm-kit owns the generic types + structural lint +
per-criterion fact-assembly loop; THIS module supplies the taxonomy (`TF`/`TA`/`TG`/`PA`), the fixed
criterion skeleton (`default_rubric`), the trace -> facts function (`trace_facts`), and the
category -> fact-keys lens (`_CATEGORY_LENS`). `category` stays opaque to rlm-kit; the meaning below
is entirely ours.

`DistillSession` decomposes the same way every run (ingest -> propose a plan over
transcripts+memory); only the transcripts/memory differ. That constant task shape is why
`default_rubric()` can return the same four criteria every time rather than being assembled per run.

Sources facts from `session.assemble()`'s output (`AssembledPlan`/`AssembledCandidate`) rather than
re-deriving them from raw events, so a criterion's `observed` dict can never drift from what
`assemble` already established as ground truth for THIS trace.
"""

from __future__ import annotations

from rlm_kit.rubric import (
    Criterion,
    CriterionFact,
    RubricCriteria,
    rubric_to_meta,  # noqa: F401 - re-exported for `session.py`'s import
)
from rlm_kit.rubric import criteria_facts as _kit_criteria_facts
from rlm_kit.rubric import rubric_from_meta as _kit_rubric_from_meta
from rlm_kit.rubric import validate_rubric as _kit_validate_rubric
from rlm_kit.trace import EVENT_RESULT, EVENT_TOOL_CALL

from .trace_io import dict_events

CRITERION_CATEGORIES = ("TF", "TA", "TG", "PA")

CATEGORY_MEANING = {
    "TF": "Task Fulfillment — the run produced a non-empty plan carrying real judgement.",
    "TA": "Tool Appropriateness — evidence-gathering preceded drafting; drafting didn't thrash.",
    "TG": "Tool Grounding — each promotion candidate is backed by a real drafting call.",
    "PA": "Parameter Accuracy — the plan's structural fields are well-formed.",
}


def default_rubric(task: str = "") -> RubricCriteria:
    """The fixed, four-criterion ATLAS rubric every `DistillSession` run carries in its meta.

    `task` is accepted (and currently ignored) to keep the same call shape as a sibling rubric whose
    task-shape genuinely varies; `DistillSession`'s does not (see the module docstring), so there is
    nothing to branch on yet.
    """
    return RubricCriteria(
        criteria=[
            Criterion(
                name="plan_carries_real_judgement",
                category="TF",
                weight=1.0,
                description=(
                    "The run produced a plan with at least one non-keep candidate (prune, "
                    "promote_to_memory, or promote_to_skill) and no run-level plan problems — "
                    "evidence the planner exercised real judgement rather than defaulting to keep."
                ),
            ),
            Criterion(
                name="evidence_gathered_before_drafting",
                category="TA",
                weight=1.0,
                description=(
                    "Evidence-gathering tool calls (list_memory_files / read_memory_file / "
                    "read_transcript_chunk) preceded drafting tool calls (draft_memory_file / "
                    "draft_skill_file), and no drafting call tripped the circuit breaker — the "
                    "planner read before it wrote, and drafting didn't thrash."
                ),
            ),
            Criterion(
                name="candidates_backed_by_real_drafts",
                category="TG",
                weight=1.0,
                description=(
                    "Every promote_to_memory / promote_to_skill candidate is backed by a real, "
                    "format-valid drafting tool call (not a fabricated artifact_id), and every "
                    "prune candidate at least names a target_path."
                ),
            ),
            Criterion(
                name="plan_structurally_well_formed",
                category="PA",
                weight=1.0,
                description=(
                    "Candidates carry no structural problems, and every promote_to_skill "
                    "candidate's key_fields['scope'] is a valid value ('project' or 'global')."
                ),
            ),
        ]
    )


def rubric_from_meta(events: list[dict]) -> RubricCriteria:
    """Recover the rubric this run's `run_start` meta actually carried (empty if none recorded).

    `dict_events` first (see `trace_io.py`): rlm-kit's own `rubric_from_meta` IS tolerant of a
    malformed CRITERION entry inside `meta["rubric"]`, but its top-level `for e in events: if
    e.get("type")` loop is not — a non-dict trace line raised a raw `AttributeError` there, which
    took `criteria_facts` down with it. Two different tolerances; only one of them was rlm-kit's.
    """
    return _kit_rubric_from_meta(dict_events(events), categories=CRITERION_CATEGORIES)


def plan_from_events(events: list[dict]):
    """Reconstruct the `DistillPlan` from the run's LAST `result` event.

    `trace_facts`'s single-arg signature (matching `diff_sentry.rubric.trace_facts`) has no `plan=`
    parameter, unlike `session.assemble(events, plan)` — so this rebuilds the plan `assemble` needs
    from the trace itself, via `rlm_kit.trace.record_result`'s recorded `EVENT_RESULT` payload
    (`payload["output"]`). Returns None if no result event carries a dict output (no run, or a run
    that failed before SUBMIT), OR if that dict does not actually validate as a `DistillPlan` —
    `assemble(events, None)` already handles a missing plan as a run-level problem, and `assemble`'s
    OWN stated philosophy is "none of them raise" (`session.py`'s module docstring), so a malformed
    shape here must degrade the SAME way, not propagate a raw `pydantic.ValidationError` and crash
    the whole batch. FIXED per adversarial review: an earlier draft only guarded "no result event" /
    "output isn't a dict" and let a well-formed-but-wrong-shaped dict (e.g. missing a required field)
    raise uncaught — reproduced end-to-end via the eval CLI, where ONE malformed trace in a glob took
    the entire scoring run down with it.

    ALSO FIXED, a second and distinct failure mode: a non-dict trace LINE (`42`, `null`, `"x"`,
    `[1,2,3]` — `rlm_kit.trace.load_events` does no shape validation) raised a raw `AttributeError`
    here, ORDER-DEPENDENTLY, which is why it hid for so long: `reversed()` returns at the first
    `result` event, so a bad line BEFORE the last result was never visited and the bug looked
    absent, while a bad line AFTER it — trailing garbage, a truncated tail, a concatenated file —
    or a trace with no result event at all, crashed. `dict_events` (`trace_io.py`) drops them once,
    at the top; the `ValidationError` catch below is a different shape problem entirely.
    """
    from pydantic import ValidationError

    from .task import DistillPlan  # local import: keep rubric.py's top light, mirror trace_facts style

    for event in reversed(dict_events(events)):
        if event.get("type") == EVENT_RESULT:
            output = (event.get("payload") or {}).get("output")
            if isinstance(output, dict):
                try:
                    return DistillPlan.model_validate(output)
                except ValidationError:
                    return None
    return None


def trace_facts(events: list[dict]) -> dict:
    """The deterministic per-run facts every ATLAS criterion's `observed` slices from.

    Sources candidate-level facts from `session.assemble()`'s output (never re-derived from raw
    events, so they can't drift from what `assemble` already established); sources ordering/breaker
    facts directly from the trace's own `tool_call` events, since `assemble` doesn't surface those.

    Filters non-dict trace lines ONCE up front (`trace_io.dict_events`) so the `e.get("type")`
    comprehensions below are safe; `assemble` re-filters, which is idempotent and O(n), because it
    is public in its own right and cannot assume its caller went through here.
    """
    from .session import assemble

    events = dict_events(events)
    plan = plan_from_events(events)
    a = assemble(events, plan)
    read_steps = [
        e.get("step_id")
        for e in events
        if e.get("type") == EVENT_TOOL_CALL
        and (e.get("payload") or {}).get("tool")
        in ("list_memory_files", "read_memory_file", "read_transcript_chunk")
    ]
    draft_steps = [
        e.get("step_id")
        for e in events
        if e.get("type") == EVENT_TOOL_CALL
        and (e.get("payload") or {}).get("tool") in ("draft_memory_file", "draft_skill_file")
    ]
    draft_calls = [
        e.get("payload")
        for e in events
        if e.get("type") == EVENT_TOOL_CALL
        and (e.get("payload") or {}).get("tool") in ("draft_memory_file", "draft_skill_file")
    ]
    return {
        "n_candidates": len(a.candidates),
        "n_non_keep": sum(1 for c in a.candidates if c.action != "keep"),
        "plan_problems": list(a.problems),
        "min_read_step": min(read_steps) if read_steps else None,
        "min_draft_step": min(draft_steps) if draft_steps else None,
        "any_circuit_broken": any(bool((p or {}).get("circuit_broken")) for p in draft_calls),
        "n_candidate_problems": sum(1 for c in a.candidates if c.problems),
        "n_backed_promotions": sum(
            1
            for c in a.candidates
            if c.action in ("promote_to_memory", "promote_to_skill") and c.draft_ok
        ),
        "prune_targets_named": sum(
            1
            for c in a.candidates
            if c.action == "prune" and str((c.key_fields or {}).get("target_path") or "").strip()
        ),
        # Added per implementation-plan audit: the design's own table requires PA to also check
        # "for promote_to_skill, whether key_fields['scope'] was valid" — session.assemble() never
        # inspects key_fields at all, so this needs its OWN dedicated fact, not implicit coverage
        # from n_candidate_problems.
        "n_bad_skill_scope": sum(
            1
            for c in a.candidates
            if c.action == "promote_to_skill"
            and (c.key_fields or {}).get("scope") not in ("project", "global")
        ),
    }


# Design decision (flagged during an implementation-plan audit, resolved here): TA's fact is the raw
# min_read_step / min_draft_step step-id PAIR, not a pre-computed `evidence_before_drafting: bool`.
# This keeps `trace_facts` a pure fact-surface per `rlm_kit.rubric.criteria_facts`'s own contract
# ("never decides met/unmet") — the ordering COMPARISON is left to whatever reads `observed` later
# (the eval judge, a future trainer). `any_circuit_broken` stays a bool because it IS already a raw,
# undebatable observation (the breaker either tripped or it didn't) rather than a derived comparison.
_CATEGORY_LENS = {
    "TF": ("n_candidates", "n_non_keep", "plan_problems"),
    "TA": ("min_read_step", "min_draft_step", "any_circuit_broken"),
    "TG": ("n_backed_promotions", "prune_targets_named"),
    "PA": ("n_candidate_problems", "n_bad_skill_scope"),
}

_OBSERVABLE_VOCAB = (
    "plan",
    "candidate",
    "prune",
    "promote",
    "keep",
    "draft",
    "memory",
    "skill",
    "transcript",
    "evidence",
    "read",
    "backed",
    "problem",
)


def validate_rubric(rubric: RubricCriteria) -> list[str]:
    """A structural lint of `rubric` — see `rlm_kit.rubric.validate_rubric` for what this checks."""
    return _kit_validate_rubric(rubric, categories=CRITERION_CATEGORIES, observable_vocab=_OBSERVABLE_VOCAB)


def criteria_facts(events: list[dict], criteria: list[Criterion] | None = None) -> list[CriterionFact]:
    """Per-criterion facts for one run's trace — `criteria` from the run's own meta if recorded,
    else this module's `default_rubric()`."""
    if criteria is None:
        criteria = rubric_from_meta(events).criteria or default_rubric().criteria
    return _kit_criteria_facts(criteria, trace_facts(events), _CATEGORY_LENS)
