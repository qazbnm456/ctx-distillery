"""The eval member's own wire shapes — `EvalScore` / `EvalRow` / `EvalReport`.

Deliberately separate pydantic models from `ctx_distillery`'s (`DistillPlan` / `AssembledPlan`):
this package is a READER of that package's public surface, never a fork of its schema. Same
TF/TA/TG/PA codes as `ctx_distillery.rubric.CRITERION_CATEGORIES`, but re-declared here rather than
imported — the eval member's categories are ARTIFACT-framed judge scores (0-10 floats), a different
kind of thing from the rollout side's deterministic, unscored `CriterionFact.observed` facts, and the
two must never be confused as the same object.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

#: Same four codes as `ctx_distillery.rubric.CRITERION_CATEGORIES` — kept as a plain tuple (not an
#: import) so this package never depends on the rollout side's rubric module, only on the trace
#: shape it reads (`session.assemble`'s output) via `ctx_distillery.session`/`ctx_distillery.task`.
EVAL_CATEGORIES = ("TF", "TA", "TG", "PA")


class EvalScore(BaseModel):
    """One judge's 0-10 scores, artifact-framed per `judge.JUDGE_QUESTIONS`.

    Reward-free: this is a LABEL for a human/downstream trainer to read, never a value this package
    computes a composite from or feeds back into a training loop.
    """

    TF: float = Field(..., ge=0, le=10, description="does the plan capture what's worth keeping?")
    TA: float = Field(..., ge=0, le=10, description="did the plan's judgements follow sensible evidence?")
    TG: float = Field(..., ge=0, le=10, description="is each candidate plausibly grounded in the transcript?")
    PA: float = Field(..., ge=0, le=10, description="are the drafted files clear, well-scoped, correctly targeted?")
    notes: str = Field(default="", description="the judge's free-text rationale, if any")


class EvalRow(BaseModel):
    """One run's result: EITHER a judge's scores, OR an explicit `unscored_reason`. Never both empty.

    **`score` became OPTIONAL in parity pass 4, and that was a precondition for the live judge, not a
    convenience.** It used to be required, so a judge that failed (endpoint down, output off-schema,
    circuit breaker tripped) had literally nowhere to land: the only representable outcomes were "a
    real number" and "an exception that kills the batch". "Unscored, NEVER a fake 0" is the single
    most-repeated property across all three sibling eval members — a 0 is a claim the judge did not
    make, and it drags a mean down in a way that reads as a BAD PLAN rather than as broken infra.

    **Divergence from the siblings, argued**: toolscout/cve-reverser/diff-sentry each carry BOTH an
    `unscored: bool` field AND an optional `score`, i.e. two stored representations of one fact that
    can disagree (`unscored=False` next to `score=None` is constructible there). Here `score is None`
    IS unscored, exposed as a derived `@property` so the renderer and `compute_means` stay readable
    while there remains exactly one source of truth.

    The validator below enforces the other half: an unscored row must SAY WHY. A row with no score
    and no reason is the silent-blank failure mode the whole optional-score change exists to prevent,
    so it is refused at construction rather than rendered as an empty column.

    **`n_transcripts`/`transcript_sessions`/`transcript_subagents` are deliberately PER-ROW, never on
    `EvalReport`** — the same reasoning `EvalReport`'s own docstring gives for carrying no `taskset`
    field. `score` scores an arbitrary glob of traces, each with its own composition, so there is no
    single meaningful "composition of this report" the way there is a single `judge_model` or
    `prompt_version`; only a per-row fact is well-defined. Sourced from `ctx_distillery.trace_io
    .transcript_facts` — the same guard `ctx_distillery.rubric.trace_facts()["n_transcripts"]` and
    `ctx_distillery_studio.mapper.transcript_composition` share (invariant 11) — so all three default
    to `None`, never a fabricated `0`, when a run's own trace never recorded them.
    """

    run_id: str
    trace_path: str
    score: EvalScore | None = None
    unscored_reason: str = Field(
        default="", description="why this run has no score — REQUIRED whenever `score` is None"
    )
    n_transcripts: int | None = Field(
        default=None,
        description="how many transcripts this run's judge saw — None if the trace never recorded it",
    )
    transcript_sessions: int | None = Field(
        default=None, description="of n_transcripts, how many were main-thread sessions"
    )
    transcript_subagents: int | None = Field(
        default=None, description="of n_transcripts, how many were subagent transcripts"
    )

    @property
    def unscored(self) -> bool:
        """Derived, never stored — see the class docstring on why this is not a field."""
        return self.score is None

    @model_validator(mode="after")
    def _unscored_rows_must_state_a_reason(self) -> EvalRow:
        if self.score is None and not self.unscored_reason.strip():
            raise ValueError(
                "an unscored EvalRow must carry an `unscored_reason` — a blank one is the silent "
                "failure this shape exists to prevent (see `judge.JudgeVerdict.reason`)"
            )
        return self


class EvalReport(BaseModel):
    """A batch of runs, the per-category MEANS only (never a composite), and the run's PROVENANCE.

    `means` is empty for an empty `rows` list (nothing to average) rather than a dict of zeros, which
    would misleadingly claim a real (low) score for a taskset that produced no rows at all.

    Parity pass 4 added the four provenance fields. `prompt_version` is the load-bearing one: without
    it a number is not attributable to the prompt that produced it, which is the entire point of
    `judge.PROMPT_VERSION` — two scorecards with different prompts are not comparable, and nothing
    else in the report would say so. `n` / `n_unscored` make the denominator explicit, so a batch
    where the judge failed on half the runs cannot be read as a clean mean over all of them.

    Still NO `taskset` field, unlike every sibling's report — but the REASON changed and is worth
    restating rather than leaving as inherited wording. It used to be "there is no taskset concept
    here at all"; there is one now (`taskset.EvalTask` / `load_taskset` / `demo_taskset`). What holds
    instead is that a taskset is OPTIONAL on both paths that produce a report: `score --taskset` may
    be omitted entirely, and even `run` can be handed a `{id, reference}`-only file. A field that is
    empty on the package's primary invocation is not provenance, it is a blank column — and
    `prompt_version` already records the thing that actually changes a number.
    """

    n: int = Field(default=0, description="total runs considered (scored + unscored)")
    n_unscored: int = Field(default=0, description="runs excluded from the means (the judge failed)")
    judge_model: str = Field(default="", description="the judge actually used — a model id, or 'stub'")
    prompt_version: str = Field(default="", description="the judge prompt version the scores came from")
    means: dict[str, float] = Field(default_factory=dict)
    rows: list[EvalRow] = Field(default_factory=list)


def compute_means(rows: list[EvalRow]) -> dict[str, float]:
    """The per-category arithmetic mean across the SCORED rows — the ONLY aggregate this package computes.

    No composite/weighted score: the eval-member boundary is explicit — reward-free, per-category
    means only, no composite (`CLAUDE.md`'s known-simplification bullet on `ctx_distillery/rubric.py`
    and this package: "no field anywhere functions as a score") — so combining TF/TA/TG/PA into one
    number is left to whatever downstream trainer eventually scores these labels, never done here.

    Unscored rows (`row.score is None`) are excluded from BOTH the sum and the DENOMINATOR. Counting
    them in the denominator would be arithmetically identical to scoring them 0, which is exactly the
    fake-zero this shape exists to prevent; `EvalReport.n_unscored` is where their existence is
    reported instead. A batch in which NOTHING scored yields `{}`, the same as no rows at all — there
    is nothing to average either way.
    """
    scored = [row for row in rows if row.score is not None]
    if not scored:
        return {}
    return {
        category: sum(getattr(row.score, category) for row in scored) / len(scored)
        for category in EVAL_CATEGORIES
    }
