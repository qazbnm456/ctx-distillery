"""`ctx-distillery-eval score <trace_glob> <transcript_path> [<transcript_path> ...]`.

**Judgment call, stated explicitly** (the implementation plan's resolved decision requires the transcript
path(s) to be a mandatory second input alongside the trace path, but does not specify how transcripts
multiplex across a BATCH of runs matched by a glob — a real gap the plan itself flagged as something to
resolve "before writing judge.py, not after"): **one CLI invocation's transcript path(s) apply to
EVERY run `trace_glob` matches in that invocation.** This fits the shape this eval member is actually
built for — scoring several runs (retries, ablations, rubric variants) over the SAME transcript set —
and keeps the contract simple and literal about what the plan requires: transcripts are a required,
positional, non-fallback input, never optional and never sourced from the trace. Scoring a batch
where each matched run drew from a DIFFERENT transcript set is explicitly out of scope for this pass;
invoke `score` once per transcript set in that case. A "known simplification, because X" call, per
this project's established convention — not a silent guess.

**The judge is LIVE iff `CDEVAL_MODEL` is set and `--stub` was not passed** (`_pick_judge`), matching
every sibling eval member's rule. With no `CDEVAL_MODEL` the deterministic `StubJudge` runs instead:
fully offline, zero credentials, zero network — the CI path and the default. `--stub` forces it even
when the environment is configured, which is what makes a live-configured shell still able to run the
offline suite. There is deliberately NO `run` subcommand (drive-then-score); see `eval/README.md`'s
"Deferred: `run` + a real taskset" for the three concrete blockers, none of which is polish.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ctx_distillery.trace_io import load_trace

from .judge import PROMPT_VERSION, EvalJudgeConfig, Judge, StubJudge, make_eval_judge
from .schema import EvalReport
from .score import aggregate, score_run
from .taskset import collect_tasks


def _read_transcripts(paths: list[str]) -> list[str]:
    """Read every transcript path, refusing an EMPTY (or whitespace-only) one.

    FIXED per adversarial review: `transcript_path` being a required CLI argument only enforces
    the "mandatory" design decision STRUCTURALLY (omitting the flag errors via argparse) — an
    empty file slipped straight through, ran to completion, and would silently ask a real judge
    (the stub judge ignores its inputs, masking this) to score a plan against nothing, exactly the
    failure mode "mandatory transcript" exists to prevent. Refuse it here instead, loudly.
    """
    texts = [Path(p).read_text(encoding="utf-8") for p in paths]
    empty = [p for p, text in zip(paths, texts) if not text.strip()]
    if empty:
        raise SystemExit(
            f"transcript path(s) {empty!r} are empty — a real judge would be scoring a plan "
            f"against nothing, which defeats the point of requiring a transcript at all"
        )
    return texts


def _pick_judge(force_stub: bool) -> tuple[Judge, str, str]:
    """The LIVE judge iff `CDEVAL_MODEL` is configured (and not `--stub`), else the offline stub.

    Returns `(judge, judge_model, prompt_version)` — the last two are pure PROVENANCE, recorded on
    the `EvalReport` so a scorecard states which judge produced it under which prompt version.

    The stub reports `prompt_version=""` on purpose, following the siblings: it never rendered
    `build_prompt` at all, so pinning `PROMPT_VERSION` to a stub scorecard would claim a provenance
    it does not have and make an offline run look comparable to a live one.

    Called ONCE per invocation, above the row loop, and that matters: `make_eval_judge`'s circuit
    breaker lives in a closure, so one judge per batch means a systematically off-schema judge stops
    burning calls after a few declines instead of paying for one per trace in the glob.
    """
    config = EvalJudgeConfig.from_env()
    if force_stub or not config.model:
        return StubJudge(), "stub", ""
    return make_eval_judge(config), config.model, PROMPT_VERSION


def render_scorecard(report: EvalReport) -> str:
    """A terminal scorecard: one line per run, the per-category means (never a composite), then the
    provenance footer.

    An UNSCORED row (the judge failed — endpoint error, off-schema output, circuit breaker) renders
    its four columns as `--` followed by `unscored: <reason>`, and is absent from the means. It is
    never rendered as a 0, and it is never silently dropped from the listing either: a batch where
    the judge died must LOOK like one, both per-row and in the `n=… (… unscored)` footer.
    """
    header = "run_id".ljust(24) + "  TF    TA    TG    PA   notes"
    lines = [header]
    for row in report.rows:
        if row.score is None:
            lines.append(
                f"{row.run_id[:24].ljust(24)}  {'--':>4}  {'--':>4}  {'--':>4}  {'--':>4}  "
                f"unscored: {row.unscored_reason}"
            )
            continue
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
    lines.append(
        f"n={report.n} ({report.n_unscored} unscored)  judge={report.judge_model or '?'}"
        + (f"  prompt={report.prompt_version}" if report.prompt_version else "")
    )
    return "\n".join(lines)


def _score_command(args: argparse.Namespace) -> int:
    tasks = collect_tasks(args.trace_glob)
    if not tasks:
        print(f"no runs found matching {args.trace_glob!r}", file=sys.stderr)
        return 1
    transcript_texts = _read_transcripts(args.transcript_path)
    judge, judge_model, prompt_version = _pick_judge(args.stub)
    # `load_trace`, never `load_events(..., run_id=...)`: rlm-kit's own run_id filter is an
    # unguarded `event.get("run_id")`, so a non-dict trace line crashed INSIDE `load_events` — i.e.
    # upstream of every `ctx_distillery` function, where no amount of hardening there could reach
    # it. This call site is the only place that gap could be closed (see `trace_io.py`).
    rows = [
        score_run(
            task.run_id,
            task.trace_path,
            load_trace(task.trace_path, run_id=task.run_id),
            transcript_texts,
            judge=judge,
        )
        for task in tasks
    ]
    report = aggregate(rows, judge_model=judge_model, prompt_version=prompt_version)
    print(render_scorecard(report))
    # A batch where NOTHING scored (a dead judge, or every reply off-schema → every row unscored) is
    # NOT a green run: exit non-zero, following every sibling, so a CI gate keying on the exit code
    # cannot read an all-`--` scorecard as a pass. `not rows` is redundant with the empty-glob
    # refusal above today, and is kept because it is the condition that is actually true.
    if not rows or report.n_unscored == report.n:
        return 1
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
    score_parser.add_argument(
        "--stub",
        action="store_true",
        help="force the deterministic offline stub judge, even if CDEVAL_MODEL is set",
    )
    score_parser.set_defaults(func=_score_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
