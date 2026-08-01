"""Build a structured per-iteration breakdown of a run from its trace — the data behind the studio's
Trajectory drawer. Pure functions (no web deps), unit-tested independently of the server, mirroring
`mapper.py`'s own separation — `mapper.py` now carries one `ctx_distillery.trace_io` import (see
below), but neither module imports FastAPI or anything server-shaped.

A `DistillSession` run is a sequence of `main_step` REPL turns (the planner's reasoning + the Python
code it ran + that code's output). The `tool_call` / `sub_call` events that follow a turn belong to
that turn — its code invoked them. `run_start.meta` is the run's INITIAL state. Wall-clock time is
attributed from the event `ts` deltas: there is NO per-call instrumentation anywhere in this project,
so the gap between consecutive events is the only signal of where the time went. Keep that framing
honest wherever it is displayed — a tool entry's `duration_s` is **planner-think + tool-exec**, not
tool-exec alone.

**`timeline` is FLAT and ALWAYS available; `turn_index` is an OPTIONAL enrichment.** That is a design
consequence of two MEASURED numbers, not a hedge:

- A REAL live run (real model, real pyodide sandbox) spans **20.4s** across its `main_step` ts →
  `per_turn_timing = True`. rlm-kit backfills each turn's ts as it is parsed, so a real trace is
  live-stamped and the interval back-mapping is meaningful.
- The OFFLINE scripted harness this workspace's tests use (`rlm_kit.testing.ScriptedInterpreter` +
  `scripted_lm`) spans **0.0019s** → `per_turn_timing = False`. Every turn lands at effectively one
  instant, so the mapping would be noise and we refuse to fake it.

`turn_index` back-mapping runs ONLY `if per_turn`. So on every trace the test suite can produce — and
on any genuinely fast run — `turn_index` is absent from every timeline entry. A drawer that surfaced
tool calls only THROUGH turn grouping would therefore render nothing in tests and nothing on a fast
run. `timeline` is the always-populated view; `iterations` carries the turns; `turn_index` links them
when, and only when, the link is real.

**`_tool_entry` is ALLOWLIST-shaped, and that is load-bearing.** Every sibling studio reaches a
generic "surface the payload's short scalar fields" (or, in cve-reverser's case, `_preview(args)`)
fallback, and an audit proved by execution that all of them ship real leaks against THIS project's
payloads: `read_memory_file` records `resolved_path` (an absolute path inside the operator's own
home), the drafting tools record `args["evidence"]` (redacted transcript material) and the full
`draft` body, and every refusal records a `note` that embeds a model-supplied path VERBATIM in a
sentence — which no path-shaped-value heuristic can catch, because the path is mid-sentence. So this
module enumerates, per tool, exactly the fields that tool contributes. There is no drop-list (a
drop-list fails open the moment a tool records a new key), `args` is never surfaced wholesale, and
`draft` is carried as `draft_chars` only. That matches `mapper.py`, which drops `resolved_path`/`note`
by name plus a `_looks_like_a_path` value guard, and `CLAUDE.md` invariant 2 / `docs/DESIGN.md` §5.3:
the drafted bytes belong beside their plan entry (`GET /v1/runs/{id}`), not in a scrolling log.

An UNRECOGNIZED tool contributes NO payload fields at all — just its name and `unrecognized: True`.
The read-only tool set is CLOSED (`CLAUDE.md` invariant 1), so an unknown tool means either a trace
from a future build or a corrupted one; neither is a reason to auto-surface unvetted keys. Adding a
tool means adding a branch here. That friction is the point.

One thing this module deliberately does NOT scrub: a turn's own `reasoning`/`code`/`output`. Those
are the REPL's verbatim record and the single largest information gain the drawer offers (`mapper.py`
carries `has_code: bool` and drops `output` entirely). A turn's `output` is the REPL echo of whatever
the planner printed — which routinely INCLUDES a drafting tool's return value, drafted body and all.
That is inherent to showing turns at all; the mitigation is rendering, not filtering (the frontend
must use `el.textContent`, never `innerHTML` — `CLAUDE.md` invariant 10).
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

# The ONE composition reader, imported rather than copied (`CLAUDE.md` invariant 11). `mapper` now
# re-exports it from `ctx_distillery.trace_io` (a THIRD consumer, `ctx_distillery_eval`, forced the
# move) rather than defining it locally — still pure, web-dep-free code, so this adds no weight and
# no dependency direction that did not already exist between the two views of one trace.
from .mapper import transcript_composition

#: Per-field char cap for the bulky free-text fields (a turn's reasoning/code/output, a sub-LM
#: exchange). Generous — rarely hit; it exists to bound a pathological blob, not to redact.
_CAP = 16000
#: Cap for the SHORT identifying strings (a drafted topic, a validator error, an endpoint error).
#: Matches `mapper._MAX_SCALAR`.
_MAX_LABEL = 200

#: The six tools, named rather than spelled inline, so the allowlist branches below read as a roster.
_LIST_MEMORY = "list_memory_files"
_READ_MEMORY = "read_memory_file"
_READ_TRANSCRIPT = "read_transcript_chunk"
_DRAFT_MEMORY = "draft_memory_file"
_DRAFT_SKILL = "draft_skill_file"
_DRAFT_SKILL_EXTRA = "draft_skill_extra_file"


def _looks_like_a_path(value: str) -> bool:
    """A value that IS a filesystem path. Copied from `mapper._looks_like_a_path` deliberately — the
    drawer and the feed two inches away must agree about what a path looks like. Narrow on purpose:
    it tests the value's SHAPE, never a substring, so an ordinary sentence containing a slash (a
    drafted topic like `run pytest from eval/`) is unaffected."""
    return value.startswith(("/", "~/", "\\", "./", "../"))


def _step_key(e: dict) -> int:
    s = str(e.get("step_id", ""))
    return int(s) if s.lstrip("-").isdigit() else 1 << 30


def _preview(s: Any) -> str | None:
    if s is None:
        return None
    s = str(s)
    return s if len(s) <= _CAP else s[:_CAP] + "\n…[truncated — full text in the trace]"


def _label(value: Any) -> str | None:
    """A SHORT identifying string: truncated, never dropped for being long, dropped only when the
    value IS a path.

    The truncate-don't-drop half matters. The siblings' `_scalar_fields` drops any string over its
    cap, which is precisely the length-DEPENDENT behaviour the audit flagged on `draft`: a short one
    leaks verbatim, a long one silently vanishes at the cliff, and a reader cannot tell "absent"
    from "was too long". Here presence depends only on the value's SHAPE.
    """
    if not isinstance(value, str) or not value:
        return None
    if _looks_like_a_path(value):
        return None
    return value if len(value) <= _MAX_LABEL else value[:_MAX_LABEL] + "…"


def _texts(value: Any) -> list[str]:
    """A list-of-short-strings payload field (`kinds`, `errors`) — bounded, never dropped.

    Unlike `_label` these are NOT path-guarded: they are the tool's own structural vocabulary
    (`"memory"`/`"skill"`/`"index"`) and the validator's own messages ("frontmatter `name` is missing
    …"), so dropping one would delete the reason a draft was refused rather than protect anything.
    """
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if isinstance(item, str):
            out.append(item if len(item) <= _MAX_LABEL else item[:_MAX_LABEL] + "…")
    return out


def _as_int(value: Any) -> int | None:
    """An int payload/arg field, or None. Bools are refused (`isinstance(True, int)` is True) and so
    is every non-int — which matters for the `read_transcript_chunk` ARGS in particular: those are
    whatever the planner passed, and a refusal is recorded precisely because it passed junk. A str
    there (`"/etc/passwd"`, say) must never reach the row."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _chars(value: Any) -> int | None:
    """The SIZE of a bulky field — the only thing carried about a `draft` body."""
    return len(value) if isinstance(value, str) else None


