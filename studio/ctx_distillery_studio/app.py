"""The SSE server: REPLAY, plus an OPT-IN live-drive mode. `studio/README.md`'s "Scope: replay +
opt-in live" holds the full argument and `CLAUDE.md` invariant 10 the summary.

Live mode is entirely gated on `CTXD_LIVE_PROJECTS` (unset by default): with it unset, this module
behaves EXACTLY as the replay-only version always has — `POST /v1/distill` and `GET /v1/projects`
both 404, and the trace file remains the sole source of truth for both discovery and replay
(`ctx-distillery` writes NO artifact of its own; `run_distillation` returns an in-memory
`AssembledPlan` and persists nothing — CLAUDE.md invariant 1). Cancellation, when live mode is on,
is REAL: it is built into rlm-harness's own sandbox interpreter (`RLMTask(cancel_event=...)`,
`rlm_harness.SandboxCancelled`), not an `asyncio.Task.cancel()` that cannot interrupt a blocked
`pyodide`/`deno` sandbox call. Live mode still NEVER calls `ctx_distillery.apply.apply_plan` —
applying a plan stays a separate, human-invoked action outside any web request, exactly as before.

**Import discipline (load-bearing, not a style preference)**: this module must stay importable
without pulling `dspy` into `sys.modules`, even when live mode is fully enabled — `dspy` (and every
`ctx_distillery` module that transitively imports it: `.task`, `.session`, `.cli`) is deferred into
`.live.run_live`'s own body, which only executes inside a worker thread once a live run actually
starts. `.live`/`.shutdown` are safe to import at THIS module's own top because neither of them
imports anything dspy-bearing at ITS module top either (confirmed: `.live` imports
`rlm_harness.SandboxCancelled`, which is an EAGER, non-dspy-bearing top-level export from `rlm_harness`, and
`.iterations._project_label`, already dspy-free).

Ten endpoints:
- `GET /`                                — serve the frontend shell.
- `GET /v1/config`                       — `{"traces_dir": ...}` only.
- `GET /v1/projects`                     — the configured live-mode project allowlist (404 if unset).
- `GET /v1/runs`                         — discover run ids by globbing `{TRACES_DIR}/*.jsonl`, plus which are still live.
- `GET /v1/runs/{run_id}`                — the assembled plan + rubric facts, read-only.
- `GET /v1/runs/{run_id}/events`         — SSE replay of the trace, mapped through `mapper.to_event`.
- `GET /v1/runs/{run_id}/iterations`     — the per-turn Trajectory breakdown (`iterations.build_iterations`).
- `POST /v1/distill`                     — drive a LIVE distillation, streamed as `distill.*` SSE (404 unless live mode is on).
- `POST /v1/runs/{run_id}/cancel`        — cooperatively cancel an in-flight live run.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ctx_distillery.rubric import plan_from_events, rubric_from_meta, trace_facts
from ctx_distillery.schema import assemble
from ctx_distillery.trace_io import load_trace

from .iterations import _project_label, build_iterations
from .live import run_live
from .mapper import to_event
from .shutdown import cancel_all_inflight, install_cancel_on_signal, join_workers, restore_signals

# The workspace ROOT that owns this studio/ member (parents[2] of studio/ctx_distillery_studio/app.py).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Where ctx-distillery trace files live, resolved to an ABSOLUTE path so it is stable regardless of
# the process CWD. Default: `<root>/traces` (mirroring `DS_ARTIFACTS_DIR`'s override convention).
# `CTXD_TRACES_DIR` overrides to point at any other trace directory / checkout.
TRACES_DIR = Path(os.environ.get("CTXD_TRACES_DIR") or REPO_ROOT / "traces").expanduser().resolve()
STATIC = Path(__file__).resolve().parent.parent / "static"

# `CTXD_LIVE_PROJECTS`: comma-separated (NOT `os.pathsep` — that's `:` on POSIX, which collides with
# a Windows drive letter, and a plain Windows path can contain `:` too), resolved ONCE at import so
# every later membership check is a plain equality against an already-canonical path, never a fresh
# `resolve()` racing a symlink change mid-process. Empty/unset -> `()`, live mode fully off — every
# route below that depends on it 404s, and no other module-level state here is ever touched.
def _parse_live_projects(raw: str | None) -> tuple[Path, ...]:
    if not raw:
        return ()
    return tuple(Path(p).expanduser().resolve() for p in raw.split(",") if p.strip())


_LIVE_PROJECTS: tuple[Path, ...] = _parse_live_projects(os.environ.get("CTXD_LIVE_PROJECTS"))

# Live-run registries. Populated only from the event-loop thread (`distill()`'s check-and-reserve is
# synchronous, no `await` between the membership check and the `.add`/assignment, which is what
# closes the collision race two concurrent POSTs for the same run_id would otherwise leave open); the
# ONLY other mutator is `on_done`, called from the run's own worker thread, always as a same-key
# pop/discard. CPython dict/set single-key ops are atomic across threads, so no lock is needed for
# either side of that handoff.
_CANCELS: dict[str, threading.Event] = {}
_WORKERS: dict[str, threading.Thread] = {}
_RESERVED_RUN_IDS: set[str] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Install the SIGINT/SIGTERM -> cancel-all-inflight bridge at startup — see `shutdown.py`'s
    module docstring for why the cancel must happen IN the signal handler rather than only in this
    context manager's shutdown half (uvicorn's own graceful shutdown waits for open connections,
    including a live SSE stream, BEFORE running lifespan shutdown, so cancelling only here would
    deadlock on a run that never gets told to stop). The shutdown half below is a backstop for the
    case where no SSE connection is open to make uvicorn wait in the first place: cancel every
    in-flight run, then join its worker within a bounded total budget so a cancelled run gets a
    moment to write its final trace state before the process actually exits."""
    prev_handlers = install_cancel_on_signal(_CANCELS)
    try:
        yield
    finally:
        cancel_all_inflight(_CANCELS)
        join_workers(_WORKERS, budget_s=5.0)
        restore_signals(prev_handlers)


