"""THE entry point: a project directory in -> a proposed distillation plan out. Nothing is written.

    ctx-distillery distill                        # distill the current directory's Claude Code sessions
    ctx-distillery distill /path/to/project        # ... a specific project
    ctx-distillery show traces/<run-id>.jsonl      # re-read a finished run's plan (offline)
    ctx-distillery show traces/<run-id>.jsonl --json
    ctx-distillery export "traces/*.jsonl" > ds.json   # reward-free SFT/RL dataset (offline)

`distill` needs model credentials (`CD_*`, see `.env.example`) and a sandbox; `show` and `export`
are fully offline and work on any trace file. Drive runs through this CLI rather than an ad-hoc
script — if something is missing, extend it here.

**Applying a plan is a DIFFERENT command in a DIFFERENT module: `ctx-distillery-apply`.** That split
is structural, not stylistic. `tests/test_no_write_capability.py::test_apply_is_unreachable_from_the_planner_path`
asserts that NO module under `ctx_distillery/` (except `apply.py` itself, which is excluded from the
scan) imports the writer — and it matches a function-local import just as readily as a top-level one.
A single binary offering both `distill` and `apply` would need one module importing both, which turns
that test red; the test is the guard that makes `apply.py`'s mutation-scan exemption safe
(`CLAUDE.md` invariant 8), so the CLI is shaped around it rather than the other way round. Hence:
this module never imports `apply`, and `apply.py` hosts its own `main()`.

Two visible consequences of the same invariant, both deliberate:

* **`show` and `export` have no `--out`.** This module is inside the mutation scan, so it may not
  open a file for writing at all. Redirect with `>` — the rendering goes to stdout. Note the form:
  `print(json.dumps(...))`, never `json.dump(..., sys.stdout)`. Both pass the scan textually, but
  the second calls `.write` at runtime while only LOOKING clean, which is evading the tripwire
  rather than satisfying it. (The three sibling projects' `export` all take a positional `out` path
  and `open(out, "w")` it; that is the one thing from their exporter that cannot be ported here.)
* **A run id is unique per invocation.** `TraceRecorder` APPENDS, and the sibling projects' `run()`
  drops a stale trace with `os.remove` before recording; that call is forbidden here. So the default
  `--run-id` carries a UTC timestamp and every run gets its own file. There is deliberately no
  `--force` that deletes one.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .adapters.claude_code import (
    INDEX_LINE_MAX,
    ClaudeCodeAdapter,
    project_storage_dir,
    subagent_files,
    transcript_files,
)
from .config import DistillConfig, make_chat_fn, setup
from .render import plan_as_dict, render_plan
from .rl_export import export_dataset, load_runs
from .rubric import plan_from_events
from .schema import AssembledPlan, assemble
from .session import run_distillation
from .trace_io import load_trace

#: Where `distill` records, unless `--trace-dir` says otherwise. `CTXD_TRACES_DIR` is the SAME
#: variable `studio/` already reads for its own default, so a `distill` run shows up in the studio's
#: Load picker without configuring two directories that have to agree.
_DEFAULT_TRACE_DIR = "./traces"

#: `--help`'s prose. The module docstring above is for whoever maintains this file (it explains WHY
#: applying lives in a second binary); a user running `--help` needs the invocations and the two
#: behaviours that will otherwise surprise them.
_CLI_DESCRIPTION = """\
Propose a distillation plan over a project's Claude Code sessions. This never writes anything.

    ctx-distillery distill                         # distill the current directory's sessions
    ctx-distillery distill /path/to/project         # ... a specific project
    ctx-distillery distill --include-subagents      # ... and its subagent transcripts too
    ctx-distillery show traces/<run-id>.jsonl       # re-read a finished run's plan (offline)
    ctx-distillery show traces/<run-id>.jsonl --json
    ctx-distillery export "traces/*.jsonl" > ds.json    # reward-free SFT/RL dataset (offline)

`distill` needs model credentials (CD_*, see .env.example) and a sandbox; `show` and `export` are
fully offline.

