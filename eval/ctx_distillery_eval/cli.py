"""`ctx-distillery-eval` — `score` existing traces, or `run` a taskset and score what it produced.

    ctx-distillery-eval score "traces/*.jsonl" transcript.txt            # score EXISTING traces
    ctx-distillery-eval score "traces/*.jsonl" transcript.txt --taskset ts.json
    ctx-distillery-eval run demo --out ./output/eval                     # drive, then score
    ctx-distillery-eval run taskset.json --out ./output/eval --stub

`score` is offline-capable and needs judge credentials at most (none with the stub). `run`
additionally needs ctx-distillery's full solve stack (`CD_*` credentials + a Deno/pyodide sandbox),
imported LAZILY inside the command so `score` never pulls dspy.

**Judgment call, stated explicitly** (the implementation plan's resolved decision requires the transcript
path(s) to be a mandatory second input alongside the trace path, but does not specify how transcripts
multiplex across a BATCH of runs matched by a glob — a real gap the plan itself flagged as something to
resolve "before writing judge.py, not after"): **one `score` invocation's transcript path(s) apply to
EVERY run `trace_glob` matches in that invocation.** This fits the shape this eval member is actually
built for — scoring several runs (retries, ablations, rubric variants) over the SAME transcript set —
and keeps the contract simple and literal about what the plan requires: transcripts are a required,
positional, non-fallback input, never optional and never sourced from the trace. Scoring a batch
where each matched run drew from a DIFFERENT transcript set is explicitly out of scope for this pass;
invoke `score` once per transcript set in that case. A "known simplification, because X" call, per
this project's established convention — not a silent guess. (`run` has no such ambiguity: it drives
each task itself, so each row is judged against the transcripts THAT run actually saw — the redacted
ones, straight off `DistillArtifacts`.)

**`score --taskset` is OPTIONAL, and its optionality is the point.** Every sibling's `score` takes a
taskset POSITIONALLY, because theirs cannot reconstruct a planner-visible input from a trace without
one. Ours can and always could: the two existing positionals stay exactly where they were, and
`--taskset` only adds judge-only `reference` text, paired on `Task.run_id == EvalTask.id` (the
family's convention; `collect_tasks` reads the trace ENVELOPE's `run_id`, not the filename stem, so a
`run`-produced `<id>-<stamp>.jsonl` still pairs). A run_id with no matching task simply gets an empty
reference and the v1 prompt — never a refusal, because scoring traces the taskset does not describe
is the normal case here, not an error.

**The judge is LIVE iff `CDEVAL_MODEL` is set and `--stub` was not passed** (`_pick_judge`), matching
every sibling eval member's rule. With no `CDEVAL_MODEL` the deterministic `StubJudge` runs instead:
fully offline, zero credentials, zero network — the CI path and the default. `--stub` forces it even
when the environment is configured, which is what makes a live-configured shell still able to run the
offline suite. Note `--stub` forces the offline JUDGE only: `run` still drives a real distillation,
which is what needs `CD_*`.

**Three things `run` deliberately does NOT copy from the siblings**, each a real defect there:

1. **No `os.remove` of a stale trace.** `ctx_distillery/cli.py`'s docstring already names that call
   as forbidden in this project, and this member follows the same rule. `TraceRecorder` APPENDS, so
   `run` instead derives a UNIQUE trace FILENAME per invocation — `<slug(task.id)>-<UTC stamp>.jsonl`
   — while keeping `run_id == task.id` so the pairing above still works. No `--force` that deletes.
2. **Everything lands under `--out`** (the toolscout/diff-sentry form). cve-reverser's `run` builds
   CWD-relative `sources/`/`traces/`/`responses/` paths despite its docstring promising otherwise.
3. **A failing task becomes an `unscored` ROW, never an aborted batch.** cve-reverser raises a bare
   `SystemExit` inside its run loop, so task 1 of 50 kills the other 49. `EvalRow` already models
   "unscored, with a reason" correctly here, so a missing `project`, a missing `CD_ROOT_LM`, or a
   planner that explodes mid-run is reported per-row and the batch continues. The refusal is still
   LOUD — the reason is printed in the scorecard and the aggregate gate below still exits non-zero
   when nothing scored — it is just no longer fatal to the runs after it.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from ctx_distillery.trace_io import load_trace

from .judge import PROMPT_VERSION, EvalJudgeConfig, Judge, StubJudge, make_eval_judge
from .schema import EvalReport, EvalRow
from .score import aggregate, score_run
from .taskset import EvalTask, collect_tasks, demo_taskset, load_taskset


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


def _references_for(taskset_path: str | None) -> dict[str, str]:
    """`{task id: reference}` from an optional taskset — `{}` when none was given.

    Loading failures are NOT swallowed: `load_taskset` raises `ValueError`/`OSError`/`JSONDecodeError`
    on a malformed or missing file, and a taskset the operator explicitly passed and that cannot be
    read is a typo to fix, not a condition to degrade past (unlike a run_id the taskset simply does
    not describe, which is normal and gets an empty reference).
    """
    if not taskset_path:
        return {}
    return {task.id: task.reference for task in load_taskset(taskset_path)}


def _load_tasks(spec: str, *, root: Path) -> list[EvalTask]:
    """`demo` materializes the built-in taskset under `root`; anything else is a JSON path.

    `root` is where `demo_taskset` may create files, and it is ALWAYS the caller's `--out` — the demo
    taskset's project directories and its `claude-home/` stand-in are machine-dependent by
    construction (see `taskset.demo_taskset`), so they have to be materialized somewhere, and the
    only defensible somewhere is a directory the operator named.
    """
    return demo_taskset(root / "demo") if spec == "demo" else load_taskset(spec)


#: Cap on a slugged task id, matching `ctx_distillery_studio.app._RUN_ID_MAX`. A slug becomes ONE
#: filename component (plus a `-<stamp>.jsonl` suffix), and most filesystems cap one at 255 BYTES.
_TASK_ID_MAX = 120


def _slug(raw: str) -> str:
    """A filesystem-safe token: keep `[A-Za-z0-9._-]`, fold the rest to `-`, strip leading/trailing
    `.`/`-` so it can never become a traversal segment, and cap at `_TASK_ID_MAX` chars —
    re-stripped after the cut so a truncation landing on a `-`/`.` never leaves a trailing
    separator. A task id comes out of a hand-edited JSON file and becomes a FILENAME here; same
    character class and same reasoning as `ctx_distillery.cli._slug` and
    `ctx_distillery_studio.app._slug_id`.

    The cap was added with the studio's, per the same review: this is the same class of exposure and
    the WRITE side of it. `_run_command` builds `<slug>-<stamp>.jsonl` and hands it to
    `TraceRecorder`, so an over-long task id turned into an `OSError` (ENAMETOOLONG) mid-batch
    rather than a task that simply runs. Unlike the studio's, this one may still return "" for a
    fully-degenerate id — its caller's `or 'task'` is the fallback, and that predates the cap.

    The `ctx_distillery.cli._slug` cross-reference above was ASPIRATIONAL when it was written: that
    function had no cap at all, and a third review found it (plus `ctx_distillery.apply.slugify`,
    which takes untrusted model output) still uncapped while this docstring claimed "same reasoning".
    All four sluggers in the workspace now cap at 120, so the claim is true rather than intended.
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "").strip("-.")[:_TASK_ID_MAX].rstrip("-.")


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


