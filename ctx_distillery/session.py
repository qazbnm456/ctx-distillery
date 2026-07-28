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

What stayed HERE is what genuinely needs the LM stack or the harness: `run_distillation` (it
constructs a `DistillSession`) and `render_memory_index` (it renders `ArtifactRef`s for THIS task's
`memory_index: str` signature input — prompt-side presentation for one task, not part of the plan's
shape, so it would be a bad fit for a shapes module).

Nothing in this module writes or applies anything. The returned `AssembledPlan` is inert; applying it
is a separate, human-gated step outside the RLM trajectory entirely.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

from rlm_kit.trace import TraceRecorder

from .adapters.base import ArtifactRef, HarnessAdapter
from .redact import redact_transcript
from .rubric import default_rubric, rubric_to_meta
from .schema import PROMOTION_ACTIONS, AssembledCandidate, AssembledPlan, assemble
from .task import DistillSession
from .trace_io import load_trace

#: Re-exported for back-compat — the assemble-on-read shapes are DEFINED in `schema.py` (dspy-free)
#: and listed here so `from .session import assemble` / `AssembledPlan` / `AssembledCandidate` /
#: `PROMOTION_ACTIONS` keep resolving, in this repo and in both workspace members.
__all__ = [
    "PROMOTION_ACTIONS",
    "AssembledCandidate",
    "AssembledPlan",
    "assemble",
    "render_memory_index",
    "run_distillation",
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
