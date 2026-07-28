"""`score_run` — turn one distillation run's trace + its transcript(s) into an `EvalRow`.

Reconstructs the plan the SAME way `ctx_distillery.rubric.trace_facts` does (the run's LAST
`EVENT_RESULT` payload, re-validated as a `DistillPlan`), then re-sources it through
`ctx_distillery.session.assemble` — both PUBLIC entry points of `ctx_distillery`'s surface,
never a private helper reached across the package boundary (this package is a one-way READER of
`ctx_distillery`, per `CLAUDE.md`'s known-simplification bullet on the eval member;
`tests/test_boundary.py` in the root package pins that `ctx_distillery` never imports this package
back).

Studio pass, step 0: `plan_from_events` used to be a private, per-package-duplicated helper
(`ctx_distillery.rubric._plan_from_events`, and a local copy here). It is now PUBLIC on
`ctx_distillery.rubric` — already this package's own established boundary (public, top-level,
already imported-from for `rubric_to_meta` elsewhere in this initiative) — so this module imports
and calls it instead of keeping a second copy of the same reconstruction + `ValidationError`-degrade
logic. See `CLAUDE.md` invariant 11 for the full boundary-ambiguity resolution.

CLI pass: `render_plan` itself made the same journey, for the same reason. It was defined HERE (it
was written to feed the judge prompt), then `ctx-distillery show` needed the identical rendering — a
reviewer should read exactly what the judge reads — so it moved to `ctx_distillery.render` and this
module imports it rather than keeping the second copy that would have drifted. It stays in this
module's `__all__`, so `from ctx_distillery_eval.score import render_plan` keeps working.
"""

from __future__ import annotations

from ctx_distillery.render import render_plan
from ctx_distillery.rubric import plan_from_events
from ctx_distillery.session import assemble

from .judge import Judge, StubJudge
from .schema import EvalReport, EvalRow, compute_means


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
    plan = plan_from_events(events)
    assembled = assemble(events, plan)
    plan_text = render_plan(assembled)
    score = judge(plan_text, transcript_texts)
    return EvalRow(run_id=run_id, trace_path=trace_path, score=score)


def aggregate(rows: list[EvalRow]) -> EvalReport:
    """Reward-free aggregation: per-category MEANS only (`schema.compute_means`), never a composite."""
    return EvalReport(rows=list(rows), means=compute_means(rows))


__all__ = ["aggregate", "render_plan", "score_run"]
