"""One-shot driver + assemble-on-read: run a distillation, then re-source what really happened.

This project proposes ONE plan per run — there is no "adopt the best of N" search loop (nothing here
has a reward signal to rank candidates by). So the driver is deliberately linear: ingest once,
redact once, run once, assemble once.

`assemble` is the read side of `CLAUDE.md` invariant (2). The planner's `DistillPlan` carries only
`{action, artifact_id, key_fields}`; the actual drafted markdown lives on the `draft_memory_file` /
`draft_skill_file` `tool_call` events. Assembling by `artifact_id` means the plan's label can never
drift from the bytes it describes — and a candidate naming an `artifact_id` no tool call produced is
reported as a PROBLEM (unassemblable) rather than trusted or raising.

Nothing in this module writes or applies anything. The returned `AssembledPlan` is inert; applying it
is a separate, human-gated step outside the RLM trajectory entirely.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from rlm_kit.trace import EVENT_TOOL_CALL, TraceRecorder

from .adapters.base import ArtifactRef, HarnessAdapter
from .redact import redact_transcript
from .rubric import default_rubric, rubric_to_meta
from .task import DistillPlan, DistillSession
from .trace_io import dict_events, load_trace

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


def render_memory_index(memory_index: Sequence[ArtifactRef]) -> str:
    """Render the index snapshot for the signature's `memory_index: str` input field.

    Deliberately terse (one line per artifact): the planner pulls a full body through
    `read_memory_file`, so the prompt-side view only needs to be enough to decide WHAT to read.
    """
    if not memory_index:
        return "(the memory store is empty — this project has no memory or skill files yet)"
    return "\n".join(
        f"- [{ref.kind}] {ref.name}: {ref.description or '(no description)'} ({ref.path})"
        for ref in memory_index
    )


async def run_distillation(
    adapter: HarnessAdapter,
    chat_fn: Any,
    trace_path: str,
    *,
    redact: Callable[[str], str] = redact_transcript,
    run_id: str | None = None,
    meta: dict | None = None,
    **kw: Any,
) -> AssembledPlan:
    """Ingest once, redact once, run one `DistillSession`, and assemble the result.

    * `adapter.ingest()` is called EXACTLY ONCE. Its `memory_index` is the immutable snapshot every
      tool closes over (so the read allowlist can't shift mid-run), and its `transcripts` are
      redacted IMMEDIATELY into the one list threaded into both the task's constructor and
      `.arun()` — which is what makes "nothing unredacted ever reaches the model" a property of the
      code rather than a claim (CLAUDE.md invariant 3).
    * Extra `**kw` go to `DistillSession` (e.g. `config=`, `interpreter=` for an offline test).
    * Writes/applies nothing: the returned `AssembledPlan` is inert until a human acts on it.
    """
    raw = adapter.ingest()
    redacted_transcripts = [redact(t) for t in raw.transcripts]
    memory_index = list(raw.memory_index)

    task = DistillSession(
        memory_index=memory_index,
        chat_fn=chat_fn,
        transcripts=redacted_transcripts,
        **kw,
    )
    rid = run_id or uuid.uuid4().hex[:12]
    run_meta = {"transcripts": len(redacted_transcripts), "memory_artifacts": len(memory_index)}
    run_meta["rubric"] = rubric_to_meta(default_rubric())
    run_meta.update(meta or {})
    with TraceRecorder(trace_path, run_id=rid, meta=run_meta):
        plan = await task.arun(
            transcripts=redacted_transcripts,
            memory_index=render_memory_index(memory_index),
        )
    # `load_trace`, not `load_events`: this trace is well-formed by construction (the recorder just
    # wrote it), so this is consistency rather than a live bug — but it means no module in the
    # workspace passes `run_id=` into `load_events`'s own unguarded filter any more (`trace_io.py`).
    return assemble(load_trace(trace_path, run_id=rid), plan)
