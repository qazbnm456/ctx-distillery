"""`ctx-distillery-eval score <trace_glob> <transcript_path> [<transcript_path> ...]`.

**Judgment call, stated explicitly** (`docs/IMPL_PLAN.md`'s resolved decision requires the transcript
path(s) to be a mandatory second input alongside the trace path, but does not specify how transcripts
multiplex across a BATCH of runs matched by a glob — a real gap the plan itself flags as something to
resolve "before writing judge.py, not after"): **one CLI invocation's transcript path(s) apply to
EVERY run `trace_glob` matches in that invocation.** This fits the shape this eval member is actually
built for — scoring several runs (retries, ablations, rubric variants) over the SAME transcript set —
and keeps the contract simple and literal about what the plan requires: transcripts are a required,
positional, non-fallback input, never optional and never sourced from the trace. Scoring a batch
where each matched run drew from a DIFFERENT transcript set is explicitly out of scope for this pass;
invoke `score` once per transcript set in that case. A "known simplification, because X" call, per
this project's established convention — not a silent guess.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rlm_kit.trace import load_events

from .schema import EvalReport
from .score import aggregate, score_run
from .taskset import collect_tasks


def _read_transcripts(paths: list[str]) -> list[str]:
    return [Path(p).read_text(encoding="utf-8") for p in paths]


def render_scorecard(report: EvalReport) -> str:
    """A terminal scorecard: one line per scored run, then the per-category means (never a composite)."""
    header = "run_id".ljust(24) + "  TF    TA    TG    PA   notes"
    lines = [header]
    for row in report.rows:
        s = row.score
        lines.append(
            f"{row.run_id[:24].ljust(24)}  {s.TF:4.1f}  {s.TA:4.1f}  {s.TG:4.1f}  {s.PA:4.1f}  {s.notes}"
        )
    if report.means:
        m = report.means
        lines.append("-" * len(header))
        lines.append(
            "mean".ljust(24) + f"  {m['TF']:4.1f}  {m['TA']:4.1f}  {m['TG']:4.1f}  {m['PA']:4.1f}"
        )
    else:
        lines.append("(no runs scored)")
    return "\n".join(lines)


def _score_command(args: argparse.Namespace) -> int:
    tasks = collect_tasks(args.trace_glob)
    if not tasks:
        print(f"no runs found matching {args.trace_glob!r}", file=sys.stderr)
        return 1
    transcript_texts = _read_transcripts(args.transcript_path)
    rows = [
        score_run(
            task.run_id,
            task.trace_path,
            load_events(task.trace_path, run_id=task.run_id),
            transcript_texts,
        )
        for task in tasks
    ]
    print(render_scorecard(aggregate(rows)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctx-distillery-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    score_parser = sub.add_parser(
        "score", help="score one or more distillation traces against their transcript(s)"
    )
    score_parser.add_argument("trace_glob", help="glob pattern matching one or more trace JSONL files")
    score_parser.add_argument(
        "transcript_path",
        nargs="+",
        help=(
            "one or more transcript file paths — MANDATORY, applied to every run matched by "
            "trace_glob in this invocation (see this module's docstring)"
        ),
    )
    score_parser.set_defaults(func=_score_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