APPLYING a plan is a separate command, `ctx-distillery-apply` - a different binary on purpose, so
that nothing on the planner's side can reach the one module that writes. Two things follow from the
same rule and are worth knowing up front: neither `show` nor `export` has --out (redirect with `>`),
and every run gets a unique, timestamped id because a trace file is appended to and never deleted
here.
"""


#: Cap on a slugged run id, matching `apply._SLUG_MAX` and the two workspace members' own sluggers
#: (`studio`'s `app._RUN_ID_MAX`, `eval`'s `cli._TASK_ID_MAX`). A slug becomes ONE filename
#: component and most filesystems cap one at 255 BYTES.
_RUN_ID_MAX = 120


def _slug(raw: str) -> str:
    """A filesystem-safe id token: keep `[A-Za-z0-9._-]`, fold the rest to `-`, strip leading and
    trailing `.`/`-` so it can never become a traversal segment (`..`, an absolute path, a nested
    directory), and cap at `_RUN_ID_MAX` characters — re-stripping after the cut so a truncation
    landing on a `-`/`.` never leaves a trailing separator. The run id becomes a FILENAME, and
    `--run-id` is user input; the same reasoning (and the same character class) as `studio`'s
    `_slug_id`.

    The cap was the LAST of the four to be applied, and it is the WRITE side: `_cmd_distill` builds
    `<slug>.jsonl` and hands it to `TraceRecorder`, so `--run-id <300 x's>` raised a raw `OSError`
    (ENAMETOOLONG) out of a real run instead of simply running. `eval`'s slugger already cited this
    function as "same reasoning" while this one had no cap at all — that cross-reference is true now.
    """
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "").strip("-.")
    return token[:_RUN_ID_MAX].rstrip("-.")


def default_run_id(project_dir: Path) -> str:
    """`<project-name>-<UTC timestamp>` — readable, and unique per invocation.

    Uniqueness is load-bearing, not cosmetic: `TraceRecorder` appends, and this package may not
    delete a stale trace (see the module docstring), so two runs sharing a run id would interleave
    into one file under one id and `load_trace(path, run_id=...)` could no longer separate them.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{_slug(project_dir.name) or 'project'}-{stamp}"


def _trace_dir(argument: str | None) -> Path:
    return Path(argument or os.environ.get("CTXD_TRACES_DIR") or _DEFAULT_TRACE_DIR).expanduser()


def _emit(plan: AssembledPlan, *, as_json: bool) -> str:
    return (
        json.dumps(plan_as_dict(plan), indent=2, ensure_ascii=False)
        if as_json
        else render_plan(plan)
    )


# ---- subcommands -------------------------------------------------------------


