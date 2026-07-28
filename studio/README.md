# ctx-distillery-studio

A replay-only SSE server + zero-build web frontend for [`ctx-distillery`](../README.md) — a separate
uv workspace member, not a subpackage of `ctx_distillery` itself.

## What this is for

`ctx-distillery`'s `DistillSession` proposes a plan (prune / promote_to_memory / promote_to_skill /
keep) over one or more session transcripts + the current memory/skill index, and writes NOTHING
itself (`run_distillation` returns an in-memory `AssembledPlan`; there is no `responses/{run_id}.json`
or similar artifact anywhere). So this studio's sole source of truth, for both discovery and replay,
is the trace file the run's `TraceRecorder` already wrote (`{TRACES_DIR}/{run_id}.jsonl`) — a human
reviewer opens it to see, for a past run: the live feed (planner reasoning turns, any recursive
sub-LM escalation, evidence reads, drafting calls), and — the money shot — each promotion candidate's
verbatim drafted text rendered right next to its plan entry, before deciding whether to call
`apply_plan` themselves, by hand, outside this studio entirely.

## Scope: replay-only, v1

There is no live-drive endpoint (no `POST /v1/distill` or similar). `run_distillation` needs a
caller-supplied `HarnessAdapter` + `chat_fn` already wired (real Claude Code storage + a real model)
— a materially heavier precondition than a self-contained one-shot driver a web request could
reasonably own end-to-end. Replay already delivers the money shot above from traces a caller already
produced by calling `run_distillation` themselves. This studio is READ-ONLY of the trace file and
NEVER calls `ctx_distillery.apply.apply_plan` — applying a plan stays a separate, human-invoked,
outside-any-web-request action.

## Endpoints

| method + path | what it does |
|---|---|
| `GET /` | serves the frontend shell |
| `GET /v1/config` | `{"traces_dir": ...}` — the one thing that genuinely varies by deployment |
| `GET /v1/runs` | discovers run ids by globbing `{TRACES_DIR}/*.jsonl`, sorted |
| `GET /v1/runs/{run_id}` | the assembled plan (`ctx_distillery.session.assemble`) + ATLAS rubric facts (`ctx_distillery.rubric.trace_facts`), re-derived from the trace — never trusted from the plan's own claim |
| `GET /v1/runs/{run_id}/events` | SSE replay of the trace, mapped through `mapper.to_event` to a stable `distill.*` event vocabulary, paced by an optional `?delay=` |

`run_id` is sanitized (`_slug_id`) before it ever becomes a path component — a studio reachable over
HTTP must not open a path-traversal hole on itself just because this project's own trace files are
normally trusted.

## Frontend

Zero-build vanilla JS/CSS (`static/index.html` / `app.js` / `style.css`), no bundler, no
`node_modules`: a Load box (`GET /v1/runs` feeds a `<datalist>`), a live SSE feed panel (including the
planner's own reasoning turns and any sub-LM escalation), the PLAN panel — one row per candidate, its
`action`/`key_fields` next to its `draft`, rendered via `el.textContent = draft` **only** (never
`innerHTML` — a drafted memory/skill body is untrusted model output, not markup to render), and a
Rubric panel listing `rubric_facts` per ATLAS category. A `problems`-carrying candidate is visually
flagged, never silently dropped.

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

## Run it

```sh
CTXD_TRACES_DIR=./traces uvicorn ctx_distillery_studio.app:app --reload
```

Point `CTXD_TRACES_DIR` at wherever `run_distillation`'s `trace_path` argument actually wrote —
default is `<repo-root>/traces` (mirroring the `DS_ARTIFACTS_DIR` override convention `diff-sentry`'s
studio uses).
