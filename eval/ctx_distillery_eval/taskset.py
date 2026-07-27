"""`collect_tasks` — enumerate `{run_id, trace_path}` pairs from a glob of distillation trace files.

Deliberately narrow: this module's only job is enumeration, never scoring (that's `score.py`) and
never sourcing transcripts (a trace file never carries the raw transcript text it was drawn from —
see `judge.py`'s module docstring for why). A trace file normally carries exactly one run
(`ctx_distillery.session.run_distillation`'s own docstring: "ingest once, redact once, run once,
assemble once"), but a file is read defensively as potentially carrying more than one `run_id`
(e.g. a hand-assembled or concatenated trace file) rather than assuming exactly one.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass

from rlm_kit.trace import load_events


@dataclass(frozen=True)
class Task:
    """One run to score: its id, and the trace file it was recorded into."""

    run_id: str
    trace_path: str


def collect_tasks(trace_glob: str) -> list[Task]:
    """Expand `trace_glob`, and return one `Task` per distinct `run_id` found in each matched file.

    Sorted by path then by run_id, so a batch CLI invocation is deterministic across runs on the
    same filesystem. A matched file with no recorded events (or none carrying a `run_id`) contributes
    no tasks rather than raising — an empty/corrupt trace file degrades the batch, not the whole run.
    """
    tasks: list[Task] = []
    for path in sorted(glob.glob(trace_glob)):
        events = load_events(path)
        run_ids = sorted({e.get("run_id") for e in events if e.get("run_id")})
        for run_id in run_ids:
            tasks.append(Task(run_id=run_id, trace_path=path))
    return tasks