app = FastAPI(title="ctx-distillery-studio", version="0.1.0", lifespan=lifespan)


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


def _project_in_allowlist(project_dir: Path) -> bool:
    """Exact-match membership against `_LIVE_PROJECTS` only — never a prefix/substring test, the
    same defense CLAUDE.md invariant 5 states for `read_memory_file`'s allowlist (a substring test
    lets a crafted path through; an unresolved prefix test lets a `..`-segment trick pass). This is
    the ONLY thing standing between an HTTP request and reading a arbitrary directory's entire Claude
    Code history into a live distillation — see `studio/CLAUDE.md` invariant 10's reopening
    conditions, of which this allowlist is one."""
    try:
        resolved = project_dir.expanduser().resolve()
    except OSError:
        return False
    return resolved in _LIVE_PROJECTS


#: Extracts (hostname, port-or-"") from an HTTP `Host` header or an `Origin`'s netloc — two
#: alternatives, bracketed-IPv6 first (`[::1]`, `[::1]:8000`) then plain (`localhost`,
#: `localhost:8000`, `127.0.0.1:8000`). An UNBRACKETED `::1` deliberately does NOT match either
#: branch (the plain branch's character class excludes `:`, so it can never consume a literal
#: containing one) and so is treated as non-loopback — fail-safe, not a gap: a real browser always
#: brackets an IPv6 host literal, so this only ever rejects a malformed/synthetic header, never a
#: legitimate one. A second-pass review found this docstring previously claimed "bare `::1`"
#: matched, which was checked directly and is false — corrected here rather than left to confuse a
#: future reader into thinking the regex needs a third branch. Verified by hand against all 9
#: accumulated review test cases, including that it correctly REJECTS
#: `localhost.attacker.example:8000` / `127.0.0.1.attacker.example:8000` (no partial match).
_HOST_RE = re.compile(r"^\[([^\[\]]+)\](:\d+)?$|^([^:\[\]]+)(:\d+)?$")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _authority(host_header: str) -> tuple[str, str]:
    """`(hostname, port)` — `port` is `""` when absent, never `None`, so two absent ports compare
    equal to each other without a caller needing to know that convention."""
    m = _HOST_RE.match(host_header)
    if not m:
        return ("", "")
    hostname = m.group(1) or m.group(3) or ""
    port = (m.group(2) or m.group(4) or "")[1:]  # strip the leading ":"
    return (hostname, port)


