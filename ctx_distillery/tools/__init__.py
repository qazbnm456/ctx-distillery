"""The `DistillSession` tool set — READ-ONLY, and closed.

Per `CLAUDE.md` invariant (1) the six tools here (`list_memory_files`, `read_memory_file`,
`read_transcript_chunk`, `draft_memory_file`, `draft_skill_file`, `draft_skill_extra_file`) are the
complete set, not a starting point to extend with a writer. `draft_skill_extra_file` widened the
enumeration from five to six — it drafts a skill's supplementary `references/`/`scripts/` files, and
it is read-only in exactly the same sense the other five are: it returns text and records it to the
trace, and touches no path. Every factory follows rlm-harness's base/wrap split: the generic mechanics
come from the kit (`make_model_tool`, `record_tool_call`), and only this project's names, validators,
wording, and tracing live here.

Every tool exposes EXPLICIT named params (no `*args`/`**kwargs`, no required-after-defaulted) so
dspy's in-sandbox proxy is callable — enforced by `rlm_harness.testing.assert_repl_safe` in the tests.
"""

from .drafting import (
    FormatCheck,
    make_draft_memory_file_tool,
    make_draft_skill_extra_file_tool,
    make_draft_skill_file_tool,
)
from .memory_reader import make_list_memory_files_tool, make_read_memory_file_tool
from .transcript_reader import make_read_transcript_chunk_tool

__all__ = [
    "FormatCheck",
    "make_draft_memory_file_tool",
    "make_draft_skill_extra_file_tool",
    "make_draft_skill_file_tool",
    "make_list_memory_files_tool",
    "make_read_memory_file_tool",
    "make_read_transcript_chunk_tool",
]
