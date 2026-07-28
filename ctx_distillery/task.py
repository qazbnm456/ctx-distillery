"""The DistillSession RLM task — declaration plus the runtime tool wiring.

`DistillSession` declares the shape of the task (signature, output_model, instructions) as
CLAUDE.md's invariants require, and its `__init__` wires the five READ-ONLY tools from an immutable
memory-index snapshot plus an already-redacted transcript list. `session.run_distillation` is the
driver that produces both and assembles the result; nothing here reads a harness directly.

The SUBMIT shapes themselves (`DistillAction` / `DistillCandidate` / `DistillPlan`) used to be
DEFINED here, next to the `RLMTask`. They now live in the dspy-free `schema.py` and are RE-EXPORTED
below, so `from ctx_distillery.task import DistillPlan` keeps working exactly as before — see
`schema.py`'s docstring for the measurement that forced the split (importing `eval/`'s or `studio/`'s
entry point pulled dspy purely because the only route to these shapes ran through this module's
`from rlm_kit import RLMTask`). What stayed HERE is what genuinely needs dspy or is task-specific:
the task class, its instructions, and the `pyodide` pin, which CLAUDE.md invariant 1 requires be
stated in the task rather than delegated to config.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from rlm_kit import RLMTask
from rlm_kit.config import RLMConfig
from rlm_kit.runtime import get_config
from rlm_kit.tools.model import ChatFn

from .adapters.base import ArtifactRef
from .schema import DistillAction, DistillCandidate, DistillPlan
from .tools.drafting import make_draft_memory_file_tool, make_draft_skill_file_tool
from .tools.memory_reader import make_list_memory_files_tool, make_read_memory_file_tool
from .tools.transcript_reader import make_read_transcript_chunk_tool

#: Re-exported for back-compat — `DistillAction`/`DistillCandidate`/`DistillPlan` are DEFINED in
#: `schema.py` (dspy-free) and listed here so every historical `from .task import ...` call site,
#: in this repo and in both workspace members, resolves unchanged.
__all__ = [
    "PINNED_INTERPRETER",
    "PLANNER_PROMPT_VERSION",
    "DistillAction",
    "DistillCandidate",
    "DistillPlan",
    "DistillSession",
]

#: The sandbox this task ALWAYS runs in — see `_forced_config` and CLAUDE.md invariant (1).
PINNED_INTERPRETER = "pyodide"

#: Bump whenever `_INSTRUCTIONS` changes in a way that could move plan quality — the same contract
#: the eval member's `judge.py::PROMPT_VERSION` has for the JUDGE prompt, on the side that
#: had no counterpart at all. (Named without its package, deliberately: `eval/`'s boundary test
#: scans this package's source TEXT for that package's name, prose included.) Without it, two
#: traces either side of an instruction change are
#: indistinguishable on the axis that dominates plan quality; `session.run_distillation_artifacts`
#: stamps it into every run's `run_meta`, and `tests/test_task.py` pins the literal so the constant
#: cannot drift silently from the text it names.
#:
#: v1 -> v2: the subagent paragraphs below (findings ARE evidence / parent-and-subagent agreement is
#: an echo / contradiction is a signal), plus the index-line orientation paragraph.
PLANNER_PROMPT_VERSION = "ctxd-planner-v2"


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

Some runs include SUBAGENT transcripts alongside the session transcripts. When they do, every
entry begins with a short index line saying which it is: `[i] subagent <id> parent=... type=...
depth=...` versus `[i] session <id>`, followed by a `session=` line naming what it belongs to.

A subagent's FINDINGS are ordinary evidence and you should use them exactly as you would a
session's: what it read, what it measured, what it concluded, what it decided. Most of the real
work in a session is often inside its subagents, and ignoring them would distil the coordination
layer and skip the content.

What a subagent is NOT is an INDEPENDENT witness to its own parent. Its task came from that parent,
so parent-and-subagent agreement is an echo, not corroboration — never count it as "multiple
transcripts independently confirm". Two entries only corroborate each other when neither descends
from the other: compare their `session=` values, and for entries in the same session compare
`parent=`. Subagents that share a parent are siblings, not independent witnesses either.

A subagent CONTRADICTING its parent, on the other hand, is a strong signal and worth flagging as a
conflict — the derivation makes disagreement harder to produce, not easier.

When entries carry that index line, orient yourself first with
`print("\\n".join(t.split("\\n")[0] for t in transcripts))`, and give that scan its OWN REPL cell —
one cell's output is capped, so printing anything else beside it eats the same budget. If the
output comes back truncated, page it (`transcripts[:200]`, then `transcripts[200:400]`, ...).

For a `prune` candidate you MUST set `key_fields["target_path"]` to the exact `path` of the
existing artifact you are proposing to prune, copied verbatim from `list_memory_files()`. That is
the only way a human's apply step can tell WHICH file a prune refers to; a prune whose
`target_path` is missing, altered, or not one of the listed paths is refused rather than guessed
at, and the harness's own memory index (`kind: "index"`) is never a valid prune target.

For a `promote_to_skill` candidate you MUST set `key_fields["scope"]` to either "project" or
"global", and pass that SAME value as `draft_skill_file`'s `scope` argument. Decide it by what the
finding actually is:

* "project" — the knowledge is tied to THIS project's own tooling, layout, or conventions (its test
  command, its release checklist, the way ITS codebase is organized). It would be noise, or simply
  wrong, in another repository.
* "global" — the technique is genuinely portable and would help in any project (a debugging method,
  a way of using a tool, a review habit that has nothing to do with this codebase's specifics).

Skills at the two scopes live in two separate directories and are two separate namespaces: the same
name existing in the OTHER scope is not a collision. A promote_to_skill candidate whose `scope` is
missing or is not one of those two values is refused at apply time rather than guessed at, exactly
like a `prune` with no `target_path`.

See CLAUDE.md for the hard invariants this task is built against.
"""


class DistillSession(RLMTask):
    """Propose a distillation plan over one or more transcripts + the memory/skill index.

    Judgement-only: this task's authority stops at producing a `DistillPlan`. It never
    mutates or deletes a transcript or memory/skill file — see CLAUDE.md invariant (1), the
    structural no-mutation guarantee.
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