def _gap(ts: float | None, prev: float | None) -> float | None:
    return round(ts - prev, 3) if (ts is not None and prev is not None) else None


def _tool_entry(p: dict, gap: float | None) -> dict:
    """One `tool_call` → a UI-ready entry, built from a per-tool ALLOWLIST (see the module docstring).

    The envelope field is `entry_kind`, NOT `kind`: three of the six tools record their OWN `kind`
    in the payload (`read_memory_file` the artifact's kind, the two SKILL/memory drafting tools
    `"memory"`/`"skill"`), and cve-reverser's `setdefault`-onto-`{"kind": "tool"}` form silently
    swallows it. Renaming the envelope is what lets the payload's `kind` survive verbatim.
    `draft_skill_extra_file` carries a `kind` too (`"reference"`/`"script"`, not `"memory"`/
    `"skill"` — a different vocabulary for a different question, "what kind of supplementary file"
    rather than "what kind of artifact"), handled in its own branch below rather than folded into
    the `_DRAFT_MEMORY`/`_DRAFT_SKILL` one: it records a `relative_path` the other two don't have.
    """
    tool = p.get("tool")
    args = p.get("args") if isinstance(p.get("args"), dict) else {}
    e: dict = {"entry_kind": "tool", "tool": tool, "ok": p.get("ok"), "duration_s": gap}

    if tool == _LIST_MEMORY:
        # Records the COUNT, never the listing (the tool's own "record size, not body" convention).
        e.update(label="list memory", count=_as_int(p.get("count")), kinds=_texts(p.get("kinds")))
    elif tool == _READ_MEMORY:
        # `name` is an ArtifactRef basename, never a path. `resolved_path` is the operator's absolute
        # path and `note` embeds a model-supplied one mid-sentence — NEITHER is carried, matching
        # `mapper._SCALAR_DROP`. A refusal therefore reads as `ok: false` with no detail; the reason
        # stays in the trace file, where a human debugging a refusal can read it in context.
        e.update(
            label="read memory",
            name=_label(p.get("name")),
            kind=_label(p.get("kind")),
            chars=_as_int(p.get("chars")),
            truncated=bool(p.get("truncated")),
        )
    elif tool == _READ_TRANSCRIPT:
        # THE audit row: `#index @offset +length of total_length` is exactly what
        # `tools/transcript_reader.py` exists to record — "did the plan's cross-conversation overlap
        # claim really look at both transcripts?" — and nothing surfaced it before this drawer. The
        # window is all ints; the tool never records the text it read (that is its stated audit point).
        e.update(
            label="read transcript",
            transcript_index=_as_int(args.get("transcript_index")),
            offset=_as_int(args.get("offset")),
            limit=_as_int(args.get("limit")),
            length=_as_int(p.get("length")),
            total_length=_as_int(p.get("total_length")),
        )
    elif tool in (_DRAFT_MEMORY, _DRAFT_SKILL):
        # `draft_chars`, NEVER `draft` (invariant 2 / DESIGN §5.3: the bytes belong beside their plan
        # entry). `args["evidence"]` is redacted transcript material and is never carried either —
        # it is bulky, and the turn's own `output` already shows what the planner fed the drafter.
        # `topic`/`procedure` ARE carried: they are the only human-readable name of what was drafted
        # at the moment it happened, they are a handful of words in practice, and `_label` bounds and
        # path-guards them. `reasoning` is not carried: it is the DRAFTER's unbounded free text (it
        # can restate the body it just wrote), and it was `None` on every drafting call of the real
        # live run measured for this module — an unbounded leak surface for no observed content.
        is_skill = tool == _DRAFT_SKILL
        e.update(
            label="draft skill" if is_skill else "draft memory",
            artifact_id=_label(p.get("artifact_id")),
            kind=_label(p.get("kind")),
            draft_chars=_chars(p.get("draft")),
            errors=_texts(p.get("errors")),
            endpoint_error=_label(p.get("endpoint_error")),
            circuit_broken=bool(p.get("circuit_broken")),
        )
        if is_skill:
            e.update(procedure=_label(args.get("procedure")), scope=_label(args.get("scope")))
        else:
            e.update(topic=_label(args.get("topic")), memory_type=_label(args.get("memory_type")))
    elif tool == _DRAFT_SKILL_EXTRA:
        # Same shape as the two drafting tools above (`draft_chars` never `draft`, `errors`/
        # `endpoint_error`/`circuit_broken` carried verbatim) plus the two fields that are THIS
        # tool's own: `relative_path` (a skill-relative virtual path like `references/x.md` — never
        # `_looks_like_a_path`-shaped, since it never starts with `/`/`~/`/`./`/`../`, so `_label`
        # passes it through) and `kind` (`"reference"`/`"script"`, a different vocabulary from the
        # artifact `kind` the other two drafting tools carry).
        e.update(
            label="draft skill extra file",
            artifact_id=_label(p.get("artifact_id")),
            relative_path=_label(p.get("relative_path")),
            kind=_label(p.get("kind")),
            draft_chars=_chars(p.get("draft")),
            errors=_texts(p.get("errors")),
            endpoint_error=_label(p.get("endpoint_error")),
            circuit_broken=bool(p.get("circuit_broken")),
        )
    else:
        # An UNRECOGNIZED tool contributes NO payload fields — not a generic scalar sweep, not
        # `_preview(args)`. The tool set is closed (invariant 1), so this branch means a trace from a
        # different build or a corrupted one, and neither justifies auto-surfacing unvetted keys.
        # `unrecognized` lets the UI say so out loud instead of rendering a mystery empty row.
        e.update(label=_label(tool) or "tool", unrecognized=True)
    return e