def _host_is_loopback(request) -> bool:
    """Defends against DNS rebinding — and this is the part a first draft got backwards. A rebinding
    attack makes the victim's BROWSER open a genuinely loopback TCP connection (attacker DNS resolves
    `rebind.attacker.example` to `127.0.0.1`), while the `Host`/`Origin` HEADERS the browser sends
    still name `rebind.attacker.example` (a browser sends the hostname it navigated to, never the
    resolved IP). So checking `request.client.host` ALONE — or falling back to the `Host` header only
    when the peer isn't loopback — is exactly backwards: it treats the rebound case, where the peer
    genuinely IS loopback, as automatically trusted. The only sound check is BOTH conditions holding
    together: the actual TCP peer is loopback (a header can't spoof `request.client.host`) AND the
    `Host` header the browser sent also NAMES a loopback host. A rebound request fails the second
    half every time, because the browser never stops sending the attacker's hostname."""
    client_host = request.client.host if request.client else None
    if client_host not in _LOOPBACK_HOSTS:
        return False
    hostname, _ = _authority(request.headers.get("host", ""))
    return hostname in _LOOPBACK_HOSTS


def _same_origin_or_absent(request) -> bool:
    """A same-origin CSRF floor: when `Origin` is present, its FULL authority (hostname AND port)
    must match the request's own `Host` header. Comparing hostname alone — a first draft's mistake —
    would treat every port on `localhost`/`127.0.0.1` as mutually trusted, which is exactly the
    threat model this studio runs in: any OTHER local dev server or app bound to a different port on
    the same loopback address would then count as "same origin" for CSRF purposes. An ABSENT `Origin`
    passes (a same-origin fetch sometimes omits it, and so does every non-browser client) — which is
    exactly why this check alone is not sufficient and `_host_is_loopback` above is REQUIRED
    alongside it, never as an alternative (it defends against rebinding; this defends CSRF)."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    origin_authority = (parsed.hostname or "", str(parsed.port) if parsed.port is not None else "")
    return origin_authority == _authority(request.headers.get("host", ""))


def _require_loopback_and_same_origin(request) -> None:
    if not _host_is_loopback(request):
        raise HTTPException(403, "live mode is only reachable over a loopback connection")
    if not _same_origin_or_absent(request):
        raise HTTPException(403, "cross-origin request refused")


def _load_trace(run_id: str) -> list[dict]:
    """Read `{run_id}`'s trace file. 404 if it doesn't exist; 502 (never 500) on a genuinely
    external failure — an unreadable file or a corrupted JSONL line — mirroring `assemble`'s own
    "none of them raise" discipline all the way out to the HTTP layer.

    FIXED per adversarial review, then DE-DUPLICATED: `rlm_harness.trace.load_events` does NO shape
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


def _refuse_if_still_live(run_id: str) -> None:
    """Raise 409 if `run_id` is a live run still in flight. Reading its trace mid-write would show a
    truncated, ever-changing snapshot a client would misread as final — applied to every SINGLE-id
    trace-reading endpoint (`stream_run`, `get_run`, `get_iterations`). `list_runs` is a LISTING
    endpoint, not a single-id one, so it gets a `live` key alongside `runs` instead of a 409 here."""
    if _slug_id(run_id) in _WORKERS:
        raise HTTPException(409, f"run {run_id!r} is still in progress")


