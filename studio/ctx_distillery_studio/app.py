"""The SSE server: REPLAY-ONLY (no live-drive endpoint — see `CLAUDE.md` invariant 10's scope
decision: `run_distillation` needs a caller-supplied `HarnessAdapter` + `chat_fn` already wired,
unlike a self-contained one-shot driver a web request could reasonably own end-to-end).

Unlike diff-sentry-studio (whose replay mode reads a durable `responses/{run_id}.json` PLUS a
`traces/{run_id}.jsonl`), `ctx-distillery` writes NO artifact of its own — `run_distillation`
returns an in-memory `AssembledPlan` and persists nothing (the whole point of `CLAUDE.md` invariant
1). So this studio's sole source of truth, for both discovery and replay, is the trace file itself
(`{TRACES_DIR}/{run_id}.jsonl`) — there is no second JSON to read alongside it.

Five endpoints:
- `GET /`                                — serve the frontend shell.
- `GET /v1/config`                       — `{"traces_dir": ...}` only.
- `GET /v1/runs`                         — discover run ids by globbing `{TRACES_DIR}/*.jsonl`.
- `GET /v1/runs/{run_id}`                — the assembled plan + rubric facts, read-only.
- `GET /v1/runs/{run_id}/events`         — SSE replay of the trace, mapped through `mapper.to_event`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ctx_distillery.rubric import plan_from_events, trace_facts
from ctx_distillery.session import assemble
from ctx_distillery.trace_io import load_trace

from .mapper import to_event

# The workspace ROOT that owns this studio/ member (parents[2] of studio/ctx_distillery_studio/app.py).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Where ctx-distillery trace files live, resolved to an ABSOLUTE path so it is stable regardless of
# the process CWD. Default: `<root>/traces` (mirroring `DS_ARTIFACTS_DIR`'s override convention).
# `CTXD_TRACES_DIR` overrides to point at any other trace directory / checkout.
TRACES_DIR = Path(os.environ.get("CTXD_TRACES_DIR") or REPO_ROOT / "traces").expanduser().resolve()
STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="ctx-distillery-studio", version="0.1.0")


class _RevalidateStatic(StaticFiles):
    """Serve static assets with `Cache-Control: no-cache` so the browser ALWAYS revalidates — it
    still 304s when unchanged (via the ETag StaticFiles already sends, so it's cheap). Without this
    the zero-build `app.js`/`style.css` cache indefinitely, so a shipped frontend change silently
    shows the OLD UI until a manual hard-refresh. Mirrors `diff_sentry_studio.app._RevalidateStatic`
    verbatim."""

    async def get_response(self, path: str, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


# The zero-build vanilla frontend, served same-origin so no CORS. Guarded so a backend-only deploy
# without the dir still boots.
if STATIC.is_dir():
    app.mount("/static", _RevalidateStatic(directory=str(STATIC)), name="static")


@app.get("/")
def index() -> FileResponse:
    """Serve the single-page frontend shell."""
    idx = STATIC / "index.html"
    if not idx.exists():
        raise HTTPException(404, "frontend not present (static/index.html missing)")
    return FileResponse(str(idx), headers={"Cache-Control": "no-cache"})  # revalidate; never stale


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _slug_id(raw: str) -> str:
    """A filesystem-/URL-safe id token: keep `[A-Za-z0-9._-]`, fold the rest (incl. `/`) to `-`,
    strip leading/trailing `.`/`-` so it can NEVER become a traversal segment (`..`, an absolute
    path, a nested dir). `run_id` becomes a file path — a studio reachable over HTTP must not open a
    path-traversal hole on itself just because this project's OWN trace files are normally trusted.
    Copied verbatim from `diff_sentry_studio.app._slug_id`."""
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "").strip("-.")
    return token or "unknown"


def _trace_path(run_id: str) -> Path:
    return TRACES_DIR / f"{_slug_id(run_id)}.jsonl"


def _step_key(event: dict) -> int:
    """Sort key for SSE replay ordering: non-digit `step_id` sorts LAST, never raises. Copied
    verbatim from `diff_sentry_studio.app._step_key`."""
    s = str(event.get("step_id", ""))
    return int(s) if s.lstrip("-").isdigit() else 1 << 30


def _load_trace(run_id: str) -> list[dict]:
    """Read `{run_id}`'s trace file. 404 if it doesn't exist; 502 (never 500) on a genuinely
    external failure — an unreadable file or a corrupted JSONL line — mirroring `assemble`'s own
    "none of them raise" discipline all the way out to the HTTP layer.

    FIXED per adversarial review, then DE-DUPLICATED: `rlm_kit.trace.load_events` does NO shape
    validation — a line that is syntactically valid JSON but NOT an object (`42`, `"x"`, `[1,2,3]`,
    `null`) parses fine (no `ValueError`, so the 502 guard above never fires) and lands in the
    returned list as-is. Every downstream consumer (`plan_from_events`/`trace_facts`/`_step_key`/
    `mapper.to_event`) assumes dict shape and calls `.get(...)` unconditionally, so such a line
    reached them and raised a raw `AttributeError` — a genuine 500, and exactly what this
    function's own docstring already claimed (incompletely) not to allow.

    The filter that fixed it first lived inline HERE; it now lives in
    `ctx_distillery.trace_io.load_trace`, which this function calls, because `eval/` turned out to
    need the identical guard (its batch scoring died on the same input, in `collect_tasks` and
    again inside `load_events`'s own unguarded `run_id` filter) and a second copy is precisely what
    `CLAUDE.md` invariant 11 exists to prevent. That is a de-duplication, NOT a removal: this is
    still the ONE entry point every endpoint's events pass through, `_step_key`/`mapper.to_event`
    still never see a non-dict entry, and
    `test_get_run_never_500s_on_a_syntactically_valid_but_non_dict_jsonl_line` still pins the whole
    guarantee end to end.
    """
    path = _trace_path(run_id)
    if not path.exists():
        raise HTTPException(404, f"no trace for run {run_id!r}")
    try:
        return load_trace(str(path))
    except (OSError, ValueError) as exc:  # a half-written / corrupted trace file, not a code bug
        raise HTTPException(502, f"trace file for run {run_id!r} is unreadable: {exc}") from exc


@app.get("/v1/config")
def config() -> JSONResponse:
    """Unlike `diff-sentry-studio` (whose config is three env-var model-role names), this project's
    model is an INJECTED `chat_fn`, not an env-var-selected one — there is no `CTXD_ROOT_LM` to
    report. So this reports what genuinely varies by deployment instead: the traces directory a
    "why is my run not showing up" user should be able to check from the UI itself."""
    return JSONResponse({"traces_dir": str(TRACES_DIR)})


@app.get("/v1/runs")
def list_runs() -> JSONResponse:
    """Run ids that have a stored trace, sorted — feeds the Load picker. No `responses/` directory
    exists to enumerate instead of the trace file (see the module docstring)."""
    runs = sorted(p.stem for p in TRACES_DIR.glob("*.jsonl")) if TRACES_DIR.is_dir() else []
    return JSONResponse({"runs": runs})


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str) -> JSONResponse:
    """The assembled plan (re-sourced from the trace, never trusted from the plan's own claim) plus
    the ATLAS rubric facts for the same run — `{"plan": {...}, "rubric_facts": {...}}`. 404 when the
    trace file doesn't exist; NEVER 500 on a malformed-but-readable trace."""
    events = _load_trace(run_id)
    plan = plan_from_events(events)
    assembled = assemble(events, plan)
    facts = trace_facts(events)
    return JSONResponse({"plan": dataclasses.asdict(assembled), "rubric_facts": facts})


@app.get("/v1/runs/{run_id}/events")
async def stream_run(run_id: str, delay: float = 0.0) -> StreamingResponse:
    """Replay the run's trace as SSE, sorted by `step_id` (`_step_key`, matching
    `diff-sentry-studio`'s own ordering caveat: `main_step`s flush post-hoc with trailing step_ids,
    so a replay streams actions before reasoning turns). `delay` (seconds) paces it to feel live.
    If no `distill.run.completed` was ever mapped (a truncated trace — e.g. a hard-killed run whose
    recorder never reached `__exit__`), synthesize one at the end so a client waiting on it doesn't
    hang forever — same reasoning `diff_sentry_studio.app.stream_run` states for its own case."""
    events = sorted(_load_trace(run_id), key=_step_key)

    async def gen():
        saw_completed = False
        for event in events:
            out = to_event(event)
            if out is None:
                continue
            if out["event"] == "distill.run.completed":
                saw_completed = True
            yield _sse(out["event"], out["data"])
            if delay:
                await asyncio.sleep(delay)
        if not saw_completed:
            yield _sse("distill.run.completed", {})

    return StreamingResponse(gen(), media_type="text/event-stream")
