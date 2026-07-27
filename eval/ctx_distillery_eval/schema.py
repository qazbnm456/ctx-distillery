"""The eval member's own wire shapes — `EvalScore` / `EvalRow` / `EvalReport`.

Deliberately separate pydantic models from `ctx_distillery`'s (`DistillPlan` / `AssembledPlan`):
this package is a READER of that package's public surface, never a fork of its schema. Same
TF/TA/TG/PA codes as `ctx_distillery.rubric.CRITERION_CATEGORIES`, but re-declared here rather than
imported — the eval member's categories are ARTIFACT-framed judge scores (0-10 floats), a different
kind of thing from the rollout side's deterministic, unscored `CriterionFact.observed` facts, and the
two must never be confused as the same object.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: Same four codes as `ctx_distillery.rubric.CRITERION_CATEGORIES` — kept as a plain tuple (not an
#: import) so this package never depends on the rollout side's rubric module, only on the trace
#: shape it reads (`session.assemble`'s output) via `ctx_distillery.session`/`ctx_distillery.task`.
EVAL_CATEGORIES = ("TF", "TA", "TG", "PA")


class EvalScore(BaseModel):
    """One judge's 0-10 scores, artifact-framed per `docs/DESIGN.md`'s eval-member table.

    Reward-free: this is a LABEL for a human/downstream trainer to read, never a value this package
    computes a composite from or feeds back into a training loop.
    """

    TF: float = Field(..., ge=0, le=10, description="does the plan capture what's worth keeping?")
    TA: float = Field(..., ge=0, le=10, description="did the plan's judgements follow sensible evidence?")
    TG: float = Field(..., ge=0, le=10, description="is each candidate plausibly grounded in the transcript?")
    PA: float = Field(..., ge=0, le=10, description="are the drafted files clear, well-scoped, correctly targeted?")
    notes: str = Field(default="", description="the judge's free-text rationale, if any")


class EvalRow(BaseModel):
    """One scored run."""

    run_id: str
    trace_path: str
    score: EvalScore


class EvalReport(BaseModel):
    """A batch of scored runs, plus the per-category MEANS only — never a composite.

    `means` is empty for an empty `rows` list (nothing to average) rather than a dict of zeros, which
    would misleadingly claim a real (low) score for a taskset that produced no rows at all.
    """

    rows: list[EvalRow] = Field(default_factory=list)
    means: dict[str, float] = Field(default_factory=dict)


def compute_means(rows: list[EvalRow]) -> dict[str, float]:
    """The per-category arithmetic mean across `rows` — the ONLY aggregate this package computes.

    No composite/weighted score: `docs/DESIGN.md`'s eval-member boundary is explicit ("reward-free
    (per-category means only, no composite)"), so combining TF/TA/TG/PA into one number is left to
    whatever downstream trainer eventually scores these labels, never done here.
    """
    if not rows:
        return {}
    return {
        category: sum(getattr(row.score, category) for row in rows) / len(rows)
        for category in EVAL_CATEGORIES
    }
