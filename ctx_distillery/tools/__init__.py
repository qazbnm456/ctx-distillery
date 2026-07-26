"""The `DistillSession` tool set — READ-ONLY, and closed.

Per `CLAUDE.md` invariant (1) the five tools here (`list_memory_files`, `read_memory_file`,
`read_transcript_chunk`, `draft_memory_file`, `draft_skill_file`) are the complete set, not a
starting point to extend with a writer. Every factory follows rlm-kit's base/wrap split: the generic
mechanics come from the kit (`make_model_tool`, `record_tool_call`), and only this project's names,
validators, wording, and tracing live here.

Every tool exposes EXPLICIT named params (no `*args`/`**kwargs`, no required-after-defaulted) so
dspy's in-sandbox proxy is callable — enforced by `rlm_kit.testing.assert_repl_safe` in the tests.
"""

from .drafting import (
    FormatCheck,
    make_draft_memory_file_tool,
    make_draft_skill_file_tool,
)
from .memory_reader import make_list_memory_files_tool, make_read_memory_file_tool
from .transcript_reader import make_read_transcript_chunk_tool

__all__ = [
    "FormatCheck",
    "make_draft_memory_file_tool",
    "make_draft_skill_file_tool",
    "make_list_memory_files_tool",
    "make_read_memory_file_tool",
    "make_read_transcript_chunk_tool",
]
