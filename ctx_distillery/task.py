"""The DistillSession RLM task — an honest skeleton, not a working implementation.

This is the FIRST scaffolding pass for ctx-distillery. `DistillSession` declares the shape
of the task (signature, output_model, instructions) as designed in docs/DESIGN.md, but wires
NO tools yet — see the TODO below. Do not treat this as functional; it is here so the
project's structure, dependency on rlm-kit, and output contract are pinned down before any
tool is implemented.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from rlm_kit import RLMTask

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
            "candidate flagged as an overlap/conflict. Never the drafted artifact body itself."
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

    # TODO(ctx-distillery): wire the read-only tools designed in docs/DESIGN.md. None of
    # these exist yet — this is the first scaffolding pass, not a working task. Planned tools,
    # all read-only (see CLAUDE.md invariant 1 and docs/DESIGN.md's tool enumeration):
    #
    #   - list_memory_files()            -> structured index of existing memory/skill files
    #   - read_memory_file(path)         -> full text of one existing memory/skill file
    #   - read_transcript_chunk(...)      -> paginated read over a (potentially huge) transcript
    #   - draft_memory_file(...)          -> LM-backed drafting tool (make_model_tool), TEXT ONLY,
    #                                        returns a candidate memory file body + an artifact_id
    #   - draft_skill_file(...)           -> LM-backed drafting tool (make_model_tool), TEXT ONLY,
    #                                        returns a candidate SKILL.md body + an artifact_id
    #
    # Each will be sourced from a HarnessAdapter (see ctx_distillery/adapters/base.py) so the
    # planner core stays harness-agnostic. No tool here may ever write or delete a file — that
    # would break the structural guarantee this whole project exists to uphold.
    tools: list = []
