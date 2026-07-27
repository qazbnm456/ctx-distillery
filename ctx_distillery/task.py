"""The DistillSession RLM task — declaration plus the runtime tool wiring.

`DistillSession` declares the shape of the task (signature, output_model, instructions) as
designed in docs/DESIGN.md, and its `__init__` wires the five READ-ONLY tools from an immutable
memory-index snapshot plus an already-redacted transcript list. `session.run_distillation` is the
driver that produces both and assembles the result; nothing here reads a harness directly.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field
from rlm_kit import RLMTask
from rlm_kit.config import RLMConfig
from rlm_kit.runtime import get_config
from rlm_kit.tools.model import ChatFn

from .adapters.base import ArtifactRef
from .tools.drafting import make_draft_memory_file_tool, make_draft_skill_file_tool
from .tools.memory_reader import make_list_memory_files_tool, make_read_memory_file_tool
from .tools.transcript_reader import make_read_transcript_chunk_tool

#: The sandbox this task ALWAYS runs in — see `_forced_config` and CLAUDE.md invariant (1).
PINNED_INTERPRETER = "pyodide"

# --- Output contract -----------------------------------------------------------------------
#
# Per docs/DESIGN.md ("Judgement-only SUBMIT ... output_model shape"): the plan carries only
# {action, artifact_id, key_fields} per candidate — never the drafted memory/skill content
# itself. The actual markdown+frontmatter text for a promotion is produced by a separate
# drafting tool (`draft_memory_file` / `draft_skill_file`, both TODO below) and re-sourced on
# READ by matching the tool-call event whose `artifact_id` matches this candidate's — never
# from the plan's own claim about what it wrote. This keeps the judgement (what to do) and the
# authored content (the tool-call event) from ever drifting apart.

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
            "ctx_distillery/apply.py — a prune with no matching target_path is refused)."
        ),
    )


class DistillPlan(BaseModel):
    """The full proposed plan for one distillation run. Inert until a human applies it."""

    candidates: list[DistillCandidate] = Field(
        default_factory=list,
        description="One entry per transcript segment / memory file judged by this run.",
    )


_INSTRUCTIONS = """\
You are a judgement-only distillation planner for AI coding-agent memory. You are given one or
more session transcripts and the current memory/skill index as REPL variables. Your job is to
decide, per candidate, one of: keep, prune, promote_to_memory, promote_to_skill.

You NEVER write or delete anything yourself — you have no tool that can. Every output you
produce is a PROPOSED plan a human must review and apply explicitly; treat "safe to prune" as a
judgement call you can get wrong, not a certainty.

Distinguish promotions carefully: a fact about the user or the project (a decision, a
constraint, a piece of state) is a MEMORY candidate; a reusable how-to/procedure discovered
during a session is a SKILL candidate. These are two distinct target shapes, not one bucket.

When multiple transcripts independently confirm the same thing, say so explicitly rather than
silently deduplicating. When two transcripts disagree, flag it as a conflict for human review
rather than picking a side.

For a `prune` candidate you MUST set `key_fields["target_path"]` to the exact `path` of the
existing artifact you are proposing to prune, copied verbatim from `list_memory_files()`. That is
the only way a human's apply step can tell WHICH file a prune refers to; a prune whose
`target_path` is missing, altered, or not one of the listed paths is refused rather than guessed
at, and the harness's own memory index (`kind: "index"`) is never a valid prune target.

See docs/DESIGN.md for the full design and acceptance criteria this task is built against.
"""


class DistillSession(RLMTask):
    """Propose a distillation plan over one or more transcripts + the memory/skill index.

    Judgement-only: this task's authority stops at producing a `DistillPlan`. It never
    mutates or deletes a transcript or memory/skill file — see CLAUDE.md invariant (1) and
    docs/DESIGN.md's "Structural no-mutation guarantee."
    """

    signature = "transcripts: list[str], memory_index: str -> plan: DistillPlan"
    output_field = "plan"
    output_model = DistillPlan
    instructions = _INSTRUCTIONS

    # Empty at class level and REASSIGNED per instance in __init__ (a tool closes over runtime-injected
    # state — the index snapshot, the transcripts, the chat_fn — which cannot exist on the class).
    tools: ClassVar[list[Callable[..., Any]]] = []

    def __init__(
        self,
        *,
        memory_index: Sequence[ArtifactRef],
        chat_fn: ChatFn,
        transcripts: Sequence[str],
        config: RLMConfig | None = None,
        **kw: Any,
    ) -> None:
        """Wire the five read-only tools from an index SNAPSHOT + already-redacted transcripts.

        `memory_index` is the `list[ArtifactRef]` from ONE `adapter.ingest()` call — an immutable
        snapshot, never a live adapter, so the `read_memory_file` allowlist cannot shift mid-run.
        `transcripts` MUST already be redacted (`session.run_distillation` does this immediately
        after `ingest()`); it is the same list passed to `.arun(transcripts=...)`, deliberately
        threaded twice — once to build `read_transcript_chunk`'s closure, once to bind the signature
        input — so there is exactly one copy of the text in play.
        """
        self.tools = [
            make_list_memory_files_tool(memory_index),
            make_read_memory_file_tool(memory_index),
            # SAME list `.arun(transcripts=...)` binds — see the docstring above.
            make_read_transcript_chunk_tool(transcripts),
            make_draft_memory_file_tool(chat_fn, memory_index),
            make_draft_skill_file_tool(chat_fn, memory_index),
        ]
        super().__init__(config=_forced_config(config), **kw)


def _forced_config(config: RLMConfig | None) -> RLMConfig:
    """Return `config` (or the configured default) with `interpreter` forced to `pyodide`.

    CLAUDE.md invariant (1) requires the sandbox pin be STATED IN THE TASK, not left to
    `.env.example` or to whatever the caller happened to configure: the "no mutation" guarantee
    depends on never routing through a writable-mount `container` (or, worse, `local`) config. So the
    pin is enforced HERE, in code — `dataclasses.replace` on the frozen `RLMConfig` — and a caller
    passing `interpreter="local"` gets `pyodide` anyway rather than a silently weakened sandbox.

    (An explicit interpreter OBJECT via `RLMTask(interpreter=...)` still bypasses this, exactly as it
    bypasses rlm-kit's own sandbox guard: that is the documented TEST seam — `ScriptedInterpreter` —
    where the caller supplies and owns the double. It is not a config path.)
    """
    base = config if config is not None else get_config()
    if base.interpreter == PINNED_INTERPRETER:
        return base
    return dataclasses.replace(base, interpreter=PINNED_INTERPRETER)