def _emit(rows: list[EvalRow], *, judge_model: str, prompt_version: str) -> int:
    """Aggregate, print the scorecard, and return the shared exit code. One owner for both commands.

    A batch where NOTHING scored (a dead judge, every reply off-schema, or — for `run` — every task
    failing to drive) is NOT a green run: exit non-zero, following every sibling, so a CI gate keying
    on the exit code cannot read an all-`--` scorecard as a pass. `not rows` is kept alongside
    because it is the condition that is actually true for an empty batch.
    """
    report = aggregate(rows, judge_model=judge_model, prompt_version=prompt_version)
    print(render_scorecard(report))
    if not rows or report.n_unscored == report.n:
        return 1
    return 0


def _score_command(args: argparse.Namespace) -> int:
    tasks = collect_tasks(args.trace_glob)
    if not tasks:
        print(f"no runs found matching {args.trace_glob!r}", file=sys.stderr)
        return 1
    transcript_texts = _read_transcripts(args.transcript_path)
    references = _references_for(args.taskset)
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
            # `.get(..., "")`: a run the taskset does not describe is scored with NO reference and
            # the v1 prompt, never skipped — see this module's docstring.
            reference=references.get(task.run_id, ""),
        )
        for task in tasks
    ]
    return _emit(rows, judge_model=judge_model, prompt_version=prompt_version)


def _drive(task: EvalTask, trace_path: Path):
    """Drive ONE task's distillation and return its `DistillArtifacts`. Raises on any refusal.

    `ctx_distillery`'s dspy-bearing surface is imported HERE, inside the function — importing
    `ctx_distillery_eval.cli` must stay dspy-free and openai-free (`eval/tests/test_boundary.py`
    pins that in a fresh subprocess), and `run` is the only mode that needs the solve stack at all.

    A missing `project` is refused LOUDLY (cve-reverser's stance — diff-sentry silently drives `{}`,
    which is a gap there rather than a design choice), as is a `project_dir` that is not a directory.
    Both raise; `_run_command`'s loop turns them into an unscored ROW so the rest of the batch still
    runs. `claude_home` is honoured as its own field because CLAUDE.md invariant 6 forbids anything
    here from silently reading the machine's real `~/.claude`.

    Returns the ARTIFACTS, not the plan: the judge must read the REDACTED transcripts this run
    actually saw. Re-`ingest()`ing and re-`redact()`ing would score against a different redaction,
    and the trace records only offset/length metadata for them, never the bodies.
    """
    import asyncio

    from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter
    from ctx_distillery.config import DistillConfig, make_chat_fn, setup
    from ctx_distillery.session import run_distillation_artifacts

    project = task.project.get("project_dir")
    if not project:
        raise ValueError(
            f"task {task.id!r} has no project.project_dir — `run` drives a real project directory, "
            f"so it cannot be defaulted (a {{id, reference}}-only taskset is fine for `score`)"
        )
    project_dir = Path(str(project)).expanduser().resolve()
    if not project_dir.is_dir():
        raise ValueError(f"task {task.id!r}: project_dir {str(project_dir)!r} is not a directory")
    home = task.project.get("claude_home") or None

    config = setup(DistillConfig.from_env())
    adapter = ClaudeCodeAdapter.for_project(project_dir, home=home)
    return asyncio.run(
        run_distillation_artifacts(
            adapter,
            make_chat_fn(config),
            str(trace_path),
            run_id=task.id,  # NOT the filename — the pairing convention keys on the envelope id
            meta={
                "project_dir": str(project_dir),
                "planner": config.main_model,
                "drafter": config.draft_model or config.sub_model,
                "interpreter": config.interpreter,
                "max_iterations": config.max_iterations,
                "max_llm_calls": config.max_llm_calls,
            },
        )
    )


