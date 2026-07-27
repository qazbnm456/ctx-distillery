"""Map a ctx-distillery trace event -> a public SSE event (the single source of truth for the
streamed event surface). Pure function, no FastAPI/web deps — unit-tested independently of the
server, mirroring `diff_sentry_studio.mapper`'s own separation.

A trace event is `{schema, run_id, step_id, ts, type, payload}` (rlm-kit's frozen trace/v1). We
surface only the events a UI needs and rename them to a stable `distill.<noun>.<verb>` vocabulary.
Unknown/internal events return None (skipped) — never guessed at.

Per `docs/DESIGN.md`'s Studio table: `main_step`/`sub_call` are mapped, not dropped. ADDED per
implementation-plan audit — an earlier draft of this module silently fell through to `return None`
for both, which is a real gap against this initiative's own motivating goal (seeing every step's
context and results): `rlm_kit.task.record_main_trajectory` emits `main_step` UNCONDITIONALLY for
every `RLMTask` run (not opt-in, and `DistillSession` does not disable it), and
`rlm_kit.sub_lm.bind_recorder_to_sub_lm` emits `sub_call` for any recursive sub-LM escalation the
root planner issues. For a judgement-only task with only five tools, the planner's OWN reasoning
turns are plausibly the richest part of the trace — dropping them from the live feed would have been
a silent regression against the whole point of building a studio at all. Payload shapes confirmed
against rlm_kit/trace.py's real `record_main_trajectory` (`turn`/`reasoning`/`code`/`output`) and
`sub_lm.py`'s `bind_recorder_to_sub_lm` (`input`/`processed`/`raw`).
"""

from __future__ import annotations

from typing import Any

from rlm_kit.trace import (
    EVENT_FINAL,
    EVENT_MAIN_STEP,
    EVENT_RESULT,
    EVENT_RUN_END,
    EVENT_RUN_START,
    EVENT_SUB_CALL,
    EVENT_TOOL_CALL,
)

#: The three read-only, progressive-disclosure tools — no single "the" interesting field, so they
#: fall through to a generic scalar-field passthrough (mirroring `_scalar_fields`'s own fallback
#: role in `diff_sentry_studio.mapper`).
_EVIDENCE_TOOLS = ("list_memory_files", "read_memory_file", "read_transcript_chunk")
#: The two drafting tools — deliberately WITHOUT the full `draft` text (kept out of the live feed the
#: same way diff-sentry keeps bulky fields out of `_scalar_fields`); the full text is what
#: `GET /v1/runs/{run_id}` returns, paired with its plan entry.
_DRAFT_TOOLS = ("draft_memory_file", "draft_skill_file")

#: Payload keys a bespoke tool event already surfaces (or that are bulky/nested) — dropped from the
#: generic scalar-field passthrough so an evidence-read tool_call streams meaningful short fields
#: instead of a raw blob. This project's read-only tools never carry a `draft` body (only the two
#: drafting tools do, and those get their own bespoke event above), but the drop-set stays defensive
#: about any list/dict-shaped value regardless of key name — see `_scalar_fields` below.
_SCALAR_DROP = frozenset({"tool", "ok", "args"})
_MAX_SCALAR = 200  # a payload scalar longer than this is treated as bulky and dropped


def _scalar_fields(p: dict) -> dict:
    """The payload's SHORT scalar fields (str/int/float/bool) for a tool with no bespoke event —
    the already-surfaced `tool`/`ok`/`args` keys are dropped, and any list/dict-shaped value is
    dropped regardless of its key name, so an evidence-read tool_call streams a meaningful row
    instead of a raw blob."""
    out: dict = {}
    for k, v in (p or {}).items():
        if k in _SCALAR_DROP:
            continue
        if isinstance(v, (bool, int, float)) or isinstance(v, str) and len(v) <= _MAX_SCALAR:
            out[k] = v
    return out


def _ev(name: str, data: dict) -> dict[str, Any]:
    return {"event": name, "data": data}


def to_event(trace_event: dict) -> dict[str, Any] | None:
    """Return `{"event": <name>, "data": {...}}` for a surfaced trace event, else None."""
    t = trace_event.get("type")
    p = trace_event.get("payload") or {}

    if t == EVENT_RUN_START:
        meta = p.get("meta") or {}
        rubric = meta.get("rubric") or []
        return _ev(
            "distill.run.created",
            {
                "transcripts": meta.get("transcripts"),
                "memory_artifacts": meta.get("memory_artifacts"),
                "rubric": {
                    "categories": sorted({c.get("category") for c in rubric if c.get("category")}),
                    "criteria": len(rubric),
                },
            },
        )
    if t == EVENT_MAIN_STEP:
        return _ev(
            "distill.plan.step",
            {"turn": p.get("turn"), "reasoning": p.get("reasoning"), "has_code": bool(p.get("code"))},
        )
    if t == EVENT_SUB_CALL:
        return _ev(
            "distill.sub_lm.call",
            {"input": p.get("input"), "processed_or_raw": p.get("processed") or p.get("raw")},
        )
    if t == EVENT_TOOL_CALL:
        tool = p.get("tool")
        if tool in _EVIDENCE_TOOLS:
            return _ev("distill.evidence.read", {"tool": tool, **_scalar_fields(p)})
        if tool in _DRAFT_TOOLS:
            return _ev(
                "distill.draft.created",
                {
                    "tool": tool,
                    "artifact_id": p.get("artifact_id"),
                    "ok": p.get("ok"),
                    "errors": p.get("errors") or [],
                    "circuit_broken": bool(p.get("circuit_broken")),
                },
            )
        return None  # an unrecognized tool_call is skipped, never guessed at
    if t == EVENT_RESULT:
        return _ev("distill.plan.done", {})
    if t == EVENT_RUN_END:
        return _ev("distill.run.completed", {})
    if t == EVENT_FINAL:
        # A real finished trace holds BOTH `final` (rlm_kit.task.record_main_trajectory) and
        # `run_end` (the recorder's __exit__). Mapping both would emit the terminal event TWICE per
        # replay, and `final` lands BEFORE `result` — mirrors `diff_sentry_studio.mapper`'s own
        # documented reason for skipping it verbatim. `run_end` is the sole terminal.
        return None
    return None  # unknown event type — skip
