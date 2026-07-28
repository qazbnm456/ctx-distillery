"""`ctx-distillery-studio` — a replay-only SSE server + zero-build web frontend for ctx-distillery.

Replays a finished `DistillSession` run's trace/v1 JSONL file: the replay feed (planner reasoning,
sub-LM escalations, evidence reads, drafting calls) via Server-Sent Events, and the assembled plan
— each promotion candidate's `draft` rendered next to its `action`/`key_fields` — via a plain GET.
No live-drive endpoint (v1 scope decision; `CLAUDE.md` invariant 10 and `studio/README.md`'s
"Scope: replay-only, v1" carry the argument). NOT because the preconditions are heavy — the CLI's
`_cmd_distill` already assembles them from the `CD_*` env — but because there is no cancel seam for
a multi-minute sandboxed episode, because the import-level `live`-extra valve is unavailable here
(replay itself needs `assemble`), and, strongest, because the live input would be a `project_dir`:
an unauthenticated parameter selecting whose entire Claude Code history gets shipped to a model.
Read-only of the trace file; never calls `ctx_distillery.apply.apply_plan`.
"""

from __future__ import annotations

__version__ = "0.1.0"