def _cmd_distill(args) -> int:
    """Run one distillation over a project's Claude Code storage and print the proposed plan.

    `run_distillation` calls `adapter.ingest()` exactly once (`CLAUDE.md` invariant 5), so the
    emptiness check below goes through `transcript_files` — a plain directory listing of the same
    storage directory — rather than a second ingest. Saying "no transcripts found under <path>"
    beats proposing an empty plan and looking broken.
    """
    project = Path(args.project_dir).expanduser().resolve()
    if not project.is_dir():
        print(f"no such project directory: {project}", file=sys.stderr)
        return 1

    found = transcript_files(project, home=args.claude_home)
    if not found:
        storage = project_storage_dir(project, home=args.claude_home)
        print(
            f"no transcripts found under {storage} — that is where Claude Code stores this "
            f"project's past conversations, one <session-id>.jsonl per conversation. Nothing to "
            f"distill yet.",
            file=sys.stderr,
        )
        return 1

    subagents = subagent_files(project, home=args.claude_home) if args.include_subagents else []
    entries = len(found) + len(subagents)

    config = setup(DistillConfig.from_env())
    # The ENTRY ceiling, stated at the point of use rather than discovered inside a truncated scan.
    # The planner orients itself by printing line 0 of every entry, and `CD_MAX_OUTPUT_CHARS` caps
    # ONE REPL cell's output — so past `max_output_chars // (INDEX_LINE_MAX + 1)` entries that scan
    # comes back head+tail truncated (visibly, with dspy's own marker, and pageable) rather than
    # complete. Both numbers come from one constant so they cannot drift apart.
    ceiling = config.max_output_chars // (INDEX_LINE_MAX + 1)
    if entries > ceiling:
        print(
            f"warning: {entries} transcript entries exceeds the ~{ceiling} that fit one REPL "
            f"cell's index scan at CD_MAX_OUTPUT_CHARS={config.max_output_chars}. The planner "
            f"still sees every entry and can page through them, but its one-shot overview will be "
            f"truncated; the full index is recorded in the trace either way.",
            file=sys.stderr,
        )
    run_id = _slug(args.run_id) if args.run_id else default_run_id(project)
    if not run_id:
        print(f"--run-id {args.run_id!r} reduces to an empty token — give it some [A-Za-z0-9._-]",
              file=sys.stderr)
        return 1
    trace_path = _trace_dir(args.trace_dir) / f"{run_id}.jsonl"
    if trace_path.exists():
        # `TraceRecorder` APPENDS and this module may not delete (see the module docstring), so a
        # re-used run id would interleave two runs into one file under one id — after which
        # `load_trace(path, run_id=...)` cannot separate them and `show` would render a mixture.
        # Refusing is the honest move: the operator picks a new id, and no data is destroyed.
        print(
            f"{trace_path} already exists, and a trace is appended to rather than replaced — "
            f"pass a different --run-id (the default is unique per invocation).",
            file=sys.stderr,
        )
        return 1
    adapter = ClaudeCodeAdapter.for_project(
        project, home=args.claude_home, include_subagents=args.include_subagents
    )

    # `entries`, not `len(found)`: with subagents on, a project with 19 sessions and 351 subagents
    # would otherwise report 19.
    #
    # This is what was DISCOVERED, which is an upper bound on what the planner ends up seeing —
    # `for_project` drops an entry that renders empty (an unreadable file, or one with no
    # user/assistant events at all). Deliberately not made exact: the true figure needs the render,
    # i.e. a second full `ingest()` over up to ~880 files, and `run_distillation` owns the one
    # ingest by invariant 5. An upper bound is also the RIGHT input for the ceiling warning below,
    # which should fire early rather than late.
    print(f"distilling up to {entries} transcript(s) for {project} as {run_id} ...")
    try:
        assembled = asyncio.run(
            run_distillation(
                adapter,
                make_chat_fn(config),
                str(trace_path),
                run_id=run_id,
                meta={
                    # A self-describing trace, the sibling convention: which project, which models,
                    # and the budget THIS run actually ran under.
                    "project_dir": str(project),
                    "planner": config.main_model,
                    "drafter": config.draft_model or config.sub_model,
                    "interpreter": config.interpreter,
                    "max_iterations": config.max_iterations,
                    "max_llm_calls": config.max_llm_calls,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001 — a failed run is still navigable via its partial trace
        print(f"the run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        if trace_path.exists():
            print(f"the trace up to the failure is at {trace_path}", file=sys.stderr)
        return 1

    print(_emit(assembled, as_json=args.json))
    print(f"\n-> {trace_path}")
    print(f"   review: ctx-distillery show {trace_path}")
    print(f"   apply:  ctx-distillery-apply {trace_path} --project {project} --approve <indices>")
    return 1 if assembled.problems else 0


def _cmd_show(args) -> int:
    """Re-read a finished run's plan from its trace. Offline: no model, no network, no sandbox.

    Reads through `trace_io.load_trace`, never `rlm_harness.trace.load_events` directly (`CLAUDE.md`
    invariant 11) — a JSON-valid non-dict line must be dropped before anything calls `.get()` on it,
    and delegating the `run_id` filter downstream would put the crash upstream of that guard.
    """
    try:
        events = load_trace(args.trace, run_id=args.run_id)
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.trace}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    assembled = assemble(events, plan_from_events(events))
    print(_emit(assembled, as_json=args.json))
    return 1 if assembled.problems else 0


def _cmd_export(args) -> int:
    """Print a reward-free SFT/RL dataset for one or more traces as JSON on stdout. Offline.

    No `--out`, and no `json.dump(..., sys.stdout)` — see the module docstring; the sibling
    projects' `export` writes a file, and this module may not. Redirect with `>`.

    An empty match REFUSES rather than emitting an empty bundle. A mistyped or unquoted glob is the
    overwhelmingly likely cause (an unquoted `traces/*.jsonl` is expanded by the shell before argv
    is built, so it silently becomes a literal that matches nothing), and quietly printing a
    well-formed dataset containing zero runs is the same failure mode `eval/`'s `_read_transcripts`
    already refuses: an empty required input must fail like a missing one.
    """
    paths = [path for pattern in args.trace for path in sorted(glob.glob(pattern))]
    if not paths:
        print(
            f"no trace files matched {' '.join(args.trace)} — quote the glob so the shell does not "
            f"expand it, and check the directory ($CTXD_TRACES_DIR, else {_DEFAULT_TRACE_DIR}).",
            file=sys.stderr,
        )
        return 1
    try:
        runs = load_runs(*paths)
        bundle = export_dataset(runs)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        # `export_dataset` is INSIDE the try deliberately — found by review, with a real traceback.
        # `trace_io.dict_events` guarantees dict-NESS, not key presence: it was written for the
        # `42`/`null`/`[1,2,3]` class of bad line. A dict-shaped line missing `type` (or `payload`,
        # or carrying a non-dict `payload`) sails through it and reaches rlm-harness's own direct
        # `e["type"]` indexing in `export_actions`, raising `KeyError` from a frame two libraries
        # deep. `TraceRecorder` always writes both keys, so reaching this needs a hand-edited or
        # foreign JSONL — but "a bad trace must not produce a traceback" is what this command
        # already promises elsewhere, and a promise that only holds for the malformed shapes we
        # happened to think of first is not one.
        print(f"cannot read the trace(s): {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
    print(
        f"runs={len(runs)} | actions={len(bundle['actions'])} (drafting={len(bundle['drafting'])}, "
        f"orchestrator_tools={len(bundle['orchestrator_tools'])}) | "
        f"sft_turns={len(bundle['sft_turns'])} | reward-free",
        file=sys.stderr,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ctx-distillery",
        description=_CLI_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("distill", help="propose a distillation plan for a project (needs CD_* creds + a sandbox)")
    d.add_argument("project_dir", nargs="?", default=".",
                   help="the project to distill (default: the current directory)")
    d.add_argument("--run-id", default=None,
                   help="run id, also the trace filename (default: <project>-<UTC timestamp>, "
                        "which is unique per invocation on purpose)")
    d.add_argument("--trace-dir", default=None,
                   help=f"directory to record the trace in (default: $CTXD_TRACES_DIR, else "
                        f"{_DEFAULT_TRACE_DIR} — the same variable ctx-distillery-studio reads)")
    d.add_argument("--claude-home", default=None,
                   help="override ~/.claude (a non-default install; also what keeps the tests hermetic)")
    d.add_argument("--include-subagents", action="store_true",
                   help="also distil this project's SUBAGENT transcripts "
                        "(<session-id>/subagents/**/agent-*.jsonl), each as its own labelled "
                        "entry. Off by default: it renumbers every transcript index and ships "
                        "substantially more text to the model, so it is an explicit act")
    d.add_argument("--json", action="store_true", help="emit the plan as JSON instead of text")
    d.set_defaults(func=_cmd_distill)

    s = sub.add_parser("show", help="re-read a finished run's proposed plan from its trace (offline)")
    s.add_argument("trace", help="path to a trace JSONL file")
    s.add_argument("--run-id", default=None, help="only read events for this run id")
    s.add_argument("--json", action="store_true", help="emit the plan as JSON instead of text")
    s.set_defaults(func=_cmd_show)

    e = sub.add_parser(
        "export",
        help="print a reward-free SFT/RL dataset for one or more traces as JSON on stdout (offline)",
    )
    e.add_argument("trace", nargs="+", help="trace file glob(s) — QUOTE them, e.g. 'traces/*.jsonl'")
    e.set_defaults(func=_cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
