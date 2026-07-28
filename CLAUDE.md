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

- `ruff check .` — lint (line-length 110, matching rlm-kit's config).
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
6. **Storage discovery is CONFIRMED for some paths and INHERITED/UNCONFIRMED for others — keep the
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
   - **UNCONFIRMED, a hypothesis**: that Claude Code reads a project-repo-relative
     `<project>/.claude/skills/<name>/SKILL.md` at all. Nobody has verified it (that needs a fresh
     session in a directory seeded with a test skill, checking whether it is offered). It is motivated
     by real precedent — this repo's own `.claude/rules/` IS read project-relative — and this project
     TARGETS it for project-scoped promotions as the best available option. Do NOT write anything
     anywhere that implies this pass proved it works, and do the empirical check before relying on the
     project-skill path in anger.
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
    live-drive endpoint (no `POST /v1/distill` or similar): `run_distillation` needs a
    caller-supplied `HarnessAdapter` + `chat_fn` already wired, a materially heavier precondition
    than a self-contained one-shot driver a web request could reasonably own end-to-end — building
    one is real, additional scope this project has not taken on. `run_id` is sanitized (`_slug_id`)
    before it ever becomes a path component, and the PLAN panel renders a candidate's `draft` via
    `el.textContent` **only** — never `innerHTML` — because a drafted memory/skill body is
    untrusted model output, not markup to render. Root `pyproject.toml`'s `[tool.uv.workspace]
    members` includes `"studio"` alongside `"eval"`. **`_load_trace` filters to dict-shaped events
    ONLY, immediately after `load_events`, before anything downstream sees them** — found by an
    adversarial review post-merge: `rlm_kit.trace.load_events` does NO shape validation, so a
    JSON-valid non-dict line (`42`, `null`, `[1,2,3]`) used to reach `plan_from_events`/
    `trace_facts`/`mapper.to_event`'s `.get(...)` calls and raise a raw `AttributeError` — a genuine
    500, not the "never raise on a malformed trace" guarantee this invariant claims. Don't remove
    this filter thinking it's redundant with `plan_from_events`'s own `ValidationError` handling —
    that catches a DIFFERENT failure mode (a well-formed dict with the wrong shape), not a non-dict
    line at all.
11. **`rubric.plan_from_events` is the ONE public plan-from-trace reconstruction — `eval/` (and
    `studio/`) call it, neither keeps its own copy.** It used to be private
    (`rubric._plan_from_events`) with a duplicate local copy in `eval/ctx_distillery_eval/score.py`
    (kept separate only because `eval/`'s own convention is to never reach across the package
    boundary into an underscore-prefixed helper). `studio/` needing the SAME reconstruction a third
    time is what forced the actual fix: promote it to public on `rubric.py` (already public,
    top-level, and already imported-from by `eval/` for `rubric_to_meta`) and have `eval/score.py`
    import it instead of duplicating it again. Don't reintroduce a second copy anywhere — the
    `ValidationError`-degrade fix below has already needed applying to two copies once; a third copy
    means a third place a future fix can drift out of sync.

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
- **No CLI entry point**, and no adapter for any harness other than Claude Code. `apply_plan` is
  called from Python (or a REPL) by a human who has read the plan; a thin CLI wrapper over it is
  future work.
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
- **`studio/`'s frontend does not vendor a JetBrains Mono binary**, unlike the literal
  `diff-sentry-studio` precedent it otherwise mirrors. `static/style.css`'s `--mono` font stack
  PREFERS `"JetBrains Mono"` (matching the sibling studios' visual family when the visitor's system
  already has it installed) and falls back to the platform's own monospace stack otherwise — a
  stated simplification to avoid checking a font binary into a brand-new package, not an attempt to
  literally copy every asset of the cloned reference.

## Harness scope

Claude Code is the only adapter being built — it's the only platform whose real persistence
format has been directly verified. Codex, Hermes, OpenClaw, and OpenCode are named future
targets, deliberately **not** designed yet: their real on-disk formats haven't been inspected,
and guessing one would be speculation dressed as design. Don't add an adapter for any of them
until someone has actually looked at that harness's real format.