def _run_payload_core(run_id: str) -> dict | None:
    """The body of `GET /v1/runs/{run_id}`, minus the HTTP framing: returns `None` exactly where
    that route would 404 (no trace file for `run_id`), and RAISES `OSError`/`ValueError` exactly
    where it would 502 (an existing-but-unreadable/corrupted trace) — `get_run` below is now a thin
    wrapper translating both into the right status code. This split exists so `live.run_live`'s
    `on_done` (running in a worker thread, no HTTP request in flight) can call the identical logic
    directly: it imports this function BY NAME (`from .app import _run_payload_core`) rather than
    reimplementing any of it, per CLAUDE.md invariant 11's "one implementation per job" — the trace
    a live run just finished writing is read back through the SAME code that serves a replay read of
    any other run, so the two can never quietly diverge in what they consider the plan to be."""
    path = _trace_path(run_id)
    if not path.exists():
        return None
    events = load_trace(str(path))
    plan = plan_from_events(events)
    assembled = assemble(events, plan)
    facts = trace_facts(events)
    meta: dict = {}
    for event in events:
        if event.get("type") == "run_start":
            meta = (event.get("payload") or {}).get("meta") or {}
            break
    criteria = [
        {
            "name": getattr(c, "name", ""),
            "category": getattr(c, "category", ""),
            "description": getattr(c, "description", ""),
        }
        for c in rubric_from_meta(events).criteria
    ]
    return {
        "plan": dataclasses.asdict(assembled),
        "rubric_facts": facts,
        "rubric_criteria": criteria,
        "project": _project_label(meta.get("project_dir")),
        "transcript_index": meta.get("transcript_index") or [],
    }


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
    exists to enumerate instead of the trace file (see the module docstring). `live` lists run ids
    currently in flight (a subset that may or may not yet have a trace file at all, e.g. before
    `run_start` is recorded) — a LISTING endpoint gets this key instead of the single-id 409
    `_refuse_if_still_live` raises elsewhere, so the picker can show "(running)" rather than a dead
    end."""
    runs = sorted(p.stem for p in TRACES_DIR.glob("*.jsonl")) if TRACES_DIR.is_dir() else []
    return JSONResponse({"runs": runs, "live": sorted(_WORKERS)})


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str) -> JSONResponse:
    """The assembled plan (re-sourced from the trace, never trusted from the plan's own claim),
    the ATLAS rubric facts for the same run, and the CRITERIA those facts belong to —
    `{"plan": {...}, "rubric_facts": {...}, "rubric_criteria": [...]}`. 404 when the trace file
    doesn't exist; 409 while the run is still LIVE (`_refuse_if_still_live` — reading it mid-write
    would show a truncated, ever-changing snapshot); NEVER 500 on a malformed-but-readable trace.

    `rubric_criteria` carries each criterion's own `description`, recovered from the run's
    `run_start` meta by `rubric.rubric_from_meta`. The console renders it as the note under each
    module heading, which is what makes a bare `min_draft_step: 1` legible to a reviewer who did
    not write the rubric. The alternative — four descriptions copied into `app.js` — is the
    duplication invariant 11 exists to stop: it would drift the moment a criterion is reworded,
    and silently, because nothing compares the two. Recovered PER RUN rather than from
    `default_rubric()`, so an OLD trace renders the rubric it actually ran under, not today's.

    The actual body now lives in `_run_payload_core`, shared with `live.run_live`'s own completion
    handling (CLAUDE.md invariant 11) — this is a thin wrapper translating its two failure shapes
    (`None` / a raised `OSError`/`ValueError`) into the right HTTP status.
    """
    _refuse_if_still_live(run_id)
    try:
        payload = _run_payload_core(run_id)
    except (OSError, ValueError) as exc:  # a half-written / corrupted trace file, not a code bug
        raise HTTPException(502, f"trace file for run {run_id!r} is unreadable: {exc}") from exc
    if payload is None:
        raise HTTPException(404, f"no trace for run {run_id!r}")
    return JSONResponse(payload)


@app.get("/v1/runs/{run_id}/iterations")
def get_iterations(run_id: str) -> JSONResponse:
    """The Trajectory drawer's data: the run's REPL turns, its tool/sub-LM timeline, and its initial
    state — `iterations.build_iterations` over the same events every other endpoint reads.

    Deliberately routed through `_load_trace` rather than a local reader. All three sibling studios
    still carry a non-dict-line 500 in THEIR `/iterations` path (their loader catches only
    `JSONDecodeError`, so a JSON-valid `42`/`null`/`[1,2,3]` line reaches a `.get(...)` and raises a
    raw `AttributeError`). Going through `_load_trace` inherits `trace_io.load_trace`'s dict-shape
    filter, the 404/502 split `docs/DESIGN.md` §5.6 requires as a UI contract, and `_slug_id`
    sanitization — all three for free, and `iterations.py` itself stays web-dep-free.

    409 while `run_id` is still LIVE — `_refuse_if_still_live`, same reasoning as `get_run`."""
    _refuse_if_still_live(run_id)
    return JSONResponse(build_iterations(_load_trace(run_id)))


@app.get("/v1/runs/{run_id}/events")
async def stream_run(run_id: str, delay: float = 0.0) -> StreamingResponse:
    """Replay the run's trace as SSE, sorted by `step_id` (`_step_key`, matching
    `diff-sentry-studio`'s own ordering caveat: `main_step`s flush post-hoc with trailing step_ids,
    so a replay streams actions before reasoning turns). `delay` (seconds) paces it to feel live.
    If no `distill.run.completed` was ever mapped (a truncated trace — e.g. a hard-killed run whose
    recorder never reached `__exit__`), synthesize one at the end so a client waiting on it doesn't
    hang forever — same reasoning `diff_sentry_studio.app.stream_run` states for its own case.

    409 while `run_id` is still LIVE (`_refuse_if_still_live`) — a live run's own SSE stream is
    `POST /v1/distill`'s response, not this one; this endpoint replays a FINISHED trace."""
    _refuse_if_still_live(run_id)
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


