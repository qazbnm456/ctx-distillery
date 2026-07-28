"""The SSE server: REPLAY-ONLY. No live-drive endpoint — `studio/README.md`'s "Scope: replay-only,
v1" holds the full argument and `CLAUDE.md` invariant 10 the summary. The reason is NOT the one this
docstring used to give ("`run_distillation` needs a caller-supplied `HarnessAdapter` + `chat_fn`
already wired, unlike a self-contained driver a web request could own"): `ctx_distillery.cli`'s
`_cmd_distill` IS that driver. Three reasons survive it — (a) no cancel seam anywhere in
`run_distillation` or rlm-kit for a multi-minute, 30-turn sandboxed episode, so an HTTP-started run
could only be hung or SIGKILLed into exactly the truncated trace `stream_run` below papers over;
(b) the import-level `live`-extra valve every sibling has is unavailable, because replay itself
calls `assemble`, which ships in the same distribution as the driver; and (c), the strongest, the
live input would be a `project_dir` — an unauthenticated HTTP parameter selecting whose ENTIRE
Claude Code history gets rendered and shipped to a remote model, with no `_slug_id` analogue to
protect it. The positive case: `ctx-distillery distill` writes into `$CTXD_TRACES_DIR`, the same
directory this server globs, so `distill` -> refresh -> Load already covers the use case.

Unlike diff-sentry-studio (whose replay mode reads a durable `responses/{run_id}.json` PLUS a
`traces/{run_id}.jsonl`), `ctx-distillery` writes NO artifact of its own — `run_distillation`
returns an in-memory `AssembledPlan` and persists nothing (the whole point of `CLAUDE.md` invariant
1). So this studio's sole source of truth, for both discovery and replay, is the trace file itself
(`{TRACES_DIR}/{run_id}.jsonl`) — there is no second JSON to read alongside it.

Six endpoints:
- `GET /`                                — serve the frontend shell.
- `GET /v1/config`                       — `{"traces_dir": ...}` only.
- `GET /v1/runs`                         — discover run ids by globbing `{TRACES_DIR}/*.jsonl`.
- `GET /v1/runs/{run_id}`                — the assembled plan + rubric facts, read-only.
- `GET /v1/runs/{run_id}/events`         — SSE replay of the trace, mapped through `mapper.to_event`.
- `GET /v1/runs/{run_id}/iterations`     — the per-turn Trajectory breakdown (`iterations.build_iterations`).
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

from ctx_distillery.rubric import plan_from_events, rubric_from_meta, trace_facts
from ctx_distillery.schema import assemble
from ctx_distillery.trace_io import load_trace

from .iterations import build_iterations
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


#: Cap on a slugged run_id, matching `toolscout_studio.app._RUN_ID_MAX`. A slug becomes ONE
#: filename component, and most filesystems cap a component at 255 BYTES — see `_slug_id`.
#: The same number is now carried by every slugger in this workspace: `ctx_distillery.cli._slug`,
#: `ctx_distillery.apply._SLUG_MAX` and `ctx_distillery_eval.cli._TASK_ID_MAX`. The last two arrived
#: a review later than this one — the first pass capped only the two READ-side sluggers.
_RUN_ID_MAX = 120


def _slug_id(raw: str) -> str:
    """A filesystem-/URL-safe id token: keep `[A-Za-z0-9._-]`, fold the rest (incl. `/`) to `-`,
    strip leading/trailing `.`/`-` so it can NEVER become a traversal segment (`..`, an absolute
    path, a nested dir), and cap at `_RUN_ID_MAX` chars — re-stripping after the cut so a truncation
    landing on a `-`/`.` never leaves a trailing separator. `run_id` becomes a file path — a studio
    reachable over HTTP must not open a path-traversal hole on itself just because this project's
    OWN trace files are normally trusted.

    Follows `toolscout_studio.app._slug_id`. This used to say "copied verbatim from
    `diff_sentry_studio.app._slug_id`", and that provenance was TRUE — which was exactly the
    problem, because diff-sentry has no cap either and we inherited the gap by copying the older
    sibling. Reproduced before fixing: `GET /v1/runs/<5000 x's>` produced a 5000-char slug, so
    `_load_trace`'s `path.exists()` raised a raw `OSError` (ENAMETOOLONG, errno 63 on macOS / 36 on
    Linux) straight out of the endpoint — a 500 where a 404 belongs, and the one remaining hole in
    this module's "never raise on a bad run_id" contract, alongside the non-dict-line guard.
    `Path.exists()` does NOT swallow that errno.

    The cut cannot empty the token (leading separators are already gone, so `token[0]` is
    alphanumeric), and re-slugging a slug is the identity — which matters because every read path
    slugs again."""
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", raw or "").strip("-.")
    token = token[:_RUN_ID_MAX].rstrip("-.")
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
    """The assembled plan (re-sourced from the trace, never trusted from the plan's own claim),
    the ATLAS rubric facts for the same run, and the CRITERIA those facts belong to —
    `{"plan": {...}, "rubric_facts": {...}, "rubric_criteria": [...]}`. 404 when the trace file
    doesn't exist; NEVER 500 on a malformed-but-readable trace.

    `rubric_criteria` carries each criterion's own `description`, recovered from the run's
    `run_start` meta by `rubric.rubric_from_meta`. The console renders it as the note under each
    module heading, which is what makes a bare `min_draft_step: 1` legible to a reviewer who did
    not write the rubric. The alternative — four descriptions copied into `app.js` — is the
    duplication invariant 11 exists to stop: it would drift the moment a criterion is reworded,
    and silently, because nothing compares the two. Recovered PER RUN rather than from
    `default_rubric()`, so an OLD trace renders the rubric it actually ran under, not today's.
    """
    events = _load_trace(run_id)
    plan = plan_from_events(events)
    assembled = assemble(events, plan)
    facts = trace_facts(events)
    # Built field by field, NOT `dataclasses.asdict` and not `model_dump()`. `Criterion` is a
    # PYDANTIC model, so `asdict` raises outright — and the test suite stayed green through that
    # bug because no fixture trace carries `meta["rubric"]`, so the list was always empty and the
    # conversion never ran. It took a real trace to fail. An explicit allowlist also means a future
    # field on `Criterion` cannot ride into an HTTP response unnoticed.
    criteria = [
        {
            "name": getattr(c, "name", ""),
            "category": getattr(c, "category", ""),
            "description": getattr(c, "description", ""),
        }
        for c in rubric_from_meta(events).criteria
    ]
    return JSONResponse(
        {
            "plan": dataclasses.asdict(assembled),
            "rubric_facts": facts,
            "rubric_criteria": criteria,
        }
    )


@app.get("/v1/runs/{run_id}/iterations")
def get_iterations(run_id: str) -> JSONResponse:
    """The Trajectory drawer's data: the run's REPL turns, its tool/sub-LM timeline, and its initial
    state — `iterations.build_iterations` over the same events every other endpoint reads.

    Deliberately routed through `_load_trace` rather than a local reader. All three sibling studios
    still carry a non-dict-line 500 in THEIR `/iterations` path (their loader catches only
    `JSONDecodeError`, so a JSON-valid `42`/`null`/`[1,2,3]` line reaches a `.get(...)` and raises a
    raw `AttributeError`). Going through `_load_trace` inherits `trace_io.load_trace`'s dict-shape
    filter, the 404/502 split `docs/DESIGN.md` §5.6 requires as a UI contract, and `_slug_id`
    sanitization — all three for free, and `iterations.py` itself stays web-dep-free."""
    return JSONResponse(build_iterations(_load_trace(run_id)))


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
