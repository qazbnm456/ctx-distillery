"""Map a ctx-distillery trace event -> a public SSE event (the single source of truth for the
streamed event surface). Pure function, no FastAPI/web deps — unit-tested independently of the
server, mirroring `diff_sentry_studio.mapper`'s own separation.

A trace event is `{schema, run_id, step_id, ts, type, payload}` (rlm-harness's frozen trace/v1). We
surface only the events a UI needs and rename them to a stable `distill.<noun>.<verb>` vocabulary.
Unknown/internal events return None (skipped) — never guessed at.

`main_step`/`sub_call` are mapped, not dropped. ADDED per an implementation-plan audit — an earlier
draft of this module silently fell through to `return None` for both, which is a real gap against
this initiative's own motivating goal (seeing every step's
context and results): `rlm_harness.task.record_main_trajectory` emits `main_step` UNCONDITIONALLY for
every `RLMTask` run (not opt-in, and `DistillSession` does not disable it), and
`rlm_harness.sub_lm.bind_recorder_to_sub_lm` emits `sub_call` for any recursive sub-LM escalation the
root planner issues. For a judgement-only task with only six tools, the planner's OWN reasoning
turns are plausibly the richest part of the trace — dropping them from the live feed would have been
a silent regression against the whole point of building a studio at all. Payload shapes confirmed
against rlm_harness/trace.py's real `record_main_trajectory` (`turn`/`reasoning`/`code`/`output`) and
`sub_lm.py`'s `bind_recorder_to_sub_lm` (`input`/`processed`/`raw`).
"""

from __future__ import annotations

from typing import Any

from rlm_harness.trace import (
    EVENT_FINAL,
    EVENT_MAIN_STEP,
    EVENT_RESULT,
    EVENT_RUN_END,
    EVENT_RUN_START,
    EVENT_SUB_CALL,
    EVENT_TOOL_CALL,
)

# THE shared implementation (`CLAUDE.md` invariant 11) — moved out of this module once a third
# consumer (`ctx_distillery_eval.score.score_run`, via `trace_io.transcript_facts`) needed the
# identical guard. Re-imported (not re-derived) so `from ctx_distillery_studio.mapper import
# transcript_composition` — this module's own tests, `iterations.py` — keeps resolving unchanged.
# This is mapper.py's first real `ctx_distillery` import: `trace_io` is plain, dspy-free,
# pure-function code (no network, no fastapi), so it costs this module nothing it doesn't already
# pay for indirectly via `ctx_distillery_studio.app`'s own dependency on `ctx_distillery`.
from ctx_distillery.trace_io import transcript_composition

#: The three read-only, progressive-disclosure tools — no single "the" interesting field, so they
#: fall through to a generic scalar-field passthrough (mirroring `_scalar_fields`'s own fallback
#: role in `diff_sentry_studio.mapper`).
_EVIDENCE_TOOLS = ("list_memory_files", "read_memory_file", "read_transcript_chunk")
#: The three drafting tools — deliberately WITHOUT the full `draft` text (kept out of the live feed
#: the same way diff-sentry keeps bulky fields out of `_scalar_fields`); the full text is what
#: `GET /v1/runs/{run_id}` returns, paired with its plan entry. `draft_skill_extra_file` joined the
#: other two once it existed — without it, a live run using the sixth tool would have those calls
#: silently DROPPED from the feed (the tool_call branch's own `return None` for anything unrecognized),
#: not merely under-detailed.
_DRAFT_TOOLS = ("draft_memory_file", "draft_skill_file", "draft_skill_extra_file")

#: Payload keys a bespoke tool event already surfaces (or that are bulky/nested) — dropped from the
#: generic scalar-field passthrough so an evidence-read tool_call streams meaningful short fields
#: instead of a raw blob. This project's read-only tools never carry a `draft` body (only the two
#: drafting tools do, and those get their own bespoke event above), but the drop-set stays defensive
#: about any list/dict-shaped value regardless of key name — see `_scalar_fields` below.
#: `resolved_path` and `note` are dropped for a DIFFERENT reason than the three above, and it is a
#: privacy one: this project's evidence reads run against the operator's OWN `~/.claude` store, so
#: `read_memory_file` records an absolute path like
#: `/Users/<you>/.claude/projects/-Users-<you>-<project>/memory/<file>.md`, and its refusal `note`
#: embeds a model-supplied path verbatim in a sentence. Both are short strings, so the length guard
#: below never caught them and BOTH were streaming into the live feed, where `app.js` renders the
#: whole data object. Found by an adversarial review of the trajectory-drawer design — the leak
#: predates that work and had no test. `name` + `kind` + `chars` + `truncated` already say which
#: artifact was read; the absolute path adds nothing a reviewer needs and identifies the machine.
_SCALAR_DROP = frozenset({"tool", "ok", "args", "resolved_path", "note"})
_MAX_SCALAR = 200  # a payload scalar longer than this is treated as bulky and dropped

#: Any key whose value could carry a filesystem path is dropped by NAME above. This is the
#: belt-and-braces half: a value that LOOKS like an absolute or home-relative path is dropped
#: whatever it is called, so a future tool that records one under a new key does not reopen the hole.
#: Deliberately narrow — it tests the VALUE's shape, never a substring, so an ordinary sentence that
#: merely contains a slash is unaffected.
def _looks_like_a_path(value: str) -> bool:
    return value.startswith(("/", "~/", "\\", "./", "../"))


def _scalar_fields(p: dict) -> dict:
    """The payload's SHORT scalar fields (str/int/float/bool) for a tool with no bespoke event —
    the already-surfaced `tool`/`ok`/`args` keys are dropped, any list/dict-shaped value is dropped
    regardless of its key name, and any value that is or looks like a filesystem path is dropped so
    the feed never identifies the operator's machine (see `_SCALAR_DROP`)."""
    out: dict = {}
    for k, v in (p or {}).items():
        if k in _SCALAR_DROP:
            continue
        if isinstance(v, (bool, int, float)) or (
            isinstance(v, str) and len(v) <= _MAX_SCALAR and not _looks_like_a_path(v)
        ):
            out[k] = v
    return out


def _ev(name: str, data: dict) -> dict[str, Any]:
    return {"event": name, "data": data}


def to_event(trace_event: dict) -> dict[str, Any] | None:
    """Return `{"event": <name>, "data": {...}}` for a surfaced trace event, else None."""
    t = trace_event.get("type")
    p = trace_event.get("payload") or {}

    if t == EVENT_RUN_START:
        # `or {}` absorbs a falsy meta but NOT a truthy non-dict — the same one-level-in shape as
        # the payload bug `trace_io.dict_events` fixes. A `"meta": "nope"` used to kill the SSE
        # generator on its FIRST event, so the whole replay returned an empty stream and the UI
        # showed "connection closed". Found by review.
        meta = p.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        rubric = meta.get("rubric") or []
        return _ev(
            "distill.run.created",
            {
                "transcripts": meta.get("transcripts"),
                **transcript_composition(meta),
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
                    # Only `draft_skill_extra_file` ever carries this — `None` for the other two,
                    # never fabricated as an empty string (absent is a different claim than "").
                    "relative_path": p.get("relative_path"),
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
        # A real finished trace holds BOTH `final` (rlm_harness.task.record_main_trajectory) and
        # `run_end` (the recorder's __exit__). Mapping both would emit the terminal event TWICE per
        # replay, and `final` lands BEFORE `result` — mirrors `diff_sentry_studio.mapper`'s own
        # documented reason for skipping it verbatim. `run_end` is the sole terminal.
        return None
    return None  # unknown event type — skip
