"""One-shot driver: ingest a harness once, run one `DistillSession`, assemble the result.

This project proposes ONE plan per run — there is no "adopt the best of N" search loop (nothing here
has a reward signal to rank candidates by). So the driver is deliberately linear: ingest once,
redact once, run once, assemble once.

`assemble` — the read side of `CLAUDE.md` invariant (2) — used to be DEFINED here, alongside
`AssembledPlan` / `AssembledCandidate` / `PROMOTION_ACTIONS`. They now live in the dspy-free
`schema.py` and are RE-EXPORTED below, so `from ctx_distillery.session import assemble` (and every
other historical import of those names) resolves unchanged. The reason is measured, not aesthetic:
this module imports `task.py`, which does `from rlm_kit import RLMTask`, so `eval/` and `studio/` —
which only ever REPLAY a finished trace — were paying for dspy purely to reach a pure function over
`(events, plan)`. See `schema.py`'s docstring for the numbers and the full argument.

What stayed HERE is what genuinely needs the LM stack or the harness: `run_distillation_artifacts`
and its `run_distillation` wrapper (they construct a `DistillSession`) and `render_memory_index`
(it renders `ArtifactRef`s for THIS task's
`memory_index: str` signature input — prompt-side presentation for one task, not part of the plan's
shape, so it would be a bad fit for a shapes module).

Nothing in this module writes or applies anything. The returned `AssembledPlan` is inert; applying it
is a separate, human-gated step outside the RLM trajectory entirely.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from rlm_kit.trace import TraceRecorder

from .adapters.base import ArtifactRef, HarnessAdapter
from .redact import redact_transcript
from .rubric import default_rubric, rubric_to_meta
from .schema import PROMOTION_ACTIONS, AssembledCandidate, AssembledPlan, assemble
from .task import PLANNER_PROMPT_VERSION, DistillSession
from .trace_io import load_trace

#: Re-exported for back-compat — the assemble-on-read shapes are DEFINED in `schema.py` (dspy-free)
#: and listed here so `from .session import assemble` / `AssembledPlan` / `AssembledCandidate` /
#: `PROMOTION_ACTIONS` keep resolving, in this repo and in both workspace members.
__all__ = [
    "PROMOTION_ACTIONS",
    "AssembledCandidate",
    "AssembledPlan",
    "DistillArtifacts",
    "assemble",
    "render_memory_index",
    "run_distillation",
    "run_distillation_artifacts",
]


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


@dataclass(frozen=True)
class DistillArtifacts:
    """Everything ONE distillation run produced — the plan, and what it was actually drawn from.

    The plan alone is not enough for anything that has to grade or replay a run. Three of these
    fields are otherwise UNRECOVERABLE from outside `run_distillation_artifacts`' own frame:

    * **`transcripts` are the REDACTED texts the run actually saw** (`redact` applied once, per
      CLAUDE.md invariant 3). A caller cannot reconstruct them by re-`ingest()`ing and
      re-`redact()`ing: `HarnessAdapter` promises nothing about a second `ingest()` returning the
      same bytes, and a caller-supplied `redact=` may not even be reachable from where the grading
      happens. Reading them back out of the TRACE is permanently ruled out on this project's own
      record — `tools/transcript_reader.py` records offset/length/total_length and never the text
      itself, "that is the audit point" — so a trace-sourced substitute would be EMPTY, not merely
      lossier.
    * **`run_id`** closes a real blind spot: a caller passing `run_id=None` could previously never
      learn which id was generated for it, and the id is the key every downstream reader
      (`load_trace(path, run_id=...)`, `eval/`'s task pairing, `studio/`'s replay) is keyed on.
    * **`events`** is the just-recorded trace, already filtered to this run — so a caller that wants
      to re-assemble, score, or export does not have to re-open the file and guess the filter.

    **These fields deliberately do NOT live on `AssembledPlan`.** That shape is defined in the
    dspy-free `schema.py`, is shared with `eval/` and `studio/`, and `render.plan_as_dict` is a
    plain `dataclasses.asdict(plan)` — putting full transcript bodies on it would dump every
    redacted transcript into `ctx-distillery show --json`.
    """

    plan: AssembledPlan
    events: list[dict]
    run_id: str
    trace_path: str
    transcripts: list[str]
    memory_index: list[ArtifactRef]


async def run_distillation_artifacts(
    adapter: HarnessAdapter,
    chat_fn: Any,
    trace_path: str,
    *,
    redact: Callable[[str], str] = redact_transcript,
    run_id: str | None = None,
    meta: dict | None = None,
    **kw: Any,
) -> DistillArtifacts:
    """Ingest once, redact once, run one `DistillSession`, assemble — and return ALL of it.

    The full-fidelity driver. `run_distillation` below is a one-line wrapper over it that keeps the
    historical `-> AssembledPlan` contract; this is the entry point for a caller that also needs the
    redacted transcripts, the resolved run id, or the recorded events (see `DistillArtifacts`).

    * `adapter.ingest()` is called EXACTLY ONCE. Its `memory_index` is the immutable snapshot every
      tool closes over (so the read allowlist can't shift mid-run), and its `transcripts` are
      redacted IMMEDIATELY into the one list threaded into both the task's constructor and
      `.arun()` — which is what makes "nothing unredacted ever reaches the model" a property of the
      code rather than a claim (CLAUDE.md invariant 3).
    * Extra `**kw` go to `DistillSession` (e.g. `config=`, `interpreter=` for an offline test).
    * Writes/applies nothing: the returned artifacts are inert until a human acts on them. This
      module is inside `tests/test_no_write_capability.py`'s mutation scan and returning more data
      changes nothing about that — the only file this function's frame touches is the trace, and
      `TraceRecorder` (rlm-kit's) owns that, exactly as before.
    """
    raw = adapter.ingest()
    redacted_transcripts = [redact(t) for t in raw.transcripts]
    # Skipped entirely when empty, not called unconditionally: `tests/test_session.py`'s
    # `test_a_custom_redactor_is_honoured` asserts the INJECTED `redact` callable is called with
    # EXACTLY the transcript texts — an unconditional `redact("")` on the common empty default would
    # append a spurious call that test has nothing to do with. Redacting "" is harmless either way;
    # this is about what the injected callable actually sees, not about safety.
    redacted_instructions = redact(raw.project_instructions) if raw.project_instructions else ""
    memory_index = list(raw.memory_index)

    task = DistillSession(
        memory_index=memory_index,
        chat_fn=chat_fn,
        transcripts=redacted_transcripts,
        **kw,
    )
    rid = run_id or uuid.uuid4().hex[:12]
    run_meta = {
        "transcripts": len(redacted_transcripts),
        "memory_artifacts": len(memory_index),
        # Honest provenance, always known exactly (never a None-vs-0 ambiguity the way
        # `transcript_index` has): 0 truthfully means "no CLAUDE.md was found, or it was empty".
        "project_instructions_chars": len(redacted_instructions),
    }
    if raw.transcript_ids:
        # CONDITIONAL, and that is the whole point: an unconditional stamp makes `[]` mean two
        # different things — "this adapter reported no identities" and "this run had no
        # transcripts". Every non-`ClaudeCodeAdapter` run (this repo's tests, `eval/`'s fakes, any
        # future harness) would write `[]` beside a `meta["transcripts"]` of 3, and a consumer told
        # to render "None when absent" would faithfully render `sessions=0 subagents=0`.
        # Present-and-empty is not absent.
        run_meta["transcript_index"] = [asdict(t) for t in raw.transcript_ids]
    run_meta["rubric"] = rubric_to_meta(default_rubric())
    # Stamped by the DRIVER, not by `cli._cmd_distill`: this is the one place every caller passes
    # through (`cli`, `eval/cli._run_one`, any script), and each of those builds its own `meta`
    # dict. Without it two traces either side of an instruction change are indistinguishable on the
    # axis that dominates plan quality — the same contract `eval/`'s `PROMPT_VERSION` has.
    run_meta["planner_prompt_version"] = PLANNER_PROMPT_VERSION
    run_meta.update(meta or {})
    # Stamped LAST, deliberately AFTER the caller's own `meta` is merged in: this key is an
    # authoritative provenance stamp `apply.py` gates a write-side refusal on, unlike the diagnostic
    # counts above — a caller's `meta` dict incidentally carrying its own "harness" key must never
    # silently clobber the real value.
    run_meta["harness"] = adapter.harness_name
    with TraceRecorder(trace_path, run_id=rid, meta=run_meta):
        plan = await task.arun(
            transcripts=redacted_transcripts,
            memory_index=render_memory_index(memory_index),
            project_instructions=redacted_instructions,
        )
    # `load_trace`, not `load_events`: this trace is well-formed by construction (the recorder just
    # wrote it), so this is consistency rather than a live bug — but it means no module in the
    # workspace passes `run_id=` into `load_events`'s own unguarded filter any more (`trace_io.py`).
    events = load_trace(trace_path, run_id=rid)
    return DistillArtifacts(
        plan=assemble(events, plan),
        events=events,
        run_id=rid,
        trace_path=trace_path,
        transcripts=redacted_transcripts,
        memory_index=memory_index,
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

    UNCHANGED in signature and in return type — this is the historical driver, and every existing
    caller (`cli._cmd_distill`, the tests, any downstream script) keeps working untouched. It is now
    a thin wrapper over `run_distillation_artifacts`, which returns the same plan plus the three
    things that used to die as locals in this frame (see `DistillArtifacts`). Widening this
    function's return type instead would have been a public API break for a need only `eval/`'s
    `run` has; ADDING the wider function costs nobody anything.
    """
    artifacts = await run_distillation_artifacts(
        adapter, chat_fn, trace_path, redact=redact, run_id=run_id, meta=meta, **kw
    )
    return artifacts.plan
