"""THE entry point: a project directory in -> a proposed distillation plan out. Nothing is written.

    ctx-distillery distill                        # distill the current directory's Claude Code sessions
    ctx-distillery distill /path/to/project        # ... a specific project
    ctx-distillery show traces/<run-id>.jsonl      # re-read a finished run's plan (offline)
    ctx-distillery show traces/<run-id>.jsonl --json

`distill` needs model credentials (`CD_*`, see `.env.example`) and a sandbox; `show` is fully
offline and works on any trace file. Drive runs through this CLI rather than an ad-hoc script — if
something is missing, extend it here.

**Applying a plan is a DIFFERENT command in a DIFFERENT module: `ctx-distillery-apply`.** That split
is structural, not stylistic. `tests/test_no_write_capability.py::test_apply_is_unreachable_from_the_planner_path`
asserts that NO module under `ctx_distillery/` (except `apply.py` itself, which is excluded from the
scan) imports the writer — and it matches a function-local import just as readily as a top-level one.
A single binary offering both `distill` and `apply` would need one module importing both, which turns
that test red; the test is the guard that makes `apply.py`'s mutation-scan exemption safe
(`CLAUDE.md` invariant 8), so the CLI is shaped around it rather than the other way round. Hence:
this module never imports `apply`, and `apply.py` hosts its own `main()`.

Two visible consequences of the same invariant, both deliberate:

* **`show` has no `--out`.** This module is inside the mutation scan, so it may not open a file for
  writing at all. Redirect with `>` — the rendering goes to stdout.
* **A run id is unique per invocation.** `TraceRecorder` APPENDS, and the sibling projects' `run()`
  drops a stale trace with `os.remove` before recording; that call is forbidden here. So the default
  `--run-id` carries a UTC timestamp and every run gets its own file. There is deliberately no
  `--force` that deletes one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .adapters.claude_code import ClaudeCodeAdapter, project_storage_dir, transcript_files
from .config import DistillConfig, make_chat_fn, setup
from .render import plan_as_dict, render_plan
from .rubric import plan_from_events
from .session import AssembledPlan, assemble, run_distillation
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
    ctx-distillery show traces/<run-id>.jsonl       # re-read a finished run's plan (offline)
    ctx-distillery show traces/<run-id>.jsonl --json

`distill` needs model credentials (CD_*, see .env.example) and a sandbox; `show` is fully offline.

APPLYING a plan is a separate command, `ctx-distillery-apply` - a different binary on purpose, so
that nothing on the planner's side can reach the one module that writes. Two things follow from the
same rule and are worth knowing up front: `show` has no --out (redirect with `>`), and every run
gets a unique, timestamped id because a trace file is appended to and never deleted here.
"""


def _slug(raw: str) -> str:
    """A filesystem-safe id token: keep `[A-Za-z0-9._-]`, fold the rest to `-`, strip leading and
    trailing `.`/`-` so it can never become a traversal segment (`..`, an absolute path, a nested
    directory). The run id becomes a FILENAME, and `--run-id` is user input; the same reasoning (and
    the same character class) as `studio`'s `_slug_id`.
    """
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "").strip("-.")
    return token


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

    config = setup(DistillConfig.from_env())
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
    adapter = ClaudeCodeAdapter.for_project(project, home=args.claude_home)

    print(f"distilling {len(found)} transcript(s) for {project} as {run_id} ...")
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

    Reads through `trace_io.load_trace`, never `rlm_kit.trace.load_events` directly (`CLAUDE.md`
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
    d.add_argument("--json", action="store_true", help="emit the plan as JSON instead of text")
    d.set_defaults(func=_cmd_distill)

    s = sub.add_parser("show", help="re-read a finished run's proposed plan from its trace (offline)")
    s.add_argument("trace", help="path to a trace JSONL file")
    s.add_argument("--run-id", default=None, help="only read events for this run id")
    s.add_argument("--json", action="store_true", help="emit the plan as JSON instead of text")
    s.set_defaults(func=_cmd_show)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
