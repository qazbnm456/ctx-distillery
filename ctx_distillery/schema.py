"""The plan shapes — the SUBMIT contract, the assembled read side, and nothing that needs dspy.

This module exists for one measurable reason: **`eval/` and `studio/` must not pay for dspy.** Both
members read a finished run's trace and rebuild the plan from it; neither ever constructs an
`RLMTask`, opens a sandbox, or talks to a model. Yet before this module existed, importing either
member's entry module (`eval/`'s CLI, `studio/`'s app) pulled dspy into `sys.modules`, because the
only route to `assemble` / `AssembledPlan` was `ctx_distillery.session`, which imports
`ctx_distillery.task`, which does `from rlm_kit import RLMTask`. Measured before the split:

(The two member packages are named by DIRECTORY here, never by import name, and that is deliberate
rather than coy: `eval/tests/test_boundary.py`'s one-way fence is a TEXTUAL scan over every `.py` in
this package, so spelling either package's real module name — even in prose, even in the sentence
explaining why this module exists to serve them — turns that test red. `studio/`'s equivalent
already uses `ast` for exactly this reason; `eval/`'s stays textual on purpose, matching the
`diff-sentry` original it mirrors.)

    ctx eval.cli         -> dspy: True          diff_sentry_eval.cli -> dspy: False
    studio.app           -> dspy: True          toolscout_eval.cli   -> dspy: False

Every sibling project (`cve-reverser`, `diff-sentry`, `toolscout`) already keeps its output models in
a dspy-free `schema.py` for exactly this reason; ctx-distillery was the outlier, and the cost was a
fully-offline `ctx-distillery-eval score --stub` run — and EVERY studio HTTP request — importing an
LM framework it never calls.

**What lives here, and why each one qualifies.**

* `DistillAction` / `DistillCandidate` / `DistillPlan` — plain pydantic. They are handed to dspy as
  `DistillSession.output_model`, but they never IMPORT it; a pydantic model is a shape, and a shape
  has no business knowing which framework will serialize it.
* `PROMOTION_ACTIONS` (and the `_DRAFT_TOOL_FOR_ACTION` map it is derived from) — the action ->
  drafting-tool correspondence that `assemble` and `apply.py` both read. A constant, not a behaviour.
* `AssembledCandidate` / `AssembledPlan` / `assemble` — VERIFIED dspy-free, not assumed. `assemble`'s
  entire dependency set is `EVENT_TOOL_CALL` (a string constant from `rlm_kit.trace`),
  `trace_io.dict_events`, and the dataclasses below; `rlm_kit.trace` itself imports no dspy. It is a
  pure function over `(events, plan)` — it does no I/O, holds no state, and would give the same
  answer replayed a year later from the same JSONL. That is exactly what makes it safe to move away
  from the async driver that happens to call it once.

**What deliberately did NOT move, and why.** `session.py` keeps `run_distillation` (the async driver:
it constructs a `DistillSession`, so it is dspy-bearing by definition) and `render_memory_index` (it
renders `adapters.base.ArtifactRef`s for the task's `memory_index: str` signature input — that is
prompt-side presentation for one specific task, not part of the plan's shape). `task.py` keeps
`DistillSession`, `PINNED_INTERPRETER` and `_forced_config`, which are the RLMTask and its sandbox
pin — `CLAUDE.md` invariant 1 requires the pin be stated IN THE TASK, so it stays there.

**This is a MOVE, never an API break.** `task.py` and `session.py` both re-export every name that
left them, so `from ctx_distillery.task import DistillPlan` and `from ctx_distillery.session import
assemble` keep working byte-for-byte — the root tests, `eval/`, `studio/` and this repo's own docs
all reference the old paths, and a refactor that renames a public import path is a different, larger
decision than the one this module makes.

Nothing here writes: `CLAUDE.md` invariant 1's mutation scan (`tests/test_no_write_capability.py`)
picks this module up automatically, because it scans every `.py` under `ctx_distillery/` except the
human-gated writer. That is the intent — a shapes module that could touch the filesystem would be a
contradiction in terms.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field
from rlm_kit.trace import EVENT_TOOL_CALL

from .trace_io import dict_events

__all__ = [
    "PROMOTION_ACTIONS",
    "AssembledCandidate",
    "AssembledPlan",
    "DistillAction",
    "DistillCandidate",
    "DistillPlan",
    "assemble",
]

# --- Output contract -----------------------------------------------------------------------
#
# Per CLAUDE.md invariant (2), the judgement-only SUBMIT shape: the plan carries only
# {action, artifact_id, key_fields} per candidate — never the drafted memory/skill content
# itself. The actual markdown+frontmatter text for a promotion is produced by a separate
# drafting tool (`draft_memory_file` / `draft_skill_file`) and re-sourced on READ by matching the
# tool-call event whose `artifact_id` matches this candidate's — never from the plan's own claim
# about what it wrote. This keeps the judgement (what to do) and the authored content (the
# tool-call event) from ever drifting apart.

DistillAction = Literal["keep", "prune", "promote_to_memory", "promote_to_skill"]


class DistillCandidate(BaseModel):
    """One judgement about one transcript segment or existing memory/skill artifact."""

    action: DistillAction = Field(..., description="The judgement for this candidate.")
    artifact_id: str | None = Field(
        default=None,
        description=(
            "For promote_to_memory / promote_to_skill: the artifact_id emitted by the matching "
            "draft_memory_file / draft_skill_file tool-call event. Null for keep/prune, where "
            "there is no drafted artifact to assemble."
        ),
    )
    key_fields: dict = Field(
        default_factory=dict,
        description=(
            "Structured, deterministic-check-friendly fields for this candidate — e.g. which "
            "transcript segment(s) it covers, a one-line reason, or a cross-reference to another "
            "candidate flagged as an overlap/conflict. Never the drafted artifact body itself. "
            "For a `prune` candidate, `target_path` is REQUIRED by convention: the exact `path` of "
            "the existing artifact being pruned, verbatim from list_memory_files() (see "
            "ctx_distillery/apply.py — a prune with no matching target_path is refused). For a "
            "`promote_to_skill` candidate, `scope` is REQUIRED by the same convention: \"project\" "
            "for a finding tied to this project, \"global\" for a portable technique — it selects "
            "which skills directory the apply step would write into, and a promote_to_skill with no "
            "valid scope is refused."
        ),
    )


class DistillPlan(BaseModel):
    """The full proposed plan for one distillation run. Inert until a human applies it."""

    candidates: list[DistillCandidate] = Field(
        default_factory=list,
        description="One entry per transcript segment / memory file judged by this run.",
    )


# --- Assemble-on-read ----------------------------------------------------------------------
#
# The read side of CLAUDE.md invariant (2). The planner's `DistillPlan` carries only
# `{action, artifact_id, key_fields}`; the actual drafted markdown lives on the `draft_memory_file` /
# `draft_skill_file` `tool_call` events. Assembling by `artifact_id` means the plan's label can never
# drift from the bytes it describes — and a candidate naming an `artifact_id` no tool call produced
# is reported as a PROBLEM (unassemblable) rather than trusted or raising.

#: Which drafting tool authors each promotion action's artifact.
_DRAFT_TOOL_FOR_ACTION = {
    "promote_to_memory": "draft_memory_file",
    "promote_to_skill": "draft_skill_file",
}
PROMOTION_ACTIONS = tuple(_DRAFT_TOOL_FOR_ACTION)


@dataclass
class AssembledCandidate:
    """One plan candidate, with its drafted text re-sourced from the trace (never from the plan)."""

    action: str
    artifact_id: str | None = None
    key_fields: dict = field(default_factory=dict)
    #: The verbatim drafted text, for a promotion whose artifact_id matched a tool_call event.
    draft: str | None = None
    #: That drafting call's own deterministic validation verdict.
    draft_ok: bool | None = None
    problems: list[str] = field(default_factory=list)


@dataclass
class AssembledPlan:
    """The whole run, assembled. `problems` here are RUN-level (per-candidate ones live inline)."""

    candidates: list[AssembledCandidate] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _draft_calls(events: Sequence[dict], tool: str) -> dict[str, dict]:
    """`{artifact_id: payload}` for `tool`'s `tool_call` events — LAST call per id wins.

    Last-wins because a repair loop legitimately re-drafts; the final call for an id is the one whose
    bytes the plan is describing. (Each call actually mints a fresh id, so this is a safety net.)
    """
    found: dict[str, dict] = {}
    for event in events:
        if event.get("type") != EVENT_TOOL_CALL:
            continue
        payload = event.get("payload") or {}
        if payload.get("tool") != tool:
            continue
        artifact_id = payload.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id:
            found[artifact_id] = payload
    return found


def assemble(events: Sequence[dict], plan: DistillPlan) -> AssembledPlan:
    """Re-source each promotion candidate's drafted text from the trace, keyed by `artifact_id`.

    Structural checks only, and none of them raise: a broken candidate is reported so a human sees
    exactly which part of the plan is not backed by a real drafting call.

    FIXED per adversarial review: "none of them raise" did NOT hold for a malformed trace.
    `_draft_calls` scans EVERY event before the candidate loop, so a non-dict trace line (see
    `trace_io.py`) raised a raw `AttributeError` for ANY non-`None` plan — including an all-`keep`
    plan with no artifact to assemble at all. Only the `plan is None` path escaped, and only
    because it returns before touching `events`.
    """
    assembled = AssembledPlan()
    if plan is None:
        assembled.problems.append("no plan was produced by this run")
        return assembled
    # A non-dict trace line must never reach `_draft_calls`'s unconditional `.get`. Filtered ONCE
    # here rather than inside `_draft_calls` (which runs once PER drafting tool), so the pass is
    # O(events), not O(events x tools).
    events = dict_events(events)
    by_tool = {tool: _draft_calls(events, tool) for tool in _DRAFT_TOOL_FOR_ACTION.values()}

    for candidate in plan.candidates:
        out = AssembledCandidate(
            action=candidate.action,
            artifact_id=candidate.artifact_id,
            key_fields=dict(candidate.key_fields or {}),
        )
        if candidate.action not in PROMOTION_ACTIONS:
            # keep / prune: no artifact to assemble. An artifact_id here is a plan inconsistency.
            if candidate.artifact_id:
                out.problems.append(
                    f"action {candidate.action!r} carries artifact_id "
                    f"{candidate.artifact_id!r}, but only "
                    f"{list(PROMOTION_ACTIONS)} draft an artifact"
                )
            assembled.candidates.append(out)
            continue
        tool = _DRAFT_TOOL_FOR_ACTION[candidate.action]
        if not candidate.artifact_id:
            out.problems.append(
                f"action {candidate.action!r} carries no artifact_id, so there is no {tool} call "
                f"to assemble its text from"
            )
            assembled.candidates.append(out)
            continue
        payload = by_tool[tool].get(candidate.artifact_id)
        if payload is None:
            # Either the id was fabricated, or it was drafted by the OTHER tool (a mismatched kind) —
            # distinguish the two, because they mean different things to a reviewer.
            other = next(
                (
                    name
                    for name, calls in by_tool.items()
                    if name != tool and candidate.artifact_id in calls
                ),
                None,
            )
            if other is not None:
                out.problems.append(
                    f"artifact_id {candidate.artifact_id!r} was drafted by {other} but the "
                    f"candidate's action {candidate.action!r} expects {tool}"
                )
            else:
                out.problems.append(
                    f"no {tool} tool_call for artifact_id {candidate.artifact_id!r} "
                    f"(the plan names an artifact this run never drafted)"
                )
            assembled.candidates.append(out)
            continue
        draft = payload.get("draft")
        out.draft = draft if isinstance(draft, str) else None
        out.draft_ok = bool(payload.get("ok"))
        if not (out.draft or "").strip():
            out.problems.append(f"artifact {candidate.artifact_id!r} recorded an empty draft")
        if not out.draft_ok:
            errors = payload.get("errors") or []
            out.problems.append(
                f"artifact {candidate.artifact_id!r} failed its format check: "
                f"{'; '.join(str(e) for e in errors) or 'no detail recorded'}"
            )
        assembled.candidates.append(out)
    return assembled
