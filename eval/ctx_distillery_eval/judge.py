"""The judge seam — reads an assembled plan + its transcript excerpt(s), returns an `EvalScore`.

**Resolved per implementation-plan audit (`docs/IMPL_PLAN.md`)**: a finished trace does NOT carry
the raw transcript verbatim — it is redacted host-side before the run and passed as a task INPUT,
never itself recorded as a `tool_call` (`ctx_distillery/session.py:run_distillation`). Scoring
against `read_transcript_chunk` / `read_memory_file` tool_call RESULTS recorded in the trace is not a
viable lossier substitute either: those payloads carry only offset/length/path/chars metadata by
explicit design (`ctx_distillery/tools/transcript_reader.py`, `ctx_distillery/tools/memory_reader.py`
docstrings — "never the body"), so scoring against them would score an EMPTY substitute, not a
degraded one. The judge therefore takes the transcript text(s) as an explicit, MANDATORY argument
alongside the rendered plan — there is no trace-only fallback path, here or in the CLI.

Rubric-free: the judge prompt asks artifact-framed questions directly (per `docs/DESIGN.md`'s
eval-member table) — it never imports or references `rlm_kit.rubric` / `ctx_distillery.rubric`, and
never sees a criterion's deterministic `observed` facts. This keeps the judge a genuinely
independent, artifact-level read, decoupled from the rollout side's own fact-surfacing.
"""

from __future__ import annotations

from typing import Protocol

from .schema import EvalScore

#: The artifact-framed question each category asks the judge, verbatim from `docs/DESIGN.md`'s
#: eval-member table — kept as data (not just prose in a docstring) so `build_prompt` can render it
#: without the wording drifting out of sync with the design doc's table over time.
JUDGE_QUESTIONS = {
    "TF": "Does the plan capture what's actually worth keeping from the supplied transcript(s)?",
    "TA": "Did the plan's judgements (what to prune/promote/keep) follow a sensible evidentiary approach?",
    "TG": "Is each candidate's rationale plausibly supported by the actual transcript content?",
    "PA": "Are the drafted memory/skill files themselves clear, well-scoped, and correctly targeted?",
}


def build_prompt(plan_text: str, transcript_texts: list[str]) -> str:
    """Render the rubric-free judge prompt from the plan's rendering + the raw transcript text(s).

    Pure string assembly — no model call here. `plan_text` is expected to already be a human-legible
    rendering of the assembled plan (see `score.render_plan`); `transcript_texts` are the SAME texts
    the run was actually given (redacted, per this project's own redaction policy — the judge reads
    nothing more sensitive than the planner itself saw).
    """
    excerpts = "\n\n".join(
        f"--- transcript {i} ---\n{text}" for i, text in enumerate(transcript_texts)
    )
    questions = "\n".join(f"- {category}: {question}" for category, question in JUDGE_QUESTIONS.items())
    return (
        "You are scoring a proposed distillation plan against the transcript(s) it was drawn from.\n"
        "Score each of the following on a scale of 0-10, and give a short rationale.\n\n"
        f"{questions}\n\n"
        "=== PLAN ===\n"
        f"{plan_text}\n\n"
        "=== TRANSCRIPT(S) ===\n"
        f"{excerpts}\n"
    )


class Judge(Protocol):
    """A judge is anything callable as `judge(plan_text, transcript_texts) -> EvalScore`."""

    def __call__(self, plan_text: str, transcript_texts: list[str]) -> EvalScore: ...


class StubJudge:
    """The default, offline, fully-deterministic judge — fixed scores, no model call at all.

    This is the tested default path, per `docs/IMPL_PLAN.md`: "ships behind the `judge` extra,
    default path uses a stub judge with fixed scores, same as every sibling eval member." A REAL
    judge (behind the optional `judge` extra) is deliberately deferred — see `eval/README.md`.
    """

    def __init__(self, *, tf: float = 5.0, ta: float = 5.0, tg: float = 5.0, pa: float = 5.0) -> None:
        self._score = EvalScore(TF=tf, TA=ta, TG=tg, PA=pa, notes="stub judge — fixed deterministic scores")

    def __call__(self, plan_text: str, transcript_texts: list[str]) -> EvalScore:
        return self._score