def _run_command(args: argparse.Namespace) -> int:
    """Drive every task in the taskset, then score each fresh run against its own reference."""
    outdir = Path(args.out).expanduser()
    traces_dir = outdir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    try:
        tasks = _load_tasks(args.taskset, root=outdir)
    except (OSError, ValueError) as exc:
        print(f"cannot load taskset {args.taskset!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not tasks:
        print(f"taskset {args.taskset!r} is empty — nothing to run", file=sys.stderr)
        return 1

    judge, judge_model, prompt_version = _pick_judge(args.stub)
    # ONE stamp per invocation, so a second `run` of the same taskset writes alongside the first
    # instead of appending into it (`TraceRecorder` appends and nothing here may delete).
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows: list[EvalRow] = []
    for task in tasks:
        trace_path = traces_dir / f"{_slug(task.id) or 'task'}-{stamp}.jsonl"
        if trace_path.exists():
            # The one case a per-invocation stamp does not cover, and it must not silently append:
            # `load_taskset` refuses duplicate IDS, but two distinct ids can slug to the same token
            # (`a/b` and `a-b`), and two invocations inside the same second share a stamp. Either
            # way `TraceRecorder` would interleave two runs into one file under two run ids, after
            # which `load_trace(path, run_id=...)` can no longer separate them. Nothing here may
            # delete the existing file, so refusing this ONE task — as a row, batch intact — is the
            # honest move; the same reasoning as `ctx_distillery.cli._cmd_distill`'s refusal.
            rows.append(EvalRow(
                run_id=task.id,
                trace_path=str(trace_path),
                unscored_reason=(
                    f"run skipped: {trace_path} already exists and a trace is appended to rather "
                    f"than replaced — rename it, or give this task a distinct id"
                ),
            ))
            continue
        print(f"running {task.id} -> {trace_path} ...", file=sys.stderr)
        try:
            artifacts = _drive(task, trace_path)
        except (SystemExit, Exception) as exc:  # noqa: BLE001 — one bad task must not kill the batch
            # `SystemExit` is listed explicitly and is NOT redundant: it derives from
            # `BaseException`, so a bare `except Exception` would let `DistillConfig.from_env`'s
            # refusals (no `CD_ROOT_LM`, a non-pyodide interpreter, a subscription drafter) abort the
            # whole run. `KeyboardInterrupt` is deliberately still NOT caught.
            rows.append(
                EvalRow(
                    run_id=task.id,
                    trace_path=str(trace_path) if trace_path.exists() else "",
                    unscored_reason=f"run failed: {type(exc).__name__}: {exc}",
                )
            )
            continue
        rows.append(
            score_run(
                task.id,
                artifacts.trace_path,
                artifacts.events,
                artifacts.transcripts,  # the REDACTED texts the run itself saw
                judge=judge,
                reference=task.reference,
            )
        )
    return _emit(rows, judge_model=judge_model, prompt_version=prompt_version)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctx-distillery-eval",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
    # An OPTION, not a third positional: the two positionals above are the shipped contract and must
    # not move, and a taskset genuinely adds nothing but judge-only `reference` text here.
    score_parser.add_argument(
        "--taskset",
        default=None,
        help=(
            "optional taskset JSON — supplies each run's judge-only `reference`, paired on "
            "run_id == task id. A run with no matching task is scored without one."
        ),
    )
    score_parser.add_argument(
        "--stub",
        action="store_true",
        help="force the deterministic offline stub judge, even if CDEVAL_MODEL is set",
    )
    score_parser.set_defaults(func=_score_command)

    run_parser = sub.add_parser(
        "run",
        help="drive ctx-distillery per task, then score the fresh runs (needs CD_* creds + a sandbox)",
    )
    run_parser.add_argument(
        "taskset", help="taskset JSON path, or the literal 'demo' for the built-in offline set"
    )
    run_parser.add_argument(
        "--out",
        default="./output/eval",
        help="output directory — traces and any materialized demo taskset land under it "
             "(default ./output/eval)",
    )
    run_parser.add_argument(
        "--stub",
        action="store_true",
        help="force the deterministic offline stub JUDGE (the distillation itself still runs live)",
    )
    run_parser.set_defaults(func=_run_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
