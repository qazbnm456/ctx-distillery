"""`score_run` — turn one distillation run's trace + its transcript(s) into an `EvalRow`.

Reconstructs the plan the SAME way `ctx_distillery.rubric.trace_facts` does (the run's LAST
`EVENT_RESULT` payload, re-validated as a `DistillPlan`), then re-sources it through
`ctx_distillery.schema.assemble` — both PUBLIC entry points of `ctx_distillery`'s surface,
never a private helper reached across the package boundary (this package is a one-way READER of
`ctx_distillery`, per `CLAUDE.md`'s known-simplification bullet on the eval member;
`tests/test_boundary.py` in the root package pins that `ctx_distillery` never imports this package
back).

Parity pass 1: that import used to read `from ctx_distillery.session import assemble`. Same
function, and the old spelling still resolves (`session.py` re-exports it) — but `session.py`
imports `task.py`, which imports dspy, so a fully-offline `ctx-distillery-eval score --stub` run was
importing an LM framework it never calls. `assemble` is DEFINED in the dspy-free
`ctx_distillery.schema` now, and this module imports it from there. `eval/tests/test_boundary.py`
pins the result in a fresh subprocess, so a future import cannot quietly put dspy back.

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
from ctx_distillery.schema import assemble

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
    deterministic, tested default path; the LIVE judge (`judge.make_eval_judge`, behind the `judge`
    extra + `CDEVAL_MODEL`) is selected by `cli._pick_judge` and passed in explicitly.

    Never raises on a failed judge. Parity pass 4 widened the `Judge` protocol to return a
    `JudgeVerdict` rather than a bare `EvalScore`, and this is where that pays off: `ok=False`
    becomes an UNSCORED row carrying the verdict's reason, excluded from the means by
    `schema.compute_means` — never a fake 0, and never an exception that takes the rest of the batch
    down with it. The `or "judge returned no score"` fallback is belt-and-braces for a third-party
    `Judge` implementation that returns `ok=False` with an empty reason: `EvalRow` REFUSES a blank
    unscored row, so without it a badly-behaved judge would raise a `ValidationError` here instead of
    degrading. `verdict.score is None` is checked alongside `ok` for the same reason — an `ok=True`
    verdict with no score is not a score.
    """
    if judge is None:
        judge = StubJudge()
    plan = plan_from_events(events)
    assembled = assemble(events, plan)
    plan_text = render_plan(assembled)
    verdict = judge(plan_text, transcript_texts)
    if not verdict.ok or verdict.score is None:
        return EvalRow(
            run_id=run_id,
            trace_path=trace_path,
            unscored_reason=verdict.reason or "judge returned no score",
        )
    return EvalRow(run_id=run_id, trace_path=trace_path, score=verdict.score)


def aggregate(
    rows: list[EvalRow], *, judge_model: str = "", prompt_version: str = ""
) -> EvalReport:
    """Reward-free aggregation: per-category MEANS only (`schema.compute_means`), never a composite.

    `judge_model` / `prompt_version` are PROVENANCE, not inputs to any arithmetic — they travel from
    `cli._pick_judge` so a scorecard states which judge produced it and under which prompt version.
    Both are keyword-only with empty defaults, so `aggregate(rows)` keeps working for a caller that
    has no judge identity to report (the stub path deliberately reports `prompt_version=""`, since a
    stub never rendered the prompt and pinning a version to it would claim provenance it lacks).

    `n_unscored` is derived HERE from the rows themselves rather than counted by the caller, so it
    can never disagree with what `compute_means` actually excluded.
    """
    scored = [row for row in rows if row.score is not None]
    return EvalReport(
        n=len(rows),
        n_unscored=len(rows) - len(scored),
        judge_model=judge_model,
        prompt_version=prompt_version,
        means=compute_means(rows),
        rows=list(rows),
    )


__all__ = ["aggregate", "render_plan", "score_run"]
