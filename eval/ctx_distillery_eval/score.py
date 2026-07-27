"""`score_run` — turn one distillation run's trace + its transcript(s) into an `EvalRow`.

Reconstructs the plan the SAME way `ctx_distillery.rubric.trace_facts` does (the run's LAST
`EVENT_RESULT` payload, re-validated as a `DistillPlan`), then re-sources it through
`ctx_distillery.session.assemble` — both PUBLIC entry points of `ctx_distillery`'s surface,
never a private helper reached across the package boundary (this package is a one-way READER of
`ctx_distillery`, per `docs/DESIGN.md`'s eval-member boundary; `tests/test_boundary.py` in the root
package pins that `ctx_distillery` never imports this package back).
"""

from __future__ import annotations

from pydantic import ValidationError
from rlm_kit.trace import EVENT_RESULT

from ctx_distillery.session import AssembledPlan, assemble
from ctx_distillery.task import DistillPlan

from .judge import Judge, StubJudge
from .schema import EvalReport, EvalRow, compute_means


def _plan_from_events(events: list[dict]) -> DistillPlan | None:
    """The run's LAST `result` event's output, re-validated as a `DistillPlan` — or `None`.

    Same reconstruction `ctx_distillery.rubric._plan_from_events` performs, kept as its own small
    local copy here (rather than importing that underscore-prefixed helper across the package
    boundary) so this package only ever touches `ctx_distillery`'s PUBLIC surface.

    FIXED per adversarial review: a well-formed-but-wrong-shaped `output` dict (e.g. missing a
    required field) used to raise an uncaught `pydantic.ValidationError`, reproduced end-to-end
    scoring a glob where ONE malformed trace took the entire batch down. Returns `None` on that
    shape too — `assemble(events, None)` already reports a missing plan as a run-level problem
    rather than raising, and this must degrade the same way, matching `.rubric`'s own fix.
    """
    for event in reversed(events):
        if event.get("type") == EVENT_RESULT:
            output = (event.get("payload") or {}).get("output")
            if isinstance(output, dict):
                try:
                    return DistillPlan.model_validate(output)
                except ValidationError:
                    return None
    return None


def render_plan(plan: AssembledPlan) -> str:
    """A human/judge-legible rendering of an assembled plan — one line per candidate.

    Deliberately plain text, not JSON: the judge prompt reads more naturally this way, and nothing
    downstream parses this rendering back (the structured facts live in `ctx_distillery.rubric`, a
    completely separate, deterministic path this package never touches).
    """
    if not plan.candidates:
        return "(this run's plan proposed no candidates)"
    lines = []
    for i, candidate in enumerate(plan.candidates):
        lines.append(f"[{i}] action={candidate.action} artifact_id={candidate.artifact_id!r}")
        if candidate.key_fields:
            lines.append(f"    key_fields={candidate.key_fields!r}")
        if candidate.draft:
            lines.append(f"    draft (ok={candidate.draft_ok}):\n{candidate.draft}")
        if candidate.problems:
            lines.append(f"    problems: {candidate.problems!r}")
    if plan.problems:
        lines.append(f"(run-level problems: {plan.problems!r})")
    return "\n".join(lines)


def score_run(
    run_id: str,
    trace_path: str,
    events: list[dict],
    transcript_texts: list[str],
    *,
    judge: Judge | None = None,
) -> EvalRow:
    """Score one run: reconstruct its plan, render it, judge it against its transcript(s).

    `transcript_texts` is MANDATORY, never optional — see `judge.py`'s module docstring for why a
    trace-only fallback is not viable. `judge` defaults to `StubJudge()`, the offline, fully
    deterministic, tested default path; a real (non-stub) judge is opt-in behind the `judge` extra.
    """
    if judge is None:
        judge = StubJudge()
    plan = _plan_from_events(events)
    assembled = assemble(events, plan)
    plan_text = render_plan(assembled)
    score = judge(plan_text, transcript_texts)
    return EvalRow(run_id=run_id, trace_path=trace_path, score=score)


def aggregate(rows: list[EvalRow]) -> EvalReport:
    """Reward-free aggregation: per-category MEANS only (`schema.compute_means`), never a composite."""
    return EvalReport(rows=list(rows), means=compute_means(rows))


__all__ = ["aggregate", "render_plan", "score_run"]
