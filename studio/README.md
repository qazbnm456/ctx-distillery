# ctx-distillery-studio

An SSE server + zero-build web frontend for [`ctx-distillery`](../README.md) — a separate uv
workspace member, not a subpackage of `ctx_distillery` itself. Replay is always available; a live
drive mode is opt-in (see Scope below).

**This file owns the architecture** (endpoints, the SSE event vocabulary, scope, install/run).
[`DESIGN.md`](DESIGN.md) is the frontend's *visual & UX spec* — theme, palette, typography,
components, states, and eight browser-checkable acceptance items — and owns the look and feel only.

## What this is for

`ctx-distillery`'s `DistillSession` proposes a plan (prune / promote_to_memory / promote_to_skill /
keep) over one or more session transcripts + the current memory/skill index, and writes NOTHING
itself (`run_distillation` returns an in-memory `AssembledPlan`; there is no `responses/{run_id}.json`
or similar artifact anywhere). So this studio's sole source of truth, for both discovery and replay,
is the trace file the run's `TraceRecorder` already wrote (`{TRACES_DIR}/{run_id}.jsonl`) — a human
reviewer opens it to see, for a past run: the replay feed (planner reasoning turns, any recursive
sub-LM escalation, evidence reads, drafting calls), and — the money shot — each promotion candidate's
verbatim drafted text rendered right next to its plan entry, before deciding whether to call
`apply_plan` themselves, by hand, outside this studio entirely.

## Scope: replay + opt-in live

**This studio now has a live-drive endpoint (`POST /v1/distill`), and it is OFF by default.** It
exists only when the operator sets `CTXD_LIVE_PROJECTS` — a comma-separated allowlist of project
directories, resolved once at process start. With it unset, `GET /v1/projects` and `POST
/v1/distill` both 404 and this studio behaves EXACTLY as the replay-only version always did:
READ-ONLY of the trace file, never calling `ctx_distillery.apply.apply_plan` (applying a plan stays
a separate, human-invoked, outside-any-web-request action, live or replayed).

This is a documented reopening of a prior refusal, not a reversal of the reasoning that produced
it — `CLAUDE.md` invariant 10 named four conditions under which the endpoint should exist, and all
four are now met:

1. **A cancel seam exists, upstream, in `rlm-harness`.** `rlm_harness.SandboxCancelled` +
   `RLMTask(cancel_event=...)` reach all the way into the sandbox interpreter's own watchdog thread,
   which can kill a wedged `deno`/`pyodide` process mid-call — the thing `asyncio.Task.cancel()`
   fundamentally cannot do, because the sandbox's blocking read has no `await` inside it. A live run
   here builds a plain `threading.Event` and hands it straight to
   `run_distillation_artifacts(..., cancel_event=...)` (`studio/ctx_distillery_studio/live.py`) —
   ctx-distillery needed zero signature changes of its own for this to work, because `**kw` already
   forwards it to `RLMTask.__init__`.
2. **The route does not exist by default.** `CTXD_LIVE_PROJECTS` unset means `GET /v1/projects` and
   `POST /v1/distill` 404, unconditionally — there is no way to reach either without the operator
   opting in first.
3. **Drivable project directories come from an environment-sourced allowlist, never the request
   body.** `project_dir` in a `POST /v1/distill` body is checked for EXACT membership in
   `CTXD_LIVE_PROJECTS` (never a prefix/substring test); an unauthenticated caller cannot name an
   arbitrary directory.
4. **A stated loopback-bind / auth posture.** Every live-mode route (`GET /v1/projects`, `POST
   /v1/distill`, `POST /v1/runs/{run_id}/cancel`) requires BOTH `_host_is_loopback` AND
   `_same_origin_or_absent`, and each check is itself an AND of two conditions, not an OR —
   a first draft got both wrong the same way, and an adversarial review caught both by direct
   reproduction before this shipped:
   - `_host_is_loopback` requires the request's ACTUAL TCP peer be loopback (`request.client.host`
     — not a spoofable header) **AND** the `Host` header itself name a loopback host. Checking the
     peer alone — or falling back to the header only when the peer isn't loopback — is exactly
     backwards for DNS rebinding: a rebinding attack makes the victim's browser open a genuinely
     loopback connection while its `Host`/`Origin` headers still name the attacker's hostname (a
     browser sends the hostname it navigated to, never the resolved IP), so a peer-only check
     treats the rebound case as automatically trusted.
   - `_same_origin_or_absent` compares the FULL authority — hostname **and** port — of `Origin`
     against `Host`, not hostname alone. Hostname-only would treat every port on
     `localhost`/`127.0.0.1` as mutually trusted, which on a loopback-only threat model means any
     OTHER local dev server or app bound to a different port would count as same-origin.

   Neither of the two top-level checks substitutes for the other, and neither of the two conditions
   inside each substitutes for its sibling. This studio still has no authentication of its own — the
   posture is "loopback-only, run it on your own machine," the same posture the replay-only server
   always had.

