# studio/ — agent guide

Nested guide for `ctx-distillery-studio`. Claude Code loads a directory-level `CLAUDE.md` when it
reads files in that directory, so this file costs a session NOTHING unless the session is actually
working in `studio/`.

**Why it exists, so nobody moves it back.** Its contents were carved out of the root `CLAUDE.md`,
which had grown to 868 lines that every session paid for unconditionally — including sessions that
never touch this member. Invariant 10's body (~55 lines) plus three `## Known simplifications`
bullets (~38 lines) apply to `studio/` and to nothing else. The root file keeps a numbered STUB at
invariant 10 carrying the normative one-liner (read-only of the trace, never calls `apply_plan`, no
live-drive endpoint), because that half constrains people editing `apply.py` too. The argument, the
history, and the frontend rules live here.

**The number 10 is load-bearing and was NOT reassigned.** ~20 places across this member's code,
tests, CSS and the root package cite "invariant 10" by number (plus 13 in `CHANGELOG.md`), so the
root list still runs 1–12 with 10 as a stub. Never renumber to close the gap.

The text below is moved VERBATIM — only indentation changed and the three `##` headings added.
Corrections belong here now; do not re-add a second copy to the root file.

## Invariant 10 — read-only of the trace, unreachable from the RLM

**`studio/` (`ctx-distillery-studio`) is READ-ONLY of the trace file and unreachable from the
RLM path — it is a THIRD workspace member, never a fork of the harness.** It replays a finished
`DistillSession` run's trace/v1 JSONL file (`plan_from_events` -> `session.assemble` ->
`rubric.trace_facts`, via `GET /v1/runs/{run_id}` and an SSE `GET /v1/runs/{run_id}/events`) and
NEVER calls `ctx_distillery.apply.apply_plan` — applying a plan stays a separate, human-invoked
action outside any web request, exactly as invariant 8 already requires.

### Why there is no live-drive endpoint

There is no live-drive endpoint (no `POST /v1/distill` or similar). **The old reason —
"`run_distillation` needs a caller-supplied `HarnessAdapter` + `chat_fn` already wired, a materially
heavier precondition than a self-contained driver a web request could own" — is FALSE now and must
not be restated**: `cli.py::_cmd_distill` IS that driver (it assembles the whole precondition from
the `CD_*` env; ~55 lines, five failure paths). Three reasons survive the CLI, and
`studio/README.md` §Scope holds the full argument: (a) **no cancel seam** — a distillation is a
multi-minute, up-to-30-turn sandboxed episode and neither `run_distillation` nor anything in
rlm-kit takes a `cancel_event`, so an HTTP-started run could only be hung or SIGKILLed, leaving
exactly the truncated trace this studio papers over with its synthesized terminal event (the
fix belongs upstream in rlm-kit); (b) **the import-level `live`-extra valve is unavailable**
because replay itself needs `assemble`, which ships in the same distribution as the driver —
contingent, not structural, and NOT restorable by `live = ["openai"]` (the planner spends
through dspy/litellm, a core rlm-kit dep) nor by the `schema.py` split; splitting a package is
the only route and is out of scope; (c) **the live input would be `project_dir`** — an
unauthenticated HTTP parameter selecting whose ENTIRE Claude Code history is rendered and
shipped to a remote model, with no `_slug_id` analogue, and invariants 5/6's defenses all assume
the caller chose the project. Redaction is a filter, not an authorization decision; (c) is the
strongest. The positive case: `ctx-distillery distill` writes into `$CTXD_TRACES_DIR`, the SAME
directory this studio globs, so `distill` -> refresh -> Load already delivers what a live
endpoint would, from a process that owns its own credentials. Reopening conditions (the refusal
is falsifiable): a cancel seam in rlm-kit; an opt-in gate that makes the route not exist by
default; an allowlist of drivable project dirs sourced from the ENVIRONMENT, never the request
body; and a stated loopback-bind/auth posture.

### `run_id` sanitization

`run_id` is sanitized (`_slug_id`) before it ever becomes a path component — **and that sanitizer
follows `toolscout_studio.app._slug_id`, NOT `diff_sentry_studio`'s**: it CAPS at `_RUN_ID_MAX` (120)
and re-strips after the cut, so a truncation landing on a `-`/`.` never leaves a trailing
separator. Its docstring used to say "copied verbatim from `diff_sentry_studio.app._slug_id`",
which was TRUE and was exactly the bug — diff-sentry has no cap either, so we inherited the gap
by copying the older sibling. A slug becomes ONE filename component (255 bytes on most
filesystems): reproduced before fixing, `GET /v1/runs/<5000 chars>` raised a raw `OSError`
(ENAMETOOLONG) out of `_load_trace`'s `path.exists()` — a 500 where a 404 belongs, the one hole
left in this module's "never raise on a bad run_id" contract. `eval/cli.py::_slug` carries the
same cap (`_TASK_ID_MAX`) for the same reason on the WRITE side: a task id becomes a trace
FILENAME there. Note what a path-traversal TEST here can and cannot prove: Starlette normalises
the request path before routing, so `GET /v1/runs/..%2F..%2Fetc%2Fpasswd` never reaches
`_slug_id` at all (its 404 comes from the router, and would still be a 404 with `_slug_id`
deleted) — instrumented and confirmed. Assert on `_slug_id` DIRECTLY, and pick request-level
cases that actually survive routing (`%2e%2e`, `a%00b`, an over-long id do; the traversal-shaped
one does not).