class _DistillRequest(BaseModel):
    """`POST /v1/distill`'s body. `project_dir` is checked against `_LIVE_PROJECTS` — an
    unauthenticated caller never gets to name an arbitrary directory (CLAUDE.md invariant 10's
    reopening condition (c)). `run_id` is optional; when omitted one is derived the same way the CLI
    derives its default (`cli.default_run_id`), so a live run and a `ctx-distillery distill` run of
    the same project land on a run_id shaped the same way."""

    project_dir: str
    run_id: str | None = None
    include_subagents: bool = False


@app.get("/v1/projects")
def list_projects(request: Request) -> JSONResponse:
    """The configured live-mode project allowlist — feeds the frontend's project `<select>`. 404
    (never an empty list) when `CTXD_LIVE_PROJECTS` is unset, so the frontend can tell "live mode is
    off" apart from "live mode is on but nobody has configured a project yet". Also gated on the
    same loopback/same-origin check the two write routes use — found missing by adversarial review:
    without it, once live mode is on, ANY reachable client (not just the browser sitting at this
    machine) could read the operator's absolute local project paths."""
    if not _LIVE_PROJECTS:
        raise HTTPException(404, "live mode is not enabled (CTXD_LIVE_PROJECTS is unset)")
    _require_loopback_and_same_origin(request)
    return JSONResponse({"projects": [str(p) for p in _LIVE_PROJECTS]})