def _sub_entry(p: dict, gap: float | None) -> dict:
    """One `sub_call` — a recursive sub-LM escalation the root planner issued.

    `timeline` mixes `tool_call` AND `sub_call`, so dropping this would silently lose every
    escalation from the drawer. The siblings hardcode their own product's name for it
    (`"analyst"` / `"lifeline"`); this project already named the event `distill.sub_lm.call` in
    `mapper.py`, so the entry is named for THIS project and the two agree. `input`/`output` are
    carried in full (bounded by `_preview`) — exactly the pair `mapper.to_event` already streams for
    the same event, and the same class of content as a turn's own reasoning.
    """
    return {
        "entry_kind": "sub_lm",
        "label": "sub-LM",
        "model": _label(p.get("name") or p.get("model")),
        "duration_s": gap,
        "input": _preview(p.get("input")),
        "output": _preview(p.get("processed") or p.get("raw")),
        "error": _preview(p.get("error")),
    }


def _project_label(raw: Any) -> str | None:
    """`run_start.meta`'s `project_dir` is a REAL absolute path in the operator's home — never
    surfaced. Its basename answers the only question the drawer needs ("which project was this run
    distilling?") without identifying the machine."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    return PurePath(raw).name or None


def _initial(meta: object) -> dict:
    """The run's INITIAL state, bespoke to this project.

    diff-sentry's shape does not transfer: run against a ctx-distillery trace it produces six dead
    nulls (`change`, `source`, `instructions`, `baseline_indicators`, `emit_on`). This project's
    `run_start.meta` is `{transcripts, memory_artifacts, rubric}` (from `session.run_distillation`)
    plus `{project_dir, planner, drafter, interpreter, max_iterations, max_llm_calls}` (from
    `cli._cmd_distill`). There is NO `source`/`instructions` analogue — the transcripts are never in
    the trace, only COUNTED, by design — and inventing one would be fabrication.

    `interpreter` is surfaced because it is always `pyodide` and that is the point: `CLAUDE.md`
    invariant 1's sandbox pin is enforced in code (`task._forced_config`), and this is where a
    reviewer can see, per run, that it held. Confirmed `"pyodide"` on the real live run.
    """
    # `meta` is typed `object`: a trace can carry a non-dict there, and it must degrade to an
    # empty Init panel rather than 500 the endpoint (CLAUDE.md invariant 10).
    if not isinstance(meta, dict):
        meta = {}
    rubric = meta.get("rubric")
    return {
        "project": _project_label(meta.get("project_dir")),
        "transcripts": _as_int(meta.get("transcripts")),
        # WHAT those transcripts were, when the trace says (`mapper.transcript_composition` —
        # absent or malformed degrades to None, never to zero). `transcripts` alone cannot
        # distinguish one session from one session plus 42 subagents.
        **transcript_composition(meta),
        "memory_artifacts": _as_int(meta.get("memory_artifacts")),
        "models": {"planner": _label(meta.get("planner")), "drafter": _label(meta.get("drafter"))},
        "interpreter": _label(meta.get("interpreter")),
        "rubric_criteria": len(rubric) if isinstance(rubric, list) else None,
        "max_iterations": _as_int(meta.get("max_iterations")),
        "max_llm_calls": _as_int(meta.get("max_llm_calls")),
    }


def build_iterations(events: list[dict]) -> dict:
    """Decompose a run's trace into the Trajectory data. Returns
    `{started_at, total_s, timing_note, per_turn_timing, initial:{…}, iterations:[…], timeline:[…]}`
    — the siblings' envelope verbatim, with bespoke internals (see the module docstring).

    TWO views:
    - `iterations` — the planner's REPL turns (reasoning + code + its output), in turn order. CONTENT
      is always reliable; each turn's `output` already contains its tools' results inline. Per-turn
      timing (`rel_s`/`duration_s`) is attached only WHEN the trace carries live `main_step` ts
      (rlm-kit backfills them as each turn is parsed → `per_turn_timing=True`). A trace whose turns
      all landed at one instant — an old finalize-flushed trace, OR any run fast enough not to span a
      second, which is EVERY offline scripted-harness trace this workspace's tests produce — sets
      `per_turn_timing=False`, and we skip per-turn durations rather than fake them.
    - `timeline` — the `tool_call`/`sub_call` events, ALWAYS recorded LIVE with real `ts`, and always
      FULLY populated regardless of `per_turn_timing`. Each entry carries `seq`, `rel_s` (since run
      start) and `duration_s` (the gap since the previous live event — planner-think + tool-exec, not
      tool-exec alone). This is the accurate "where did the time go" signal either way, and the only
      view that survives a non-live-stamped trace intact.

    `turn_index` is the OPTIONAL link between the two, present on a timeline entry only when
    `per_turn_timing` is true. Never make the timeline's rendering depend on it.
    """
    evs = sorted(events, key=_step_key)
    meta: dict = {}
    ts0: float | None = None
    for e in evs:
        if e.get("type") == "run_start":
            payload = e.get("payload") or {}
            meta = payload.get("meta") or {}
            ts0 = e.get("ts")
            break
    ts_end: float | None = None
    for e in reversed(evs):
        if e.get("type") in ("run_end", "result", "final"):
            ts_end = e.get("ts")
            break

    iterations: list[dict] = []
    for e in evs:
        if e.get("type") == "main_step":
            p = e.get("payload") or {}
            iterations.append({
                "turn": p.get("turn"),
                "reasoning": _preview(p.get("reasoning")),
                "code": _preview(p.get("code")),
                "output": _preview(p.get("output")),
                "_ts": e.get("ts"),
            })
    iterations.sort(key=lambda it: it["turn"] if isinstance(it["turn"], int) else 1 << 30)
    # Per-turn timing is available IFF the trace carries live main_step ts. A >1s span over >=2 turns
    # can only be live (a real LM turn is seconds+). MEASURED: 20.4s on a real run -> True; 0.0019s on
    # the offline ScriptedInterpreter + scripted_lm harness -> False. Both poles are real and both are
    # covered by tests; the fallback is the COMMON case in this workspace, not an edge case.
    step_ts = [it["_ts"] for it in iterations if isinstance(it["_ts"], (int, float))]
    per_turn = len(step_ts) >= 2 and (max(step_ts) - min(step_ts)) > 1.0
    for i, it in enumerate(iterations):
        it["index"] = i
        if per_turn and isinstance(it["_ts"], (int, float)):
            it["rel_s"] = round(it["_ts"] - ts0, 3) if ts0 is not None else None
            nxt = iterations[i + 1]["_ts"] if i + 1 < len(iterations) else ts_end   # last turn → run end
            it["duration_s"] = round(nxt - it["_ts"], 3) if isinstance(nxt, (int, float)) else None
        it.pop("_ts", None)

    timeline: list[dict] = []
    prev = ts0
    for e in evs:
        t = e.get("type")
        ts = e.get("ts")
        if t not in ("tool_call", "sub_call") or ts is None:
            continue
        p = e.get("payload") or {}
        entry = _tool_entry(p, _gap(ts, prev)) if t == "tool_call" else _sub_entry(p, _gap(ts, prev))
        entry["seq"] = len(timeline)
        entry["rel_s"] = round(ts - ts0, 3) if ts0 is not None else None
        timeline.append(entry)
        prev = ts

    # Map each tool/sub-LM call to the TURN whose code produced it — ONLY when per-turn timing is live
    # (else the main_step ts cluster at one instant and the mapping is meaningless). A turn's code runs
    # AFTER its parse (the main_step rel_s) and before the next turn's, so a call belongs to the turn
    # with the greatest main_step rel_s <= the call's rel_s. `turn_index` indexes `iterations`. Absent
    # on every non-live-stamped trace — see the docstring; the timeline above is complete without it.
    if per_turn:
        marks = sorted((it["rel_s"], it["index"]) for it in iterations
                       if isinstance(it.get("rel_s"), (int, float)))
        for entry in timeline:
            r = entry.get("rel_s")
            if r is None or not marks:
                continue
            assigned = marks[0][1]
            for mrel, midx in marks:
                if mrel <= r:
                    assigned = midx
                else:
                    break
            entry["turn_index"] = assigned

    total = round(ts_end - ts0, 3) if (ts_end is not None and ts0 is not None) else None
    note = ("Per-turn timing is live — captured as each turn was parsed; the tool timeline shows where "
            "time went within the turns."
            if per_turn else
            "Per-turn timing isn't available for this trace (turns weren't live-stamped, or the run was "
            "too short to span); the timeline carries the live tool/sub-LM calls — where time went.")
    return {"started_at": ts0, "total_s": total, "timing_note": note, "per_turn_timing": per_turn,
            "initial": _initial(meta), "iterations": iterations, "timeline": timeline}