**What did NOT change**: no HTTP request may ever call `apply_plan`; a live run's trace lands in the
exact same `$CTXD_TRACES_DIR` a CLI `distill` run would, read back through the exact same
`_run_payload_core` a replay read uses; and while a run is live, every single-id read endpoint
(`GET /v1/runs/{run_id}`, `.../iterations`, `.../events`) refuses with 409 rather than showing a
truncated, ever-changing snapshot — a live run's own progress is watched through `POST
/v1/distill`'s own SSE response, not through the replay endpoints.

## Endpoints

| method + path | what it does |
|---|---|
| `GET /` | serves the frontend shell |
| `GET /v1/config` | `{"traces_dir": ...}` — the one thing that genuinely varies by deployment |
| `GET /v1/projects` | the configured live-mode project allowlist; 404 when `CTXD_LIVE_PROJECTS` is unset |
| `GET /v1/runs` | discovers run ids by globbing `{TRACES_DIR}/*.jsonl`, sorted, plus a `live` list of run ids currently in flight |
| `GET /v1/runs/{run_id}` | the assembled plan (`ctx_distillery.schema.assemble`) + ATLAS rubric facts (`ctx_distillery.rubric.trace_facts`), re-derived from the trace — never trusted from the plan's own claim. 409 while `run_id` is still live. |
| `GET /v1/runs/{run_id}/events` | SSE replay of a FINISHED trace, mapped through `mapper.to_event` to a stable `distill.*` event vocabulary, paced by an optional `?delay=`. 409 while `run_id` is still live — see `POST /v1/distill` for that case. |
| `GET /v1/runs/{run_id}/iterations` | the Trajectory drawer's per-turn breakdown (`iterations.build_iterations`): the run's `initial` state, its REPL turns (reasoning + code + output), and a flat tool/sub-LM `timeline`. 409 while `run_id` is still live. |
| `POST /v1/distill` | opt-in only (404 when `CTXD_LIVE_PROJECTS` is unset). Starts a live distillation over `{"project_dir": ..., "run_id": null, "include_subagents": false}` and streams it as SSE — the SAME `distill.*` vocabulary as replay, plus one live-only event, `distill.run.started`, carrying the resolved `run_id` before anything else exists to key events off of. 403 off-loopback or cross-origin; 400 on a `project_dir` outside the allowlist or a `run_id` collision. |
| `POST /v1/runs/{run_id}/cancel` | cooperatively cancels an in-flight live run (sets its `cancel_event`; rlm-harness's own sandbox watchdog does the rest). 404 if live mode is off or `run_id` isn't currently live; 403 off-loopback or cross-origin. |

`run_id` is sanitized (`_slug_id`) before it ever becomes a path component — a studio reachable over
HTTP must not open a path-traversal hole on itself just because this project's own trace files are
normally trusted. The sanitizer follows `toolscout_studio.app._slug_id`, not `diff_sentry_studio`'s:
it also CAPS the slug at `_RUN_ID_MAX` (120) and re-strips after the cut, so a truncation landing on
a `-`/`.` leaves no trailing separator. A slug becomes one filename component, capped at 255 bytes on
most filesystems, and without the cap a 5000-char `run_id` raised a raw `OSError` (ENAMETOOLONG) out
of `_load_trace` — a 500 where a 404 belongs.

Worth knowing when testing this: Starlette normalises the request path before routing, so
`GET /v1/runs/..%2F..%2Fetc%2Fpasswd` **never reaches `_slug_id`** — its 404 comes from the router.
Assert on `_slug_id` directly, and use request-level cases that survive routing (`%2e%2e`, `a%00b`,
an over-long id).

## Frontend

Zero-build vanilla JS/CSS (`static/index.html` / `app.js` / `trajectory.js` / `style.css`), no
bundler, no `node_modules`: a Load box (`GET /v1/runs` feeds a `<datalist>`), a **Live distill**
panel (hidden unless `GET /v1/projects` succeeds — see Scope above) with a project `<select>`
sourced from that SAME allowlist and a Distill/Cancel pair of buttons, the **Replay feed** panel —
which now renders EITHER a replayed OR a live run through the identical `renderFeedEvent` path,
including the planner's own reasoning turns and any sub-LM escalation — the PLAN panel — one row per
candidate, its `action`/`key_fields` next to its `draft`, rendered via `el.textContent = draft`
**only** (never `innerHTML` — a drafted memory/skill body is untrusted model output, not markup to
render) — and a Rubric panel listing `rubric_facts` per ATLAS category. A `problems`-carrying
candidate is visually flagged, never silently dropped.

A live run's own stream is consumed by hand (`sseFrames` in `app.js`), not via `EventSource`:
`EventSource` cannot POST a request body, and `POST /v1/distill`'s body is how the frontend names
which project/run to start. The wire format and every event's meaning are otherwise identical to
replay's — a `distill.run.completed` frame during a live run additionally carries a `cancelled`
flag the feed status line reflects ("done" vs "cancelled"), which a replayed run's synthesized
fallback event never sets.

Plus the **Trajectory drawer** (`static/trajectory.js`, a `window.Trajectory(deps)` factory over
`GET /v1/runs/{id}/iterations`, opened from a `◫ Trajectory` handle): a turn nav, a detail pane
rendering each turn's reasoning + its REPL `code`/`output`, and a FLAT tool/sub-LM timeline. The
timeline never depends on `turn_index` — that field exists only when `per_turn_timing` is true, which
no offline trace is — so it stays populated on every trace; `turn_index` only drives a cross-highlight
when it is really there. The no-`innerHTML` rule is sharpest here: a turn's `output` is the REPL's own
echo and does carry drafted bodies and evidence, so **rendering it as text IS the mitigation** —
`iterations.py`'s leak tests cover `timeline` and `initial`, not turn text. There are no transport
controls, no progress bar and no expand button; see `DESIGN.md` §5.7 for why each is absent rather
than missing.

The feed panel is labelled **Replay feed** and its status reads `replaying…`. It used to say "Live
feed" / "streaming…", which promised a capability this backend does not have (there is no
live-drive endpoint — see the Scope section); `?delay=` only PACES the replay to feel live. The
ordering caveat is real either way: `main_step` events flush POST-HOC with trailing `step_id`s, so a
replay sorted by `_step_key` streams the run's ACTIONS before the reasoning turns that produced
them. `studio/DESIGN.md` is the frontend's visual & UX spec; this file owns the architecture.

## Install (workspace member)

From the repo root (a `uv` workspace member — see the root `pyproject.toml`'s `[tool.uv.workspace]`):

```sh
uv sync
uv run --directory studio --package ctx-distillery-studio --extra dev pytest
```

(`--package` alone only selects which workspace member's *environment* to use — it does not change
pytest's cwd or which `pyproject.toml`'s `testpaths` gets resolved, so it would silently run the root
package's suite instead; `--directory studio` makes pytest resolve `studio/pyproject.toml`'s own
`testpaths` — the same Phase-1 lesson `eval/README.md` documents, applied here too. See
`.github/workflows/ci.yml`'s `studio-test` job.)

Or, for a plain-pip environment where the workspace `[tool.uv.sources]` reference can't resolve (e.g.
a bare venv, not a `uv` project): install `ctx-distillery` first, then this package in editable mode,
e.g. `pip install -e . -e ./studio` from the repo root.

### Frontend static contracts (node)

`tests/static-contract.test.js` pins the CSS/JS rules that regress silently because nothing in the
Python suite opens a browser — the `[hidden]` guard, the `.layout` viewport-height pin, `word-break`
on every model-supplied field, the draft `<pre>`'s and the drawer well's `overflow-wrap`, the §2
derived-state frame classes, the responsive stack, and the absolute no-`innerHTML` rule, which scans
**every `static/*.js`** (it read only `app.js` until `trajectory.js` existed) and asserts it really
found the files it exists to police. `tests/trajectory.test.js` is the drawer factory's
dependency-injection contract: a stub DOM, the facade shape, a missing injected dep refused at
construction, and proof that `getRunId` is re-consulted rather than snapshotted. Plain CommonJS, run
directly:

```sh
for f in studio/tests/*.test.js; do node "$f"; done
```

No npm, no `package.json`, no `node_modules` — the same runner shape the sibling studios use. CI runs
it in its OWN job (`studio-static`) rather than as a step inside `studio-test`, because `studio-test`
is a 3-version Python matrix and these assertions read files as text, so a step there would run them
three times identically for no signal.

## Run it

```sh
CTXD_TRACES_DIR=./traces uvicorn ctx_distillery_studio.app:app --reload
```

Point `CTXD_TRACES_DIR` at wherever `run_distillation`'s `trace_path` argument actually wrote —
default is `<repo-root>/traces` (mirroring the `DS_ARTIFACTS_DIR` override convention `diff-sentry`'s
studio uses).

To additionally enable live mode, set `CTXD_LIVE_PROJECTS` to a comma-separated list of project
directories a request may drive a distillation over, AND make sure the same `CD_*` environment
`ctx-distillery distill` needs (`CD_ROOT_LM` at minimum — see the root `README.md`) is exported in
this process too, since a live run builds its config the exact same way the CLI does:

```sh
CTXD_TRACES_DIR=./traces CTXD_LIVE_PROJECTS=/Users/you/project-a,/Users/you/project-b \
  CD_ROOT_LM=... uvicorn ctx_distillery_studio.app:app --reload
```

Leave `CTXD_LIVE_PROJECTS` unset for a pure replay deployment — this is the recommended default for
anything other than your own machine, since neither route it gates has any authentication beyond the
loopback/same-origin checks described above.
