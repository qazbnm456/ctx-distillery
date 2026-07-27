"""`ctx-distillery-studio` — a replay-only SSE server + zero-build web frontend for ctx-distillery.

Replays a finished `DistillSession` run's trace/v1 JSONL file: the live feed (planner reasoning,
sub-LM escalations, evidence reads, drafting calls) via Server-Sent Events, and the assembled plan
— each promotion candidate's `draft` rendered next to its `action`/`key_fields` — via a plain GET.
No live-drive endpoint (v1 scope decision; see `docs/DESIGN.md`'s Studio section): `run_distillation`
needs a caller-supplied harness adapter + model already wired, unlike a self-contained one-shot
driver a web request could reasonably own end-to-end. Read-only of the trace file; never calls
`ctx_distillery.apply.apply_plan`.
"""

from __future__ import annotations

__version__ = "0.1.0"