@app.post("/v1/distill")
async def distill(body: _DistillRequest, request: Request) -> StreamingResponse:
    """Start a LIVE distillation and stream it as SSE, in the exact `distill.*` vocabulary replay
    already produces — `live.trace_event_sink` reuses `mapper.to_event` UNCHANGED, so the frontend's
    existing replay event handlers apply to a live run with no branching. Plus one genuinely NEW
    event, `distill.run.started`, carrying `run_id` before `run_start` is even recorded (a client
    needs the id immediately — e.g. to enable its Cancel button — and every other event is keyed off
    a trace that does not exist yet at that point).

    404 when live mode is off; 403 when the request fails either the loopback or the same-origin
    check (`_require_loopback_and_same_origin` — both required together, see that function); 400
    when `project_dir` is not in `CTXD_LIVE_PROJECTS`, or the derived `run_id` collides with a run
    already in flight or an existing trace file.

    `default_run_id` is imported LAZILY, inside this function's own body, never at module top:
    `ctx_distillery.cli` (and transitively `.session`/`.task`) pulls `dspy` into `sys.modules`
    immediately on import — confirmed directly against this repo — and this module's whole point is
    staying importable at zero cost when live mode is off, a guarantee that cannot depend on this
    route handler never having run yet.

    Never calls `ctx_distillery.apply.apply_plan` — applying a plan drawn from a live run is the
    same separate, human-invoked action it always was for a replayed one."""
    if not _LIVE_PROJECTS:
        raise HTTPException(404, "live mode is not enabled (CTXD_LIVE_PROJECTS is unset)")
    _require_loopback_and_same_origin(request)

    raw_project_dir = Path(body.project_dir).expanduser()
    if not _project_in_allowlist(raw_project_dir):
        raise HTTPException(400, f"project_dir {body.project_dir!r} is not in CTXD_LIVE_PROJECTS")
    project_dir = raw_project_dir.resolve()

    from ctx_distillery.cli import default_run_id

    run_id = _slug_id(body.run_id) if body.run_id else _slug_id(default_run_id(project_dir))
    trace_path = _trace_path(run_id)

    # Synchronous check-and-reserve — no `await` between the membership check and the `.add` below —
    # is what closes the collision race two concurrent POSTs for the same run_id would otherwise
    # leave open (both passing the check before either reserves).
    if run_id in _RESERVED_RUN_IDS or run_id in _WORKERS or trace_path.exists():
        raise HTTPException(400, f"run {run_id!r} already exists or is in progress")
    _RESERVED_RUN_IDS.add(run_id)

    cancel_event = threading.Event()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _done_marker = object()

    def sink(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def on_done(payload: dict) -> None:
        # The ONLY mutator of these three registries besides `distill()`'s own reservation above —
        # always a same-key pop/discard, called from the run's worker thread.
        _CANCELS.pop(run_id, None)
        _WORKERS.pop(run_id, None)
        _RESERVED_RUN_IDS.discard(run_id)
        loop.call_soon_threadsafe(
            queue.put_nowait, {"_done": True, "cancelled": bool(payload.get("cancelled"))}
        )

    def worker() -> None:
        run_live(
            project_dir,
            run_id,
            trace_path,
            sink,
            on_done,
            cancel_event=cancel_event,
            include_subagents=body.include_subagents,
        )

    _CANCELS[run_id] = cancel_event
    thread = threading.Thread(target=worker, name=f"ctxd-live-{run_id}", daemon=True)
    _WORKERS[run_id] = thread
    thread.start()

    async def gen():
        yield _sse("distill.run.started", {"run_id": run_id})
        saw_completed = False
        while True:
            item = await queue.get()
            if isinstance(item, dict) and item.get("_done"):
                # Mirrors `stream_run`'s own synthesized-terminal-event fallback: a run that failed
                # before any `run_end` was ever recorded (e.g. `setup()` itself raised) never gets a
                # `distill.run.completed` out of `trace_event_sink`, and a client waiting on it must
                # not hang forever.
                if not saw_completed:
                    yield _sse("distill.run.completed", {"cancelled": item.get("cancelled", False)})
                break
            if item.get("event") == "distill.run.completed":
                saw_completed = True
            yield _sse(item["event"], item["data"])

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> JSONResponse:
    """Cooperatively cancel an in-flight live run: sets its `cancel_event`, which rlm-harness's own
    sandbox watchdog (or the run's next LM-call await) observes and unwinds from as a
    `SandboxCancelled` — see `live.py`. This is a REQUEST to stop, not a guarantee of immediate
    effect, so the response is `{"cancelling": true}` rather than `{"cancelled": true}`; the run's
    own SSE stream (`POST /v1/distill`'s response) is what tells the client when it actually ends.

    404 when live mode is off, or when `run_id` is not currently a live run (already finished, or
    never started) — same reasoning as `_refuse_if_still_live`, inverted: nothing to cancel is not
    an error a client should retry. 403 on a failed loopback/same-origin check, same as `distill`."""
    if not _LIVE_PROJECTS:
        raise HTTPException(404, "live mode is not enabled (CTXD_LIVE_PROJECTS is unset)")
    _require_loopback_and_same_origin(request)
    slug = _slug_id(run_id)
    ev = _CANCELS.get(slug)
    if ev is None:
        raise HTTPException(404, f"run {run_id!r} is not currently live")
    ev.set()
    return JSONResponse({"cancelling": True})
