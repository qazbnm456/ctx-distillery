"""Human-legible renderings of an `AssembledPlan` — one implementation, three consumers.

`render_plan` used to live in the `eval/` member's `score.py`, where it was written to feed the
LLM-as-judge prompt. The CLI's `show` command needs the SAME rendering (a reviewer reads exactly
what the judge reads, which is the point), so it moved here rather than being copied — the same
"one implementation per job, never a per-member copy" reasoning `CLAUDE.md` invariant 11 already
gives for `rubric.plan_from_events` and `trace_io.load_trace`. `eval/`'s `score.py` imports it from
here now and keeps no copy; that module's own `__all__` still re-exports it, so nothing downstream
of `eval/` had to change.

`AssembledPlan` comes from `schema.py`, NOT from `session.py` where it used to be defined: this
module is imported by `eval/`'s `score.py`, and routing a plain dataclass import through the module
that constructs an `RLMTask` is what made a fully-offline eval run pay for dspy. Same shape, same
`from ctx_distillery.session import AssembledPlan` still working for anyone who wants it — just not
via the heavy path (see `schema.py`'s docstring).

Rendering only. Nothing here writes a file — `render_plan` returns a string and the CLI `print`s it,
which is why `ctx-distillery show` deliberately has no `--out` flag (its sibling projects' `render`
commands do). See `cli.py`'s module docstring: `CLAUDE.md` invariant 1's mutation scan covers every
module in this package except `apply.py`, and "the renderer cannot write" is a property worth having
rather than an inconvenience worth working around.
"""

from __future__ import annotations

import dataclasses

from .schema import AssembledPlan

__all__ = ["plan_as_dict", "render_plan"]


def render_plan(plan: AssembledPlan) -> str:
    """A human/judge-legible rendering of an assembled plan — one line per candidate.

    Deliberately plain text, not JSON: the judge prompt reads more naturally this way, and nothing
    downstream parses this rendering back (the structured facts live in `ctx_distillery.rubric`, a
    completely separate, deterministic path). The leading `[i]` is the candidate's LIST INDEX, which
    is exactly what `apply_plan`'s `approved_ids` (and so `ctx-distillery-apply --approve`) takes —
    a reviewer reads an index here and types that same index there.

    FIXED while moving it here: the no-candidates branch used to `return` early, which DROPPED the
    run-level problems line entirely — so the single most important case (`assemble(events, None)`,
    "no plan was produced by this run", a run that died before SUBMIT) rendered as the bare and
    actively misleading "proposed no candidates", both to a reviewer and to the eval judge. The two
    sections are now independent, which changes nothing about a plan that has candidates or a plan
    that has neither.
    """
    lines = []
    if not plan.candidates:
        lines.append("(this run's plan proposed no candidates)")
    for i, candidate in enumerate(plan.candidates):
        lines.append(f"[{i}] action={candidate.action} artifact_id={candidate.artifact_id!r}")
        if candidate.key_fields:
            lines.append(f"    key_fields={candidate.key_fields!r}")
        if candidate.draft:
            lines.append(f"    draft (ok={candidate.draft_ok}):\n{candidate.draft}")
        for relative_path, extra in candidate.extra_files.items():
            lines.append(f"    extra file {relative_path} (ok={extra.draft_ok}):\n{extra.draft}")
        if candidate.problems:
            lines.append(f"    problems: {candidate.problems!r}")
    if plan.problems:
        lines.append(f"(run-level problems: {plan.problems!r})")
    return "\n".join(lines)


def plan_as_dict(plan: AssembledPlan) -> dict:
    """The same plan as a JSON-ready dict — `AssembledPlan`/`AssembledCandidate` are dataclasses.

    Used by `ctx-distillery show --json`. `dataclasses.asdict` rather than a hand-written mapping so
    a field added to `AssembledCandidate` shows up here automatically instead of being silently
    dropped from the machine-readable view.
    """
    return dataclasses.asdict(plan)
