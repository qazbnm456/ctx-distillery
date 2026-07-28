# ctx-distillery-studio

A replay-only SSE server + zero-build web frontend for [`ctx-distillery`](../README.md) — a separate
uv workspace member, not a subpackage of `ctx_distillery` itself.

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

## Scope: replay-only, v1

There is no live-drive endpoint (no `POST /v1/distill` or similar). This studio is READ-ONLY of the
trace file and NEVER calls `ctx_distillery.apply.apply_plan` — applying a plan stays a separate,
human-invoked, outside-any-web-request action.

**The reason used to be "the preconditions are too heavy", and that reason is now false.** It said
`run_distillation` needs a caller-supplied `HarnessAdapter` + `chat_fn` already wired, unlike a
self-contained one-shot driver a web request could own end-to-end. `ctx_distillery/cli.py`'s
`_cmd_distill` **is** that driver: it assembles the whole precondition from the `CD_*` environment
(discovery -> `setup()` -> `make_chat_fn` -> `run_distillation`). It is not trivial — roughly 55
lines with five distinct failure paths — but "a self-contained driver does not exist" stopped being
true the moment the CLI landed, so the old wording is retired rather than restated.

The endpoint is still DECLINED, on three reasons that survive the CLI:

1. **There is no cancel seam.** A distillation is a multi-minute, up-to-30-turn sandboxed episode.
   `run_distillation` has no `cancel_event` parameter, and neither does anything in `rlm-kit`. So an
   HTTP-initiated run could only be hung or SIGKILLed, leaving a truncated trace this studio then
   papers over with its synthesized terminal `distill.run.completed` — a papering-over that is
   correct for a run someone else killed and dishonest for one this server started and abandoned.
   `diff-sentry` and `toolscout` ship live endpoints without cancel because their operations are
   SHORT (a classification is one short change). `cve-reverser`, whose runtime profile matches ours,
   is the one sibling that needed cancel — and it cost a threaded `cancel_event`, a cancel route, a
   dedicated `shutdown.py` plus its own tests, and SIGINT/SIGTERM wrapping to defeat a real uvicorn
   deadlock. The honest fix belongs UPSTREAM in `rlm-kit`, which is where `toolscout`'s studio puts
   it too.
2. **The import-level safety valve is unavailable here.** Every sibling gates its live path behind a
   `live = ["<parent>"]` extra, so a replay-only deploy *physically cannot* spend — the driving code
   is not installed. Ours makes `ctx-distillery` a CORE dependency, because replay itself calls
   `schema.assemble` / `rubric.trace_facts`. **State this precisely: it is contingent, not
   structural.** `live = ["openai"]` would not restore the valve either — the planner spends through
   `dspy`/`litellm`, a CORE `rlm-kit` dependency, long before any drafting call reaches an `openai`
   client. Nor does the dspy-free `schema.py` split change it, because `assemble` still ships in the
   same distribution as `run_distillation`. Splitting a package would be the only way to get the
   valve back, and that is out of scope. (What this reason is NOT: a claim that a route would be
   "armed by `CD_ROOT_LM`". Route existence and credential presence are different things, and no
   sibling gates on env either.)
3. **The live input would be a project directory — the strongest of the three.** The siblings' live
   request bodies are self-describing payloads: a pasted change, a `repo` + `number`, a task string.
   Ours would be `project_dir` — an unauthenticated HTTP parameter selecting *whose entire Claude
   Code conversation history* gets rendered and shipped to a remote model. `_slug_id` protects
   `run_id`; there is no analogue for `project_dir`, and the containment defenses in `CLAUDE.md`
   invariants 5 and 6 all assume THE CALLER CHOSE THE PROJECT. Redaction is a filter, not an
   authorization decision.

**The positive case, which is the real answer:** `ctx-distillery distill` writes into
`$CTXD_TRACES_DIR` — the SAME directory this studio globs for `GET /v1/runs`. So `distill` →
refresh → **Load** already delivers everything a live endpoint would, from a process that owns its
own credentials and that an operator can Ctrl-C.

**Reopening conditions** (this refusal is falsifiable, not doctrinal). Build the endpoint when all
of these hold: a cancel seam exists in `rlm-kit` and `run_distillation` accepts it; an opt-in gate
makes the route NOT EXIST by default; the drivable project directories come from an allowlist
sourced from the environment, never from the request body; and the deployment has a stated
loopback-bind / auth posture.

## Endpoints

| method + path | what it does |
|---|---|
| `GET /` | serves the frontend shell |
| `GET /v1/config` | `{"traces_dir": ...}` — the one thing that genuinely varies by deployment |
| `GET /v1/runs` | discovers run ids by globbing `{TRACES_DIR}/*.jsonl`, sorted |
| `GET /v1/runs/{run_id}` | the assembled plan (`ctx_distillery.schema.assemble`) + ATLAS rubric facts (`ctx_distillery.rubric.trace_facts`), re-derived from the trace — never trusted from the plan's own claim |
| `GET /v1/runs/{run_id}/events` | SSE replay of the trace, mapped through `mapper.to_event` to a stable `distill.*` event vocabulary, paced by an optional `?delay=` |
| `GET /v1/runs/{run_id}/iterations` | the Trajectory drawer's per-turn breakdown (`iterations.build_iterations`): the run's `initial` state, its REPL turns (reasoning + code + output), and a flat tool/sub-LM `timeline` |

`run_id` is sanitized (`_slug_id`) before it ever becomes a path component — a studio reachable over
HTTP must not open a path-traversal hole on itself just because this project's own trace files are
normally trusted.

## Frontend

Zero-build vanilla JS/CSS (`static/index.html` / `app.js` / `trajectory.js` / `style.css`), no
bundler, no `node_modules`: a Load box (`GET /v1/runs` feeds a `<datalist>`), the **Replay feed**
panel — an SSE re-stream of a FINISHED trace, including the planner's own reasoning turns and any
sub-LM escalation — the PLAN panel — one row per candidate, its `action`/`key_fields` next to its
`draft`, rendered via `el.textContent = draft` **only** (never `innerHTML` — a drafted memory/skill
body is untrusted model output, not markup to render) — and a Rubric panel listing `rubric_facts` per
ATLAS category. A `problems`-carrying candidate is visually flagged, never silently dropped.

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