### Rendering, workspace membership, and the trace-shape guard

The PLAN panel renders a candidate's `draft` via `el.textContent` **only** — never `innerHTML` —
because a drafted memory/skill body is untrusted model output, not markup to render. Root
`pyproject.toml`'s `[tool.uv.workspace] members` includes `"studio"` alongside `"eval"`.
**`_load_trace` reads through `ctx_distillery.trace_io.load_trace`, which filters to dict-shaped
events ONLY before anything downstream sees them** — found by an adversarial review post-merge:
`rlm_kit.trace.load_events` does NO shape validation, so a JSON-valid non-dict line (`42`, `null`,
`[1,2,3]`) used to reach `plan_from_events`/`trace_facts`/`mapper.to_event`'s `.get(...)` calls and
raise a raw `AttributeError` — a genuine 500, not the "never raise on a malformed trace" guarantee
this invariant claims. The filter first lived INLINE here; it moved into `trace_io` when `eval/`
turned out to need the identical guard (see invariant 11) — a de-duplication, never a removal.
`_load_trace` is still the ONE entry point every endpoint's events pass through, which is what
lets `_step_key`/`mapper.to_event` stay unguarded. Don't remove this filter thinking it's
redundant with `plan_from_events`'s own `ValidationError` handling — that catches a DIFFERENT
failure mode (a well-formed dict with the wrong shape), not a non-dict line at all.
`studio/tests/test_boundary.py` pins the "never calls `apply_plan`" half of this invariant
(statically, via `ast`, so the `__init__` docstring that NAMES `apply_plan` while promising
never to call it isn't itself flagged).

## Known simplifications — studio (stated, not hidden)

- **`studio/`'s frontend does not vendor a JetBrains Mono binary**, unlike the literal
  `diff-sentry-studio` precedent it otherwise mirrors. `static/style.css`'s `--mono` font stack
  PREFERS `"JetBrains Mono"` (matching the sibling studios' visual family when the visitor's system
  already has it installed) and falls back to the platform's own monospace stack otherwise — a
  stated simplification to avoid checking a font binary into a brand-new package, not an attempt to
  literally copy every asset of the cloned reference. (`static/vendor/` does not exist.) Two more
  deliberate divergences live in `studio/DESIGN.md`: the type is MONO-ONLY with no sans-prose split
  (this console's "prose" is a drafted memory/skill file — frontmatter and markdown structure a
  reviewer is checking, not paragraphs they are reading), and the replay TRANSPORT is not built —
  no `replay-core.js`, no play/pause/speed. That one is argued, not deferred by default: the
  siblings' transport animates a walk through data `iterations.py` already renders as static
  numbers, its payoff scales with tool-call count (a ctx-distillery run makes a handful), and this
  studio has never even used the `?delay=` pacing its own server already offers. ←/→ stop-stepping
  is inlined instead. The Trajectory drawer ITSELF is now BUILT (`studio/ctx_distillery_studio/
  iterations.py`, `GET /v1/runs/{run_id}/iterations`, `static/trajectory.js`) — this bullet used to
  say it did not exist, which went false the moment the endpoint landed.
- **The drawer's TURN TEXT is not scrubbed, and cannot be — `textContent` rendering IS the
  mitigation, not a stylistic preference.** `iterations.py`'s `timeline` and `initial` are
  allowlist-shaped and verified clean on a real live trace (no `resolved_path`, no `note`, no
  drafted body, no `evidence`, no `/`-leading string). But `iterations[*].code` and `.output` carry
  the planner's own REPL echo — measured on that same trace: 4 of 6 drafted bodies and ALL 6
  evidence blobs appear there, because the planner printed a drafting tool's return value and typed
  its evidence as a literal. That is inherent to showing turns at all, which is the drawer's whole
  reason to exist (`mapper.to_event` gives `has_code: bool` and drops `output`, so this is genuinely
  new information). So: never read the leak tests as a promise that turn text is clean, and never
  let a node in that pane be built with anything but `textContent`.
  `studio/tests/static-contract.test.js` scans EVERY `static/*.js` for markup sinks — it used to
  read `app.js` only, which a new `trajectory.js` would have sailed straight past.
- **`studio/DESIGN.md` is a VISUAL & UX spec, not an architecture doc, and that division is the
  point.** All three siblings' studio design docs open the same way — architecture is locked in the
  README, the design doc owns the look and feel only — so writing one does NOT reintroduce the
  project-level blueprint this repo deliberately purged. Endpoints, the SSE mapping, scope, and
  install/run stay in `studio/README.md`; theme/palette/typography/components/states/acceptance stay
  in `studio/DESIGN.md`. Its §2 is this project's own signature (invariant 2 made visual: the plan's
  `artifact_id` CLAIM vs. the drafted BYTES), and its `blocked` frame state mirrors
  `apply.py::_blocking_problem` exactly — if that function's refusal set changes, the frame,
  `app.js`'s `applyBlocker()`, and §2's table move together or the console starts lying about what
  the apply step will accept.

## Verify

`make studio-test` (this member's Python suite) and `make static-test` (the frontend static
contracts) from the repo root; `make check` runs both alongside everything else. The raw commands
and why `--directory` is load-bearing are in the root `CLAUDE.md ## Verify`.
