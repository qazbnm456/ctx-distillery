# ctx-distillery — agent guide

`ctx-distillery` is a downstream consumer of [`rlm-kit`](https://github.com/qazbnm456/rlm-kit):
an RLM-driven planner that reads AI coding-agent session transcripts + a persistent memory
store and proposes a distillation plan (what to prune, what to merge across sessions, what to
promote into a memory file or a Skill file). It never applies anything itself. See
`README.md` for the overview and `ctx_distillery/README.md` for the package-level guide.

`rlm-kit` is pinned as a git dependency (see `pyproject.toml`). For local co-development against
an in-progress rlm-kit checkout, install it editable over the top:

```
uv pip install -e ../rlm-kit
```

## Verify

Run BOTH before pushing — the suite is fully offline (no live model, no Deno, no network):

- `uvx ruff@0.16.0 check .` — lint (line-length 110, matching rlm-kit's config). **The version pin is
  deliberate and CI carries the same one.** An unpinned `uvx ruff check .` resolves the LATEST ruff at
  run time, so a release that widens the DEFAULT rule set turns the job red with nobody having touched
  a line of code — ruff 0.16's expansion did exactly that to two sibling projects (256 and 224 fresh
  errors, same pyproject, same source). Bump the pin on purpose, and land the resulting fixes in the
  SAME commit as the bump. A bare `ruff` is not
  installed in this workspace; `uvx` is how CI runs it too.
- `pytest -q` — the whole suite. The dspy-bearing tests (`test_task.py`, `test_session.py`)
  drive a REAL `dspy.RLM.aforward` through `rlm_kit.testing.ScriptedInterpreter` +
  `scripted_lm`, so the planner → tools → SUBMIT chain executes (each tool's own tracing runs)
  at zero cost; they `importorskip("dspy")`.
- `tests/test_no_write_capability.py` is the tripwire for invariant (1): a static scan over
  every module under `ctx_distillery/` — except the deliberate, human-gated `apply.py` — asserting
  none contains a write/delete call. If it goes red, someone added a writer — that is the finding,
  not a test to relax, and `apply.py` is not a precedent for a second exemption.
- `tests/test_apply.py` needs no dspy, no rlm-kit model wiring, and no network: applying a plan is
  plain host-side file I/O, so it runs against real files under `tmp_path`.
- A LIVE run additionally needs real credentials and a Deno/pyodide sandbox
  (`brew install deno`). Don't do it in CI; it costs money.
- The `eval/` and `studio/` workspace members each carry their OWN test suite and must be run
  separately — they are not collected by a bare root `pytest -q` (each has its own
  `pyproject.toml` `testpaths`; `--directory eval`/`--directory studio` is what makes `uv run`
  resolve the RIGHT `testpaths` — `--package` alone does not, an earlier Phase-1 fix). `uv run
  --directory eval --package ctx-distillery-eval --extra dev python -m pytest` / `uv run
  --directory studio --package ctx-distillery-studio --extra dev python -m pytest` (matching
  `.github/workflows/ci.yml`'s `eval-test`/`studio-test` jobs). **`--extra dev` is added for
  explicitness, not because it's load-bearing — corrected per adversarial review, which found an
  earlier draft's claim that omitting it breaks the job was FALSE**: this is a `uv` workspace,
  which shares ONE venv across all members, and the ROOT `pyproject.toml`'s `[dependency-groups]
  dev = ["pytest>=8.0"]` already installs pytest into that shared venv on every `uv sync`,
  regardless of which member a given `uv run --package` is scoped to. In a plain-pip environment
  (no `uv`), install each member editable instead: `pip install -e . -e ./eval -e ./studio` from
  the repo root, then run `pytest` from inside each member's own directory.
- `for f in studio/tests/*.test.js; do node "$f"; done` — the studio's frontend static contracts.
  Plain CommonJS, no npm/`package.json`/`node_modules`; `studio/tests/static-contract.test.js` reads
  `static/style.css` and `static/app.js` as TEXT and pins the rules nothing in the Python suite can
  see (the `[hidden]` guard, the `.layout` viewport-height pin, `word-break` on every model-supplied
  field, the draft `<pre>`'s `overflow-wrap`, the `DESIGN.md` §2 derived-state frame classes, the
  responsive stack, and the absolute no-`innerHTML` rule). CI runs it in its OWN `studio-static` job,
  NOT as a step in the 3-version `studio-test` matrix, where it would run three times identically.
- `tests/test_public_api.py` gates the package's public surface: `import ctx_distillery` must stay
  dspy-free (checked in a FRESH subprocess — `sys.modules` in the pytest process is polluted by the
  dspy-bearing tests), every `__all__` name must resolve, `__version__` must match `pyproject.toml`,
  and the writer must be absent (see invariant 8 and the `## Versioning` section below).
- `tests/test_subscription.py` runs WITHOUT the `[subscription]` extra installed: the drafter-hazard
  tests touch only the dspy-free `DistillConfig.from_env`, and the router tests monkeypatch
  `rlm_kit.ClaudeAgentLM`. One sharp edge, recorded because it is easy to get wrong —
  `monkeypatch.setattr(rlm_kit, "ClaudeAgentLM", ...)` does a `getattr` FIRST, which trips rlm-kit's
  package `__getattr__` and pulls dspy into the test process. Fine in a test (they
  `importorskip("dspy")`); never let it become a module-level import.
- **`pytest-asyncio` / `asyncio_mode = "auto"` are DECLINED, deliberately** — all three sibling
  projects carry them, this one does not, and that is a decision rather than an oversight. All three
  of this repo's suites contain ZERO async tests: no `async def test_*`, no `@pytest.mark.asyncio`.
  The four async call sites (`run_distillation` and friends) are driven from synchronous tests
  through an explicit `asyncio.run(...)`, and the only `async def` anywhere in the suites is a
  nested, underscore-prefixed fake pytest never collects. Adding the plugin would change no
  behaviour and buy a dev dependency with no consumer. Reopen it when the first genuinely async
  test needs writing — not for symmetry with the siblings.

## Running — always through the CLI

- **Drive runs via `cli` (`distill` / `show`), never an ad-hoc script.** `ctx-distillery distill
  [project]` is THE entry point: it discovers the project's Claude Code storage
  (`ClaudeCodeAdapter.for_project`), wires the `chat_fn` from `CD_*` (`config.DistillConfig.from_env`
  → `config.setup` → `config.make_chat_fn`), runs `session.run_distillation`, and prints the
  assembled plan. It records `<trace-dir>/<run-id>.jsonl` and writes NOTHING else — there is no
  `responses/` artifact in this project. Don't drive `run_distillation` / `assemble` from a private
  script; extend `cli.py`. Offline re-read: `ctx-distillery show <trace> [--run-id ID] [--json]`.
- **`show` has no `--out`, and `distill` never deletes a stale trace.** Both fall out of invariant 1:
  `cli.py` is inside `tests/test_no_write_capability.py`'s mutation scan, so it may not open a file
  for writing (redirect with `>`) and may not `os.remove` a trace the way the sibling projects' `run()`
  does. `TraceRecorder` appends, so the default `--run-id` is `<project>-<UTC timestamp>` and a run
  whose trace file already exists is REFUSED. Never add a `--force` that deletes one.
- **Applying is a SECOND binary: `ctx-distillery-apply` (`apply.py:main`).** See invariant 8 — this
  is structural, not stylistic. `ctx-distillery-apply <trace> --project <dir> --approve 0,3` is a DRY
  RUN; `--confirm` is what writes. `--allow-skill-scope` defaults to `project` only; installing into
  `~/.claude/skills` needs `--allow-skill-scope global`.

## Invariants — do not break

These are the hard constraints this project is built against; they exist because the operation
this project reasons about (pruning/deleting a user's own history) is irreversible.

1. **No tool ever writes or deletes anything, and the interpreter stays pinned to `pyodide`.**
   Both halves matter: never add a tool that can `open(..., "w")`, delete, or otherwise mutate a
   transcript or memory/skill file — the read-only tool set (`list_memory_files`,
   `read_memory_file`, `read_transcript_chunk`, `draft_memory_file`, `draft_skill_file`) is
   closed, not a starting point to extend with a writer. And never switch the sandbox off the
   explicitly-pinned `pyodide` interpreter — that pin is stated in the task, not left to the
   default, because the "no mutation" guarantee depends on never routing through a
   writable-mount config. Together these make "propose, never apply" a structural property of
   the sandbox, not a convention the planner could be prompted around. The pin is ENFORCED IN
   CODE, not documented: `task._forced_config` runs `dataclasses.replace(config,
   interpreter="pyodide")` before `super().__init__`, so a caller passing `interpreter="local"`
   still gets `pyodide`. (`RLMTask(interpreter=<object>)` still bypasses it — that is rlm-kit's
   documented test seam, where the caller supplies and owns the double, not a config path.)
2. **`output_model` carries only `{action, artifact_id, key_fields}` — never drafted content
   directly.** A promotion candidate's actual markdown+frontmatter body is authored by
   `draft_memory_file` or `draft_skill_file` (both `make_model_tool`-based) and recorded as a
   `tool_call` event. Assemble the real text on READ by matching that event's `artifact_id` —
   never trust the plan's own claim about what it drafted. This is what keeps a label from
   drifting from the bytes it describes.
3. **Sensitive transcript content is redacted host-side before it becomes LM context.**
   Redaction is not the planner's judgement call — do it in the tool/ingestion layer, before
   any transcript text is exposed to the RLM, the same stance rlm-kit already takes for other
   untrusted content (fetched URLs, MCP output).
4. **The harness-adapter seam (`ingest` / `schema_for` / `list_targets`) is read-only, full
   stop.** See `ctx_distillery/adapters/base.py`. No adapter may ever expose a write/emit path
   reachable from an RLM tool — the actual "apply" step (now built: `ctx_distillery/apply.py`)
   stays a separate, human-gated action outside the RLM trajectory entirely, and gained NO adapter
   method: writing into `memory_dir` is ordinary host-side Python, the same reasoning
   `tools/memory_reader.py` gives for reading.
5. **Tools close over an immutable SNAPSHOT, never a live adapter.** `run_distillation` calls
   `adapter.ingest()` EXACTLY ONCE; that `list[ArtifactRef]` is what all five tool factories
   receive. Nothing in `HarnessAdapter` promises `list_targets()` is cheap or stable across
   calls, so a live reference would let `read_memory_file`'s allowlist shift mid-run — and it
   would create a second copy of the transcripts the driver already owns. The allowlist check is
   an EXACT `Path(path).resolve()` match against the snapshot; never make it a prefix or
   substring test (a substring test lets `/etc/passwd` through under a crafted name, and an
   unresolved prefix test lets a `..`-segment trick pass) — this defends the REQUEST side.
   **Separately**, `ClaudeCodeAdapter.list_targets()` itself must never let a symlink living
   inside `memory_dir` fold its outside target into the snapshot in the first place (an
   adversarial review reproduced exactly that escape) — it only enumerates a resolved path whose
   PARENT is still `memory_dir` itself. Exact-match-on-request and containment-at-enumeration are
   two separate checks; neither substitutes for the other. `apply.py` mirrors the second one on the
   WRITE side with the identical test (`resolved.parent == memory_dir`), before any write.
6. **Storage discovery is CONFIRMED for most paths and INHERITED for one — keep the
   distinction visible.** `ClaudeCodeAdapter.for_project(project_dir)` locates the real storage, and
   the evidence behind each part is NOT equal. Say so wherever it is described, and never upgrade one
   to sound like another:
   - **CONFIRMED**: `sanitize(project_dir)` (every `/` of the absolute path → `-`, nothing else
     transformed) giving `~/.claude/projects/<sanitized>/`; transcripts as one `<session-id>.jsonl`
     per past conversation, sibling to `memory/`; global skills at `~/.claude/skills/<name>/SKILL.md`
     (each skill is a DIRECTORY, not a flat file).
   - **INHERITED, not re-verified**: the `memory/` sub-path inside the project storage directory. No
     `memory/` directory existed on the machine the research ran on; the convention is this project's
     pre-existing assumption, carried forward honestly. Auto-discovery only needs the sanitization
     rule to be right.
   - **CONFIRMED by a dedicated control experiment** (it was an UNCONFIRMED hypothesis for one pass;
     this bullet used to still say so long after the experiment closed it — if you find another place
     that still calls it unverified, that place is the stale one): Claude Code DOES read a
     project-repo-relative `<project>/.claude/skills/<name>/SKILL.md`. A throwaway probe — a scratch
     directory, never a real project on this machine, seeded with
     `.claude/skills/probe-test-skill-xyz123/SKILL.md` — was inspected by a genuinely FRESH `claude -p`
     process launched from inside it, and showed all three of: the project-local skill was listed among
     that process's available skills; a sibling CONTROL directory with no `.claude/skills/` did NOT see
     it (isolating the effect to the project-relative directory rather than a global leak); and the
     skill was actually INVOKABLE, loading its real body. Anthropic's own documented scope table agrees
     (Personal `~/.claude/skills/<name>/SKILL.md` for all projects vs. Project `.claude/skills/<name>/`
     for one). Two caveats from that same experiment are load-bearing and live in "Known
     simplifications" below — a GLOBAL skill of the same name SHADOWS a project one, and a project's
     very FIRST skills directory needs a Claude Code restart before it is discovered.
   The transcript RENDERING is deliberately LOSSY and its rules are pinned by tests: filter to
   `user`/`assistant` FIRST (no other event type carries `message` at all), handle `message.content`
   as either a plain string or a list of blocks, size a `tool_result` in chars OR blocks depending on
   ITS OWN content's shape, and name an unrecognized block rather than dropping it. `isSidechain` is
   filtered as a DEFENSIVE NO-OP — it was `false` on all 1216 real events checked, because subagent
   messages live in separate files and are not inlined; do not re-describe it as "removing subagent
   noise". Every discovery helper takes a `home=` override, and no test may read this machine's real
   `~/.claude` (non-hermetic, and it would pull real user content into a fixture).
7. **A skill's REQUIRED frontmatter is `name` + `description`, full stop — in all three places.**
   `when_to_use` and `dispatch_intent` are OPTIONAL extras: accepted and passed through verbatim when
   present, never required. Every real installed skill inspected carried them, but all of those were
   one author's single suite, and Anthropic's documented Agent-Skills convention requires neither —
   mandating them would generalize from N=1. The three encodings must move TOGETHER or they drift:
   `make_skill_validator` (`tools/drafting.py`), `_spec_for_skill`'s model-facing PROMPT TEXT (same
   module), and `ClaudeCodeAdapter.schema_for("skill")`. A skill draft is also collision-checked
   SCOPE-AWARELY: `drafting._existing_names(index, "skill", scope)` filters by scope, because the
   global and project stores are independent namespaces and the same name in the other scope is not a
   collision.
8. **`apply.py` is the ONE writer, and it is unreachable from the RLM.** It is human-called
   (`apply_plan(memory_dir, assembled_plan, approved_ids)`), takes EXPLICIT per-candidate
   approval (never "apply the whole plan"), re-scans `list_targets()` ITSELF at apply time as the
   sole collision/target authority (the run's snapshot is stale by construction), creates a
   promotion with `open(path, "x")` (O_EXCL — the atomic, TOCTOU-proof enforcement; the re-scan is
   only the friendly early message), derives the filename as `slugify(frontmatter["name"]) + ".md"`
   with a degenerate slug being a hard refusal, ARCHIVES a prune to
   `<memory_dir's parent>/_ctx_distillery_archive/` instead of deleting it, and refuses any
   candidate carrying `problems` / `draft_ok is False` / an empty promotion draft. Because it
   writes, it is the one module EXEMPT from `tests/test_no_write_capability.py`'s mutation scan —
   the exemption is guarded by `test_apply_is_unreachable_from_the_planner_path`, which asserts no
   module on the RLM path imports it. Never import `apply` from `task.py`, `session.py`, a tool, or
   `__init__.py`; never give the planner a way to reach it.
   **This is why the CLI is TWO console scripts, not one binary with three subcommands.** That
   reachability test scans EVERY `.py` under `ctx_distillery/` except `apply.py` itself, and its
   regex matches a function-local import as readily as a top-level one — so a shared `cli.py`
   offering both `distill` and `apply` cannot exist without turning it red, and `apply.py` is
   explicitly not a precedent for a second exemption. The resolution keeps both properties:
   `ctx-distillery = ctx_distillery.cli:main` (planner: `distill` / `show`, never imports `apply`)
   and `ctx-distillery-apply = ctx_distillery.apply:main` (the writer hosts its own entry point).
   Do NOT "fix" this by relaxing the regex, by adding a second exempt module, or by reaching the
   writer through `importlib` — the last is evading a tripwire by spelling. Do not add a
   `python -m ctx_distillery apply` shim either: `__main__.py` would then be the importer.
   The CLI expresses the same "explicit per-candidate approval" the API does: `--approve` takes
   indices, `--confirm` is a second deliberate act (the default is a dry run that writes nothing),
   and `tests/test_apply_cli.py::test_no_flag_ever_approves_the_whole_plan` is the tripwire against
   an `--all` creeping back in.
9. **`apply_plan`'s roots are PER KIND, and a skill's containment check is its OWN check.** A skill is
   NOT a flat `<slug>.md` in the memory store: it is `<skills_root>/<slug>/SKILL.md` — one directory
   deeper, under a root that is never `memory_dir` (`~/.claude/skills` for global,
   `<project>/.claude/skills` for project). The flat check (`resolved.parent == memory_dir`) would
   REFUSE every legitimate skill write, so do not try to bend it: `_skill_target` is a separate
   function asserting the slug carries no path separator, that `<root>/<slug>` resolves to a DIRECT
   child of the root, that `SKILL.md` there resolves inside that directory, and that `<root>/<slug>`
   does not already exist as something else. Which root is chosen comes from the candidate's own
   `key_fields["scope"]` ("global"/"project"), a documented convention exactly like `prune`'s
   `target_path` — a missing or bogus scope is REFUSED, never defaulted, and a scope whose root the
   caller did not pass is refused too (the caller decides where a skill may be installed). Derive the
   roots with `adapters.claude_code.global_skills_root()` / `project_skills_root(project_dir)` — the
   same functions `for_project` uses, so the reader and the writer cannot disagree about a location.

10. **`studio/` (`ctx-distillery-studio`) is READ-ONLY of the trace file and unreachable from the
    RLM path — it is a THIRD workspace member, never a fork of the harness.** It replays a finished
    `DistillSession` run's trace/v1 JSONL file (`plan_from_events` -> `session.assemble` ->
    `rubric.trace_facts`, via `GET /v1/runs/{run_id}` and an SSE `GET /v1/runs/{run_id}/events`) and
    NEVER calls `ctx_distillery.apply.apply_plan` — applying a plan stays a separate, human-invoked
    action outside any web request, exactly as invariant 8 already requires. There is no
    live-drive endpoint (no `POST /v1/distill` or similar). **The old reason — "`run_distillation`
    needs a caller-supplied `HarnessAdapter` + `chat_fn` already wired, a materially heavier
    precondition than a self-contained driver a web request could own" — is FALSE now and must not
    be restated**: `cli.py::_cmd_distill` IS that driver (it assembles the whole precondition from
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
    body; and a stated loopback-bind/auth posture. `run_id` is sanitized (`_slug_id`)
    before it ever becomes a path component, and the PLAN panel renders a candidate's `draft` via
    `el.textContent` **only** — never `innerHTML` — because a drafted memory/skill body is
    untrusted model output, not markup to render. Root `pyproject.toml`'s `[tool.uv.workspace]
    members` includes `"studio"` alongside `"eval"`. **`_load_trace` reads through
    `ctx_distillery.trace_io.load_trace`, which filters to dict-shaped events ONLY before anything
    downstream sees them** — found by an adversarial review post-merge: `rlm_kit.trace.load_events`
    does NO shape validation, so a JSON-valid non-dict line (`42`, `null`, `[1,2,3]`) used to reach
    `plan_from_events`/`trace_facts`/`mapper.to_event`'s `.get(...)` calls and raise a raw
    `AttributeError` — a genuine 500, not the "never raise on a malformed trace" guarantee this
    invariant claims. The filter first lived INLINE here; it moved into `trace_io` when `eval/`
    turned out to need the identical guard (see invariant 11) — a de-duplication, never a removal.
    `_load_trace` is still the ONE entry point every endpoint's events pass through, which is what
    lets `_step_key`/`mapper.to_event` stay unguarded. Don't remove this filter thinking it's
    redundant with `plan_from_events`'s own `ValidationError` handling — that catches a DIFFERENT
    failure mode (a well-formed dict with the wrong shape), not a non-dict line at all.
    `studio/tests/test_boundary.py` pins the "never calls `apply_plan`" half of this invariant
    (statically, via `ast`, so the `__init__` docstring that NAMES `apply_plan` while promising
    never to call it isn't itself flagged).
11. **Trace-reading logic has ONE implementation per job, shared across all three members — never a
    per-member copy. THREE functions are covered: `rubric.plan_from_events` (plan-from-trace
    reconstruction), `trace_io.load_trace`/`dict_events` (the non-dict shape guard), and
    `render.render_plan` (the human/judge-legible plan rendering).** Same
    failure mode, found twice; the second one is `ctx_distillery/trace_io.py`, the ONE place JSONL
    bytes become events. `rlm_kit.trace.load_events` does no shape validation, so a JSON-valid
    non-dict line reaches every `.get(...)` consumer as-is; `studio/` fixed that member-locally
    first, and **`eval/` needing the identical guard a THIRD time is what forced the shared module**
    — exactly the situation that had already forced `plan_from_events` public. `rubric`, `session`,
    `eval/taskset`, `eval/cli` and `studio/app._load_trace` all read through it. `load_trace`
    re-implements the `run_id` filter instead of passing `run_id=` down to `load_events`, and that
    is LOAD-BEARING: `load_events`'s own filter is an unguarded `event.get("run_id")`, so
    delegating puts the crash UPSTREAM of the guard, where nothing in `ctx_distillery` can reach it
    (this is why hardening only the consumers would NOT have fixed `eval/cli.py`). Hardening
    `load_events` upstream in rlm-kit is a fine follow-up THERE; it is not a prerequisite here, and
    `load_trace` stays correct either way.

    The first of the two: **`rubric.plan_from_events` is the ONE public plan-from-trace
    reconstruction — `eval/` (and `studio/`) call it, neither keeps its own copy.** It used to be
    private (`rubric._plan_from_events`) with a duplicate local copy in `eval/ctx_distillery_eval/score.py`
    (kept separate only because `eval/`'s own convention is to never reach across the package
    boundary into an underscore-prefixed helper). `studio/` needing the SAME reconstruction a third
    time is what forced the actual fix: promote it to public on `rubric.py` (already public,
    top-level, and already imported-from by `eval/` for `rubric_to_meta`) and have `eval/score.py`
    import it instead of duplicating it again. Don't reintroduce a second copy anywhere — the
    `ValidationError`-degrade fix below has already needed applying to two copies once; a third copy
    means a third place a future fix can drift out of sync.

    The third: **`render.render_plan` is the ONE plan rendering**, promoted from `eval/`'s `score.py`
    (where it was written for the judge prompt) when `ctx-distillery show` needed the identical text —
    a reviewer deciding what to approve should read exactly what the judge reads. `eval/score.py`
    imports it and re-exports it in `__all__`, so `from ctx_distillery_eval.score import render_plan`
    still works; `eval/tests/test_score.py` pins the IDENTITY, not just the behaviour. The promotion
    immediately paid for itself: the no-candidates branch used to `return` early and DROP the
    run-level problems line, so a run that died before SUBMIT rendered — to a reviewer and to the
    judge — as a bare "proposed no candidates" that never said why. Fixed once, in the one place.

## Versioning

- Keep `pyproject.toml` `[project].version` and `ctx_distillery.__version__` in sync — pinned by
  `tests/test_public_api.py::test_version_matches_pyproject`. On a bump, fold the release's changes
  into `CHANGELOG.md` (under the new version).
- **The two workspace members carry their OWN `version`** (`eval/pyproject.toml` and
  `studio/pyproject.toml`, both `0.1.0` today) and **nothing checks them** — no test, no CI step, and
  nothing compares them to the root's. Each member DOES expose its own `__version__`
  (`eval/ctx_distillery_eval/__init__.py`, `studio/ctx_distillery_studio/__init__.py`), so a check
  is writable; none exists. They are independent numbers; don't
  assume bumping the root moved them, and don't assume they must move together.
- **0.1.0 is UNRELEASED.** `CHANGELOG.md` has `## [Unreleased]` as its ONLY version heading, so the
  first real bump is a RENAME of that heading to the shipped version plus a fresh empty
  `## [Unreleased]` above it — not a new section added underneath a shipped one. Getting this
  backwards would leave the project's entire history filed under a version that never shipped.

## Known simplifications (stated, not hidden)

- **`read_memory_file` reads through `ArtifactRef.path` directly**, not through a fourth adapter
  method. The ABC answers "what exists" and "give me everything", not "give me one body on
  demand"; every in-scope harness is a local filesystem, so a plain read of the enumerated,
  already-resolved path is honest. Whether a future non-filesystem harness needs a different read
  seam is deferred to when that harness is actually designed.
- **Transcript discovery reads the MAIN THREAD only.** `for_project` renders every
  `<session-id>.jsonl` in the project's storage directory. A SUBAGENT's messages are not in those
  files at all — they live in `subagents/agent-<id>.jsonl` (with a paired `.meta.json` carrying
  `agentType`/`description`/`spawnDepth`). Distilling those is a real deferred extension: the same
  file shape, a different glob. Not built.
- **The project-scoped skills location is CONFIRMED** (empirically, via a real control experiment),
  with real precedence/timing caveats to respect: a GLOBAL skill of the same name SHADOWS a project
  one (`make_skill_validator` and `apply_plan`'s `_promote_skill` both refuse a project-scope name a
  global skill already holds, hard, with no `overwrite` bypass), and a project's very FIRST
  top-level skills directory needs a Claude Code restart before it's discovered (`apply_plan`'s
  outcome says so explicitly for that case). `<project>/.claude/skills/` is where this project
  writes a project-scoped promotion, and it is now known to be picked up, subject to those two
  caveats — not merely "believed to belong there."
- **A skill's `references/` and `scripts/` are out of scope.** A real skill directory may carry them;
  `draft_skill_file` authors the `SKILL.md` body only, and `apply.py` writes only that one file.
- **Skill enumeration is opt-in on the explicit constructor.** `ClaudeCodeAdapter(memory_dir)` (what
  `apply.py`'s re-scan builds) enumerates no skills at all; pass `global_skills_dir=` /
  `project_skills_dir=`, or use `for_project`, which resolves the real roots. Deliberate: a bare
  adapter silently reaching into a real `~/.claude/skills` would make the re-scan machine-dependent.
- **The CLI is deliberately small: `distill`, `show`, `export`, and `apply` (in its own binary).**
  No `--interactive` per-candidate approval walk (the `show` → `--approve` → `--confirm` loop is
  complete and scriptable without a TTY surface) and no `purge` (see the archive bullet below). Each
  is real additional scope, not a missing polish pass. **`export` has no `--out` and `rl_export.py`
  has no `main()`** — all three sibling projects' exporters end in `open(out, "w")`, and both modules
  sit inside invariant 1's mutation scan, so the bundle is printed to stdout with
  `print(json.dumps(...))` and redirected with `>`. Note the form: `json.dump(..., sys.stdout)` also
  passes the textual scan but calls `.write` at runtime, which is evading the tripwire rather than
  satisfying it. `export` REFUSES an empty glob match instead of printing a zero-run bundle.
- **`rl_export.run_labels` is STRUCTURAL, and that boundary is the whole design of it.** An earlier
  reading declined a labels surface outright, citing "there's no obvious reward signal for *was this
  the right thing to prune*". That is correct about an ORACLE and wrong about everything else — it
  cited `toolscout`'s model-decided `met` booleans, which live in `rubric_signal`, not `run_labels`;
  and `diff-sentry`'s and `toolscout`'s actual `run_labels` are purely structural and map onto
  `AssembledCandidate`'s real fields one-for-one. So `run_labels` counts only what
  `schema.assemble()` already established (`finalized`, the action histogram, `n_unbacked`,
  `n_draft_not_ok`, `plan_problems`) — every field recomputable from the same JSONL by a second
  reader. Only `cve-reverser`'s `valid`/`complete` is oracle-flavoured, and its domain has ground
  truth; ours does not. Never add a field that claims a judgement was CORRECT. `rlm_kit.dataset`'s
  `run_label_bundle` refuses a surface literally named `reward` (it raises), so the reward-free
  property is enforced at the transport, not by convention here.
- **The DRAFTER may never ride the Claude subscription, and `from_env` refuses it UNCONDITIONALLY.**
  `CD_ROOT_LM` / `CD_SUB_LM` accept the `claude-agent-sdk/<id>` sentinel (`config.SUBSCRIPTION_PREFIX`
  → `config._maybe_subscription_lm` → `rlm_kit.configure(main_lm=, sub_lm=)`, the `[subscription]`
  extra); the drafter cannot, because `config.make_chat_fn` builds an `openai.OpenAI` client directly.
  The gate is unconditional rather than defensive for two compounding reasons: BOTH drafting tools are
  ALWAYS wired in `DistillSession.__init__` (so a sentinel there fails LATE, mid-trajectory, on the
  single hard-budget attempt), and `draft_model` falls back to `sub_model` which falls back to
  `main_model` — so setting only `CD_ROOT_LM=claude-agent-sdk/…`, the most natural way to try the
  subscription path, silently hands the sentinel to the drafting endpoint as a model id. The error
  distinguishes *explicitly set* from *inherited* (and names WHICH variable it was inherited from),
  because the fix differs. `config.py` must stay dspy-free AT MODULE LEVEL — the
  `from rlm_kit import ClaudeAgentLM` lives inside the sentinel branch, and both
  `tests/test_public_api.py` and `tests/test_subscription.py` assert the module top in a FRESH
  interpreter. `studio/app.py` needs no mirrored prefix: its `/v1/config` reports no model at all.
- **`apply_plan` is still callable directly from Python**, and `ctx-distillery-apply` is a thin
  layer over it — the CLI only knows how to derive Claude Code's roots from a `--project` path. Point
  at an unusual layout by calling `apply_plan(memory_dir, plan, approved_ids, ...)` yourself.
- **No adapter for any harness other than Claude Code.**
- **`apply.py` archives, and nothing purges.** A pruned file is moved to
  `_ctx_distillery_archive/`, never deleted; deleting the archive for real is a separate, explicit
  `purge` operation that does not exist yet. That is deliberate — "still recoverable" beats
  "irreversible" even at the human-approved step.
- **`apply_plan` only knows the Claude Code layout** (it builds a `ClaudeCodeAdapter` directly, and
  its per-kind roots are Claude Code's). Generalising the apply step across harnesses waits for a
  second adapter to actually exist.
- **`ctx_distillery/rubric.py` sources 100% of its facts from `session.assemble()`'s output, and
  `eval/` (`ctx-distillery-eval`) never writes and is never imported back.** The rubric is
  deterministic, reward-free ATLAS (TF/TA/TG/PA) facts, built on `rlm_kit.rubric` — it never decides
  met/unmet, and no field anywhere functions as a score. `eval/` is a SEPARATE workspace member: a
  static, offline LLM-as-judge that reads the assembled plan + the transcript(s) it was drawn from
  as TEXT only (never executes anything, never touches `apply.py`), and `ctx_distillery` itself
  must NEVER import `ctx_distillery_eval` back (test-enforced, `eval/tests/test_boundary.py`). The
  eval CLI's transcript path(s) are MANDATORY and must be non-empty — `_read_transcripts` refuses an
  empty or whitespace-only file loudly (`SystemExit`), because a real judge would otherwise silently
  score a plan against nothing. The now-public `rubric.plan_from_events` (see invariant 11 — `eval/`
  and `studio/` both call it rather than keeping their own copy) must degrade to `None` on a
  malformed `result` payload rather than raise — `assemble()`'s own stated philosophy is "none of
  them raise," and a malformed shape must fail the same way, never crash a batch scoring run (or a
  studio replay) over one bad trace.
- **The eval judge is LIVE iff `CDEVAL_MODEL` is set, and an unscored row is NEVER a fake 0.** The
  `judge = ["openai>=1.0"]` extra used to be dead — nothing imported it — so `judge.make_eval_judge`
  now implements it on `rlm_kit.tools.make_model_tool`, with `from openai import OpenAI` LAZY inside
  the chat closure (`eval/tests/test_boundary.py` asserts in a FRESH subprocess that importing the
  eval CLI pulls neither dspy nor openai; hoisting that import turns it red, and
  `eval/tests/test_judge.py` re-asserts it against the module's own AST). `max_retries=0` on the
  client is deliberate: `make_model_tool`'s transient-retry loop owns retries, so leaving the
  client's own retries on would multiply the two and turn a hard 60s timeout into minutes. Three
  shape changes came FIRST and are the load-bearing half: `Judge` returns a
  `JudgeVerdict(ok, score, reason)` (the only way to distinguish circuit-broken / endpoint-error /
  off-schema), `EvalRow.score` is OPTIONAL beside a REQUIRED-when-unscored `unscored_reason`
  (`compute_means` drops such rows from the sum AND the denominator — counting them is arithmetically
  identical to scoring 0), and `EvalReport` carries `n`/`n_unscored`/`judge_model`/`prompt_version`.
  Build ONE judge per batch: the circuit breaker lives in the closure. `CDEVAL_*` is a SEPARATE env
  surface from the root's `CD_*` on purpose — the judge must be pointable at a different model than
  the run it scores.
- **The eval member now has BOTH `score` and `run`; the three former blockers were closed
  ADDITIVELY, and each fix's shape is the part worth keeping.** (1) `taskset.py` carries BOTH
  concepts side by side — `Task`/`collect_tasks` (from TRACES, what `score` was always built on) and
  `EvalTask`/`load_taskset`/`demo_taskset` (the siblings' checked-in list to DRIVE). Neither replaced
  the other. (2) `judge.build_prompt` grew a third positional `reference` slot and `PROMPT_VERSION`
  bumped to `atlas-ctxd-eval-v2`, which is exactly what the constant is for; the `=== REFERENCE ===`
  section renders ONLY when `reference` is non-empty — a divergence from all three siblings'
  unconditional `"(no reference provided; …)"` fallback, because theirs ALWAYS have a taskset and
  this project's primary path (`score` with no `--taskset`) does not, so a no-reference run must keep
  rendering the byte-identical v1 prompt. When the section IS rendered, `REFERENCE_TRUST_RULE` is
  appended to `UNTRUSTED_DATA_RULE` (which enumerates exactly two untrusted bodies), stating that a
  taskset reference is TRUSTED input — a human wrote it into a checked-in file — while still not a
  licence to change the scale or output format. (3) `session.run_distillation_artifacts` returns a
  `DistillArtifacts(plan, events, run_id, trace_path, transcripts, memory_index)` where
  `transcripts` are the REDACTED texts the run actually saw, and `run_distillation` became a
  one-line wrapper with its signature AND return type unchanged (zero call-site edits). Those fields
  deliberately do NOT go on `AssembledPlan`: `render.plan_as_dict` is `dataclasses.asdict`, so
  transcript bodies would land in `ctx-distillery show --json`. Three properties of `run` that are
  NOT copied from the siblings and must not be "fixed" back: no `os.remove` of a stale trace (the
  FILENAME is `<slug(task.id)>-<UTC stamp>.jsonl`, unique per invocation, while `run_id` stays `task.id`
  for the pairing), everything under `--out`, and a failing task becoming an `unscored` ROW rather
  than a `SystemExit` that aborts the batch. `demo_taskset(root)` MATERIALIZES under a
  caller-supplied root (a `~/.claude` stand-in is machine-dependent, so no sibling's static-JSON
  form is possible) — layout only, with the transcript CONTENT staying checked in as
  `eval/ctx_distillery_eval/demo/*.jsonl`. Because `eval/` now imports product code to drive it,
  `eval/tests/test_boundary.py` gained an AST assertion that no eval module imports
  `ctx_distillery.apply` — the root package's own tripwire scans `ctx_distillery/` only.
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

## Harness scope

Claude Code is the only adapter being built — it's the only platform whose real persistence
format has been directly verified. Codex, Hermes, OpenClaw, and OpenCode are named future
targets, deliberately **not** designed yet: their real on-disk formats haven't been inspected,
and guessing one would be speculation dressed as design. Don't add an adapter for any of them
until someone has actually looked at that harness's real format.
