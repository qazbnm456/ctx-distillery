# Changelog

All notable changes to `ctx-distillery` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`ctx-distillery` is an RLM-driven distillation planner, built on [`rlm-harness`](https://github.com/qazbnm456/rlm-harness),
that reads AI coding-agent session transcripts plus a persistent memory store and proposes a plan —
what to prune, what to merge across sessions, and what to promote into a memory file or a Skill. It
never applies anything itself.

## [Unreleased]

- **A `release.yml` now publishes to PyPI over Trusted Publishing (OIDC), with no API token.** Adapted
  from the sibling `rlm-harness` workflow. **Adding it cuts no version**: it is inert until a human
  publishes a GitHub Release, and `CLAUDE.md ## Versioning` is explicit that a release heading is the
  owner's statement about the software, never a side effect of tooling landing. One-time setup before
  the first release — a PyPI Trusted Publisher for `ctx-distillery` (owner/repo/workflow/environment)
  and a `pypi` environment in this repo's settings — is recorded in the file's own header.

  Two deliberate departures from the sibling, both because this repository is not shaped like it.
  **A tag/version guard** refuses a release whose tag disagrees with `pyproject.toml`; a PyPI version
  can never be reused, so shipping `0.1.0` under a `v0.2.0` tag is unrecoverable rather than merely
  wrong, and `test_public_api.py` already ties `__version__` to pyproject so checking the tag covers
  both. And a **note on the workspace**: `uv build` at the root emits only `ctx_distillery-<v>` —
  verified, neither `eval/` nor `studio/` — so `dist/*` is safe to upload wholesale; a member that
  ever publishes needs its own Trusted Publisher and its own job, never `--all-packages` here.

  `tests/test_doc_claims.py` gains claim 6, pinning the two guards that are each one deletable line
  and neither of which can fail visibly in testing: the fork guard names THIS repository (`release:
  published` is inherited by every fork, so a fork cutting a release would otherwise ask PyPI to
  publish this project), and the tag check still compares against pyproject. It also pins that both
  workflows install uv from the same SHA — two copies of a pin is two chances to bump one, the same
  reasoning claim 2 already applies to ruff.

- **The shipped skill defaults to the CURRENT project instead of teaching a path.** Its worked
  example was `ctx-distillery distill /path/to/project`, which is the CLI's unusual case, not its
  normal one: `project_dir` is `nargs="?", default="."` and has been since the command existed. A
  skill runs inside the project the operator is already in, so "in project A, distil project B" is
  a strange thing to make the default shape — the point raised by the owner, and correct. The
  example is now the bare `ctx-distillery distill`, with an explicit path called out as the case to
  confirm before using; `--project .` likewise for the apply line it prints, since that flag is
  `required=True` and must name the same project the run covered. The frontmatter now also states
  the scope directly, because it is a real limit and not just a default: ONE run covers ONE project
  — `for_project` resolves exactly one storage directory and `transcript_files` reads only that one.
  There is no mode that ingests every project at once, and nothing here implies otherwise any more.

- **The shipped skill's `description` was rewritten after dogfooding showed it claiming ground it
  does not own, and step 1 now offers the Claude subscription route.** A fresh session asked "what
  in this project's accumulated memory is worth keeping" and the skill never fired — correctly. The
  target was three files totalling 6 KB, this project had no trace of its own, and no `CD_*`
  credentials were set, so firing would have routed a thirty-second read into "configure credentials
  and pay for a run". The direct answer was also better: it verified each memory against the CURRENT
  code, which the planner cannot do at all. The old description invited exactly that request
  ("clean up a project's accumulated agent memory"); it now leads with the differentiator that is
  real — transcript history far larger than fits in context — and carries an explicit NOT-for
  clause. A skill that declines the requests it would serve badly is worth more than one that wins
  them.

  The subscription branch is stated with the limit that will otherwise bite: `CD_ROOT_LM=claude-agent-sdk/<id>`
  moves the PLANNER (nearly all the tokens) onto the operator's Pro/Max plan, but the drafter cannot
  follow it, so `CD_DRAFT_LM` still needs a real model and a key — verified against `from_env`, which
  refuses the inherited sentinel outright. The saving is most of the cost, not the setup. The skill
  asks before switching anyone onto their own subscription usage, and says that driving the Agent SDK
  from inside a Claude Code session is unverified rather than pretending otherwise.

- **A green suite hid a published install command that was already dead — and the fix was to stop
  tracking a branch.** The upstream harness renamed itself (`rlm-kit` -> `rlm-harness`) before its
  first publish; following it here was mechanical and is not the interesting part.

  `make check` stayed green throughout, because
  `uv.lock` pinned the old commit — while `uv tool install ... @ git+https://…/ctx-distillery`, the
  command this repository had just published in `README.md` and in the shipped skill, was already
  dead for everyone: `[tool.uv.sources]` tracks `branch = "main"`, so it resolved a package whose
  metadata now said `rlm-harness` and failed with `Package metadata name 'rlm-harness' does not match
  given name 'rlm-kit'`. A green suite and a broken published install command is a state nothing here
  can currently detect — the lockfile that makes local development reproducible is exactly what hides
  upstream drift from CI.

  The root cause is now gone rather than merely recorded. `rlm-harness` publishes to PyPI, so the
  `[tool.uv.sources]` override is deleted and the dependency is an ordinary exact pin
  (`rlm-harness==1.0.0`). Nothing tracks `main` any more, so an unlocked resolve can no longer drift
  out from under a published install command. The residual gap is narrower and worth naming: no job
  installs from the unlocked tree, so a dependency that breaks only outside the lockfile would still
  reach a user before CI.

- **This project now ships itself as a Claude Code Skill: `skills/ctx-distillery-plan/`, installable
  without cloning** — `npx skills add qazbnm456/ctx-distillery` (which is what indexes it on
  skills.sh and what makes it work in Codex/Cursor/the other agents that CLI supports), or natively
  via the new `.claude-plugin/marketplace.json`. **It is the PLANNER half ONLY, and the absent apply
  skill IS the safety mechanism** — an adversarial review of the first draft (two skills, one per
  binary) killed the apply half along with two claims made for it, both of which look like gates and
  are not; `CLAUDE.md` invariant 8's closing paragraph holds the argument. The plan skill PRINTS the
  `ctx-distillery-apply … --approve <indices>` line for a human to run, exactly as `_cmd_distill`
  already does, and carries no steps for driving it. It also declares no `allowed-tools` at all: the
  drafted grant would have pre-approved `distill`, which bills the operator and ships their redacted
  history off-box, for every project on the machine (`*` matches spaces).

  The CLI it drives stays a separate install:
  `uv tool install "ctx-distillery[cli] @ git+https://github.com/qazbnm456/ctx-distillery"`, adding
  `--with "litellm<1.95"` on macOS. Both halves of that are load-bearing and were found by testing
  the command rather than reasoning about it: the `[cli]` extra is required because `openai` is
  imported INSIDE `config.make_chat_fn`'s closure, so a bare install fails mid-trajectory rather
  than at startup; and `litellm` (transitive, via `dspy`) ships no macOS wheel at 1.95+, so it
  builds from an sdist that now needs Rust and dies in `maturin`. A `uv sync` checkout is unaffected
  (`uv.lock` pins a buildable version; `uv tool install` does not read a lockfile). Still no PyPI
  release, but no longer blocked on one: the reason it could not resolve was that this project's
  `rlm-harness` pin lived in `[tool.uv.sources]`, which never survives into published wheel metadata
  (verified by building the wheel — `Requires-Dist: rlm-harness`, bare). That pin is now an ordinary
  versioned dependency, so the metadata resolves on its own.

  `tests/test_skill_contract.py` pins what is mechanical (a case count is deliberately not quoted —
  nothing enforces one, and the first draft of this entry was already stale by a case): frontmatter
  carries `name` +
  `description` (**required** by the `npx skills` CLI, which SKIPS a skill missing either — stricter
  than Claude Code, where both are optional); `name` equals the DIRECTORY name, because
  `vercel-labs/skills` derives the install directory as `skill.name || basename(skill.path)`, so the
  tempting `name: plan` would have put a bare `/plan` in every project an installer opens; the
  shipped `SKILL.md` passes this project's OWN `make_skill_validator`; no skill pre-approves a tool
  (an ALLOWLIST on the key — `Bash(uv run *)`, `Bash(*)` and a bare `Bash` all defeat a blocklist);
  and **every command a skill prints parses through the real `build_parser().parse_args()`**, which
  a "scrape `--flag` tokens" check cannot do — it sees neither subcommand scoping (`--json` is on
  `distill` and `show` but not `export`), nor `required=True`, nor `choices`.

  The manifest's `source` names the SKILL DIRECTORY itself with `skills: ["./"]`, which is one level
  deeper than the docs' wording ("skill directories containing `<name>/SKILL.md`") first suggests.
  Both shapes were installed and invoked to decide between them, because only one is testable from
  here: the `npx skills` CLI keys its plugin-grouping map on `resolve(join(source, skillPath))` and
  looks it up by the skill's OWN resolved directory, so the likelier-looking `source: "./"` +
  `skills: ["./skills"]` misses by exactly one level — it installs and dedupes correctly but shows
  up ungrouped, and it copies the whole 2.7 MB repository into the plugin cache on every install and
  update rather than 16 KB. Whether Claude Code's loader accepts the deeper shape is NOT testable
  from here and was confirmed by hand; `CLAUDE.md` invariant 8 records that any future change to
  `source`/`skills`/`strict` needs the same manual re-run, because a manifest can pass every test in
  the suite and still fail to load.

- **Two README claims corrected in the same commit, because this change makes that file the entry
  point for people who never read the code.** It said transcripts keep "message text and thinking
  verbatim" — the measurement is that thinking renders to **0%** (2,384 blocks across 60 session
  files, none with content, because Claude Code does not persist the thinking text), which together
  with `tool_use` collapsing to a label is why a rendered transcript is mostly the user's side; the
  same wording survived in `ctx_distillery/README.md` and `adapters/claude_code.py` and was swept
  there too. And it said subagent conversations "aren't read yet", which its own line 22 already
  contradicted — they are read, opt-in, via `--include-subagents`.

- **`ctx-distillery-studio` gained an OPT-IN live-drive endpoint, `POST /v1/distill`, off unless the
  operator sets `CTXD_LIVE_PROJECTS`.** This reopens a refusal `CLAUDE.md` invariant 10 previously
  stated outright ("there is no live-drive endpoint"), on the four conditions that invariant itself
  named as sufficient to revisit it — all four are now met, and `studio/README.md`'s "Scope: replay
  + opt-in live" section and `studio/CLAUDE.md`'s invariant 10 hold the full argument:

  1. A real cancel seam now exists UPSTREAM, in `rlm-harness`: `rlm_harness.SandboxCancelled` +
     `RLMTask(cancel_event=...)` reach into the sandbox interpreter's own watchdog thread and can
     kill a wedged `deno`/`pyodide` subprocess mid-call — the thing `asyncio.Task.cancel()`
     fundamentally cannot do (the sandbox's blocking read has no `await` inside it). This project's
     `rlm-harness` pin moved to the commit that adds it; ZERO ctx-distillery signature changes were
     needed to wire it through, since `run_distillation_artifacts`'s existing `**kw` already
     forwards `cancel_event=` into `DistillSession.__init__` -> `RLMTask.__init__`.
  2. The route genuinely does not exist unless `CTXD_LIVE_PROJECTS` is set — `GET /v1/projects` and
     `POST /v1/distill` both 404 otherwise, unconditionally.
  3. A live request's `project_dir` is checked against that SAME environment-sourced allowlist
     (exact match, never prefix/substring) — never taken on faith from the request body.
  4. Every live-mode route requires BOTH a loopback check AND a same-origin check, and each of
     those is itself an AND of two conditions, not an OR — an adversarial review found a first
     draft got both wrong the same way, by direct reproduction: the loopback check must require the
     real TCP peer be loopback **AND** the `Host` header itself name a loopback host (checking the
     peer alone is backwards for DNS rebinding — a rebound request's peer genuinely IS loopback
     while its `Host`/`Origin` headers still name the attacker's hostname), and the same-origin
     check must compare `Origin` against `Host` by full authority — hostname AND port — rather than
     hostname alone (which would make every port on `localhost` mutually trusted).

  New: `studio/ctx_distillery_studio/live.py` (`run_live`, the worker-thread driver — builds the
  SAME `DistillConfig.from_env()` -> `setup()` -> `make_chat_fn()` -> `ClaudeCodeAdapter.for_project()`
  precondition the CLI's `_cmd_distill` does, so a live run and a CLI run of the same project can
  never silently diverge in what they run under) and `studio/ctx_distillery_studio/shutdown.py`
  (SIGINT/SIGTERM -> cancel-all-in-flight bridge, needed because uvicorn's own graceful shutdown
  waits for open SSE connections BEFORE running lifespan shutdown, which would otherwise deadlock on
  a run nothing has told to stop). `session.run_distillation_artifacts` gained an explicit
  `on_event` keyword (kept separate from `**kw` because it belongs to `TraceRecorder`, a different
  callee than `DistillSession`) so a live run's trace events can be forwarded to its SSE stream as
  they are recorded, not just replayed after the fact. `GET /v1/runs/{run_id}` (and
  `.../iterations`, `.../events`) now refuse with 409 while `run_id` is still live, rather than
  showing a truncated, ever-changing snapshot — a live run's own progress is watched through `POST
  /v1/distill`'s own SSE response instead, carrying one genuinely new event (`distill.run.started`)
  ahead of everything else.

  `run_live`'s exactly-once completion guarantee catches `BaseException`, not `Exception`:
  `DistillConfig.from_env()` raises `SystemExit` as its own documented error contract on a
  misconfigured `CD_*` var, and CPython's default `threading.excepthook` silently swallows an
  uncaught `SystemExit` in a non-main thread — `except Exception` would have reproduced the exact
  "the client hangs forever" failure this function exists to prevent, one exception type later.

- **`CodexAdapter` — the SECOND `HarnessAdapter` this project has ever built, for OpenAI's Codex
  CLI, READ-ONLY INGESTION ONLY.** `apply.py` gained no Codex-specific write path and remains
  entirely Claude-Code-specific (invariant 9) — a Codex-sourced run produces a real judgement-only
  plan, reviewable via `ctx-distillery show`, but `ctx-distillery-apply` cannot yet write a
  `promote_to_skill`/`promote_to_memory` candidate drawn from one INTO Codex's own store. This is a
  stated, deliberate scope boundary (confirmed with the user before design work started), not an
  oversight.

  **Evidence tier, stated explicitly rather than rounded up**: every structural fact this adapter
  is built against was confirmed by reading `openai/codex`'s own SOURCE at HEAD this session (via
  the GitHub API) — a weaker tier than several `ClaudeCodeAdapter` facts, which rest on a real
  installed Claude Code process or a dedicated control experiment. `ctx_distillery/adapters/codex.py`'s
  own module docstring says so, matching CLAUDE.md invariant 6's own evidence-tier discipline.

  Rollouts (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`) are rendered DELIBERATELY LOSSILY —
  `user`/`assistant` message text plus a `[used tool: X]` label for tool-call-shaped items (Codex's
  `FunctionCall`/`LocalShellCall`/`ToolSearchCall`/etc.), mirroring Claude Code's own `tool_use`
  label rather than the silent drop a first draft had (found by adversarial review: a coding
  agent's shell/patch calls are its actual substance, not noise). `AGENTS.md` becomes
  `project_instructions` via the SAME field `CLAUDE.md`'s own ingestion already populates — walked
  root-to-leaf from the nearest `.git` to the project directory, `AGENTS.override.md` preferred
  over `AGENTS.md` per directory, never the global `~/.codex/AGENTS.md` (the identical
  project-only scope decision already made for Claude Code's `CLAUDE.md`). Skills
  (`.agents/skills/*/SKILL.md`) are enumerated at EVERY directory in that same walk, not just the
  project directory (a second gap a first draft had — Codex's own `repo_agents_skill_roots` walks
  the identical path AGENTS.md discovery does).

  **Codex's own memory system (`~/.codex/memories/`) is DELIBERATELY NOT READ AT ALL** — a
  correctness decision, not a simplification. Reading `codex-rs/memories/README.md` in full shows
  it is a single, MACHINE-WIDE store ("Global Consolidation", "a single global phase-2 lock"),
  consolidated across every project the operator has ever used Codex on, with no `cwd`/project
  filter in either extraction phase's eligibility rules (confirmed further by adversarial review
  against the actual state-DB schema: no project/cwd column exists on the stage-1 outputs table
  either). Including it in a *per-project* snapshot would silently mix another, unrelated
  project's learnings into this one's plan — worse than simply omitting it.

  **The one real structural difference from `ClaudeCodeAdapter`, and its stated cost**: Claude Code
  partitions storage by project on disk for free; Codex does not — every rollout on the machine
  lives under the SAME `sessions/` tree, with the project recorded INSIDE each file as
  `SessionMeta.cwd`. `for_project` must therefore open every rollout on the machine far enough to
  read its `session_meta` line (capped at a fixed line count so a corrupted/hostile file that never
  yields one cannot force an unbounded per-file read — added per adversarial review) and filter by
  `cwd` — a real, stated cost proportional to every session ever recorded, not just this project's.

  **A genuinely critical bug was caught and fixed before this ever shipped**: the first draft read
  `cwd` from `payload["meta"]["cwd"]`, reasoning from `SessionMetaLine`'s Rust field being
  `#[serde(flatten)]` — backwards. Flatten means `cwd` is a DIRECT sibling of `git` on the wire,
  never nested under a `"meta"` key; had this shipped as written, `for_project` would have matched
  ZERO sessions for every project, forever, silently indistinguishable from "no Codex history yet."
  Found by adversarial review re-deriving the claim from `SessionMetaLine`'s own custom
  `Deserialize` implementation rather than trusting the Rust struct shape at a glance.

  **Prerequisite before this adapter gets CLI wiring — resolved by the harness-marker change
  below.**

- **Every run is now stamped with WHICH harness produced it, and `apply.py` refuses to write a
  candidate drawn from one it does not understand — the prerequisite `CodexAdapter` recorded
  above.** `HarnessAdapter` gained a required `harness_name` class attribute
  (`ClaudeCodeAdapter.harness_name = "claude_code"`, `CodexAdapter.harness_name = "codex"`);
  `run_distillation_artifacts` stamps `run_meta["harness"]` from it — deliberately AFTER
  `run_meta.update(meta or {})`, so a caller's own `meta` dict can never silently clobber the real
  provenance — and `schema.assemble` reads it back (via `trace_io.run_start_meta`, never trusted
  from the plan's own claim) into a new `AssembledPlan.harness: str | None` field.

  `schema.SUPPORTED_WRITE_HARNESSES = ("claude_code",)` is the one closed vocabulary both
  `render.py` and `apply.py` read from — placed in `schema.py` rather than `apply.py` specifically
  so `render.py` can import it without an `apply.py`<->`render.py` cycle (`apply.py` already
  imports `render_plan`). `render_plan` now prints a warning line at the very TOP of its output when
  `plan.harness` is set and unsupported, so `ctx-distillery show`'s default text and
  `ctx-distillery-apply`'s own dry-run report both surface a mismatch up front — an earlier
  blueprint draft missed this entirely, leaving the primary CLI surface silent until `--confirm`
  refused candidates one at a time. `apply._blocking_problem` gained a fourth check refusing every
  non-`keep` action when the harness is set and unsupported; `harness is None` PERMITS deliberately
  (every trace recorded before this landed is a Claude Code trace), and a malformed non-string
  value is kept verbatim rather than coerced to `None` — the membership check refuses it naturally,
  which is the safer of the two failure modes.

  `studio/`'s `app.js` mirrors the same rule: `applyBlocker` gained a fourth condition (exempting
  `keep`, matching `_blocking_problem` exactly), and `PLAN.harness` is populated by `loadPlan` from
  the fetched plan's own `harness` field (reset to `null` by `startReplay`). `studio/DESIGN.md` §2's
  two count-citing sentences ("three"/"five") moved to "four"/"six".

  **Caught by adversarial review before implementation**: the first blueprint draft (1) never
  touched `render.py` at all, (2) proposed JS wiring that referenced a nonexistent `data` variable
  without saying which of the two real functions (`startReplay` for reset, `loadPlan` for populate)
  needed which half of the edit, and (3) proposed a JS test that only exercised `applyBlocker` via
  `seed()`'s direct `PLAN.harness` assignment — which would have stayed green even if the real
  `loadPlan` fetch-population wiring were completely broken. All three are fixed in the shipped
  version; the JS test suite now separately drives the real `loadPlan` against a mocked `fetch`.

  **A fourth issue was caught by the post-implementation `/check` pass**: `applyBlocker`'s harness
  check originally sat AFTER the `promote_to_skill` scope gate and the `prune` target gate, while
  `_blocking_problem` checks harness BEFORE either — so a candidate with both a bad `scope` and a
  mismatched harness would show the studio reviewer the scope reason while `apply_plan` would
  actually refuse it for the harness reason instead. Not a safety bug (both land on refused), but
  it broke DESIGN.md §2's own stated property that every blocked reason shown is the one
  `apply_plan` really gives. Fixed by reordering `applyBlocker` to match, with a test pinning the
  precedence directly.

- **The planner now reads the project's own `CLAUDE.md` (or `.claude/CLAUDE.md`) as read-only
  context, closing a real gap: this tool reasoned about promoting durable knowledge into memory
  without ever seeing the durable knowledge the project already had written down.** Confirmed as a
  genuine gap first — `ArtifactKind = Literal["memory", "skill", "index"]` has no fourth kind, and
  `ClaudeCodeAdapter.ingest()`/`list_targets()` never touched a project's own root instructions
  file. Confirmed via Claude Code's own official docs (`code.claude.com/docs/en/claude-directory`,
  fetched directly, not inferred from a blog summary) that a project `CLAUDE.md` — or the
  equivalent `.claude/CLAUDE.md`, which the docs state is the same thing in a different location —
  is loaded into every session's context. **A WEAKER claim, kept visibly separate per adversarial
  review**: that Claude Code does not natively read AGENTS.md is inferred from a web-search pass
  (community discussion of a manual `ln -s AGENTS.md CLAUDE.md` workaround), not from the same
  official docs page, which does not mention AGENTS.md at all — could be stale, given this is an
  active feature request. Nothing here depends on it being right either way: a plain file read
  follows a same-directory symlink regardless, so the workaround is picked up for free whether or
  not AGENTS.md ever gains native support.

  `RawSession` gains `project_instructions: str = ""`; `ClaudeCodeAdapter.for_project` reads it via
  a new `project_claude_md_path` helper (root wins if both locations somehow exist — THIS
  project's own design choice, not a documented Claude Code precedence rule, kept visibly labeled
  as such); `session.run_distillation_artifacts` redacts it through the exact same `redact()` call
  transcripts already go through (invariant 3, skipped only when empty — a call-count detail for
  an existing test, not a redaction-scope carve-out) and stamps `run_meta["project_instructions_chars"]`
  for provenance (an honest `0`, never a fabricated claim, when none was found); `DistillSession`'s
  signature gains `project_instructions: str` as a THIRD, always-visible input (no tool needed —
  unlike `transcripts`, nothing closes over it, so it flows only through `.arun()`); and the
  planner's instructions tell it to treat this as a comparison point (flag redundant promotions,
  flag contradictions as conflicts for human review) — explicitly NOT the same trust tier as this
  project's own memory/skill files, since a target project's `CLAUDE.md` is authored by whoever
  controls THAT repo, which may be an untrusted third party the operator is distilling; the
  paragraph fences it as DATA to reason about, never instructions directed at the planner itself,
  mirroring `eval/`'s own `UNTRUSTED_DATA_RULE` convention for the identical shape of fence.
  Read-only, never a promotion/prune target — `apply.py` has no "edit an existing file in place"
  capability anywhere, and building one is an explicit non-goal here. Not added to
  `DistillArtifacts` or `eval/`'s judge prompt: no concrete consumer needs the redacted text outside
  the run itself today, and adding a field with no real consumer is the premature-abstraction trap
  this project's own conventions already warn against.

  **Found by adversarial review, fixed before merge**: the first draft skipped the
  enumeration-side containment check invariant 5 already requires for `_memory_refs`/`memory_dir`
  — `project_claude_md_path` now refuses a resolved candidate (or a `.claude` directory itself)
  that escapes the directory it was found in, closing the same class of hole a git-tracked
  `CLAUDE.md -> /etc/passwd` in a cloned, untrusted repo would otherwise open, without breaking the
  documented same-directory AGENTS.md symlink case; and the redact-unconditionally draft would have
  broken `tests/test_session.py::test_a_custom_redactor_is_honoured`, an existing test asserting
  the injected redactor's call count, fixed by skipping the call entirely on empty input.

- **A skill promotion may now carry supplementary `references/*.md` and `scripts/*` files —
  closing a Known-simplification gap that used to say this was out of scope.** A new SIXTH
  read-only tool, `draft_skill_extra_file(artifact_id, relative_path, kind, evidence)`, drafts one
  supplementary file per call, sharing the `artifact_id` an earlier `draft_skill_file` call minted
  rather than authoring a new artifact — the same repeatable-tool architecture
  `draft_memory_file`/`draft_skill_file` already use (one model call, one deterministic validator,
  one `tool_call` event), chosen over a single-call multi-file bundle because it doesn't need the
  model to produce a custom delimited format correctly in one shot.

  `schema.assemble` gathers every `draft_skill_extra_file` call sharing a `promote_to_skill`
  candidate's `artifact_id` — last call per `(artifact_id, relative_path)` wins, the compound-key
  generalization of the existing single-key "a repair loop legitimately re-drafts" rule — onto a new
  `AssembledCandidate.extra_files: dict[str, AssembledExtraFile]`. A bad extra file (empty draft,
  failed validator) is kept for visibility (parity with the main draft) but ALSO reported on the
  candidate's own `problems`, which is what makes `apply._blocking_problem`'s existing "any problems
  -> refuse" check block the whole candidate for free — no new blocking logic needed in `apply.py`.

  `apply._promote_skill` writes the supplementary files behind their OWN containment check
  (`_skill_extra_target`, CLAUDE.md invariant 9) — a DIFFERENT function from `_skill_target`, same
  reasoning as why that one is already separate from the flat memory-file check: a possibly-nested
  relative path needs its own wall. Every entry is validated BEFORE anything is written, including
  `SKILL.md` itself — a candidate doomed by one bad `relative_path` must never leave a half-written,
  DISCOVERABLE skill behind (`rlm_harness.skills.discover_skills` globs `*/SKILL.md`).

  **Found by TWO rounds of adversarial review before this shipped, and the second round is the one
  worth reading closely.** Round one: `_skill_extra_target` uses `Path.is_relative_to()` rather than
  `Path.relative_to()` + `except ValueError` (a NUL byte would otherwise raise uncaught, reproducing
  a bug `_skill_target` already had to fix once), and each supplementary file's own `mkdir` is
  isolated in its own try, separate from its `open()`. Round two, on the IMPLEMENTED code, found a
  gap round one's own fixes had missed: `_skill_extra_target` checks each `relative_path` in
  ISOLATION, so two individually-valid entries could still conflict with EACH OTHER —
  `relative_path="scripts/utils"` (a file) and `relative_path="scripts/utils/helper.py"` (which
  needs `scripts/utils` to be a DIRECTORY) both passed containment on their own, and the batch wrote
  `SKILL.md` and the first extra to disk BEFORE the second one's `mkdir` collided — leaving exactly
  the half-written, DISCOVERABLE skill the validate-before-write pass exists to prevent, reproduced
  end to end through the real `assemble()` -> `apply_plan` path. `_extra_path_conflict` closes it:
  checked on the RESOLVED targets, before anything is written, alongside every other entry's
  containment check. (The isolated `mkdir` still earns its keep for a DIFFERENT case this new check
  cannot see — a file already on disk from an earlier, unrelated `apply_plan` call, not a conflict
  between this call's own entries.)

  `render.render_plan` shows each supplementary file beside its candidate (so `ctx-distillery show`
  and the eval judge both see them — invariant 11), and `render.plan_as_dict` picks `extra_files` up
  automatically (`dataclasses.asdict`). Studio's Trajectory drawer and live feed both gained a
  bespoke row for the sixth tool too — without it, a real run using it would have rendered as
  `unrecognized: true` in the drawer and been silently DROPPED from the live SSE feed, the exact
  "trace from a future build" case those two paths' own doc comments say should never happen for a
  tool this project actually shipped.

  **Two things deliberately NOT built, named rather than left silent**: no cross-check that a
  drafted `SKILL.md`'s prose actually matches what was drafted via `draft_skill_extra_file` (a model
  can write "see `scripts/setup.sh`" without ever drafting that path); and no live registry
  validating that `draft_skill_extra_file`'s `artifact_id` argument corresponds to a real prior
  `draft_skill_file` call (a typo'd id is a silently orphaned `tool_call` — never written, never an
  error). Also not built: the studio PLAN panel does not yet browse multiple files per candidate
  visually — the data is reachable over `GET /v1/runs/{id}`, a UI story is a follow-up.

- **The eval scorecard now reports each row's own transcript composition** —
  `transcripts=<n> (sessions=<a> subagents=<b>)`, appended per row (scored or unscored), closing the
  gap an earlier entry below named "Not built, and named so it is not mistaken for shipped." Three
  new `EvalRow` fields (`n_transcripts`/`transcript_sessions`/`transcript_subagents`), all `None`
  (never a fabricated `0`) when a run's own trace never recorded them — deliberately PER ROW, never
  on `EvalReport`, for the reason that earlier entry already gave: `score` scores an arbitrary glob
  of traces, each with its own unrelated composition, so there is no single report-level number the
  way there is one `judge_model` or `prompt_version`.

  The fix is a promotion, not a new implementation (invariant 11): `studio/`'s `mapper.py` already
  had `transcript_composition(meta)`, and `rubric.py` already had its own inline scan for the bare
  `n_transcripts` count — two partial, independent reads of the same `run_start` meta. Both now go
  through `ctx_distillery.trace_io.transcript_facts` (built on two new shared primitives,
  `run_start_meta` + the moved `transcript_composition`), which `eval/`'s `score.score_run` calls as
  its own, THIRD consumer — the same trigger invariant 11 already documents elsewhere ("eval/
  needing the identical guard a THIRD time is what forced the shared module"). `studio/mapper.py`
  re-imports and re-exports the name rather than losing it, so nothing importing from `mapper.py`
  (`iterations.py`, its own tests) needed to change.

  One sharp edge, worth recording rather than leaving implicit: `trace_io.dict_events` already
  coerces a non-dict `run_start.meta` to `{}` before `run_start_meta`'s own scan ever sees it (a
  normalization `dict_events`'s own docstring states), so a malformed meta and a genuinely empty one
  are indistinguishable by the time any caller reads them — both surface as `{}`, not `None`. That
  is harmless end to end (every downstream `.get(...)` degrades identically either way), and
  `tests/test_trace_io.py` pins the distinction on purpose: `None` is reserved for "no `run_start`
  event at all."

- **`make check` is now the one verification entrypoint, and a `Makefile` is not a hole in invariant
  1.** Full verification was FIVE commands across four suites plus lint, existing only as ~40 lines of
  `CLAUDE.md ## Verify` prose whose lead-in still said "Run BOTH" — an undercount since the `eval/`,
  `studio/` and node suites landed. `tests/test_no_write_capability.py` scans `ctx_distillery/**.py`,
  so a Makefile is outside it by construction and nothing on the RLM path can reach it. One
  divergence from the documented commands, and it is a real failure the file fixes: `make test` runs
  `uv run python -m pytest -q`, because the bare `pytest -q` the docs give assumes an ACTIVATED venv
  and otherwise dies with "No such file or directory".

- **Invariant 10's body and three `studio/`-only simplifications moved to a nested
  `studio/CLAUDE.md`.** Claude Code loads a directory-level `CLAUDE.md` only when reading files under
  it, so ~93 lines that apply to that member and nothing else stopped being charged to every session
  (root: 868 -> 802 lines, 10,784 -> 9,951 words). Two things deliberately did NOT move: the
  normative sentence (read-only of the trace, never calls `apply_plan`, no live-drive endpoint) stays
  in the root stub because it also constrains anyone editing `apply.py`, and **the number 10 was not
  reassigned** — ~20 places in code, tests and CSS cite it by number, plus 13 here — so the root list
  still runs 1–12 with 10 as a stub. Invariant 3's 166-line redaction block stayed put on purpose:
  `redact.py` shares a directory with `apply.py`/`cli.py`/`session.py`, so a nested
  `ctx_distillery/CLAUDE.md` would load almost always and save nothing.

- **`tests/test_doc_claims.py` — docs-vs-code drift now fails a test instead of a review.** It is
  this repo's recurring failure mode, not a hypothetical: several in-line "this bullet used to say X,
  which was FALSE" corrections, invariant 6 recording that a stale label "has now caught this
  invariant itself twice", and four commits in one two-week window whose whole subject was repairing
  a doc claim. Every one was caught by a human reading carefully. Four claims are pinned to start:
  all four workspace sluggers share one cap AND `CLAUDE.md` states that number (each member's suite
  pinned only its OWN cap, so any of the four could move and leave the docs plus three source
  comments quietly wrong); the `ruff` pin is spelled identically in `Makefile`, `ci.yml` and
  `CLAUDE.md`; `--directory <member>` survives in both runners (ci.yml's own comment records that
  omission shipping once, green, running zero of `eval/tests/`); and `[project.scripts]` is exactly
  invariant 8's two entry points. Each check was verified by sabotage — five mutations, each turning
  exactly one test red. Reads source as TEXT, never importing across members, so it works in a
  plain-pip install too.

- **FIXED — the new `app.test.js` failed on CI while passing locally, and the CI job now spans the
  version boundary that caused it.** The stub DOM wrote `global.navigator = {}`; `navigator` became a
  READ-ONLY global in Node 21, so every test in the file threw `Cannot set property navigator of
  #<Object> which has only a getter` on the runner's Node 24 and none of them did on a local Node 16.
  The stub now defines it only when the runtime has none, via `Object.defineProperty`.

  The job's own comment is the more interesting half. It read *"No setup-node: the ubuntu runner
  ships node, and these tests are plain CommonJS — no npm, no package.json, no node_modules"*. Every
  clause of that is true, and it answers the wrong question: it is about installing DEPENDENCIES, not
  about pinning a RUNTIME. An unpinned runner version drifts with the image — the same hazard this
  repo already pins `ruff@0.16.0` against, for the same reason. `studio-static` is now a `[20, 24]`
  matrix, chosen to straddle the Node 21 boundary rather than to be thorough for its own sake.

- **The studio's review surface, reworked across three passes and two live runs.** It was replaying a
  trace correctly and explaining almost none of it. Each change below is a defect the console had,
  found by reading a real run rather than by taste:
  * **Plan review is a rail LIST + a middle STAGE.** The middle column rendered all ten candidates
    with every drafted body inline — 32,091 characters, each `<pre>` capped at 260px with its own
    scrollbar, inside a panel that also scrolled, so reading candidate 5 threw you to candidate 6.
  * **One scroll track per panel**, plus the siblings' `mask-image` fade that distinguishes a list
    that ENDS from one that continues.
  * **Ticks build the apply command.** The console exists to get a reviewer to a correct
    `ctx-distillery-apply --approve …`; it now assembles that from checkboxes, WITHOUT `--confirm`
    (invariant 8: this must never be the thing that applied a plan) and naming the project directory
    to run it from. `keep` and blocked candidates are disabled with the reason — `apply_plan` returns
    `STATUS_NOOP` for a keep, so a tick invents an action the writer does not have.
  * **The stage's Entry view is four ZONES** (proposes / evidence / why / if applied), because
    `key_fields` is FREE-FORM and two real runs used entirely different keys. Transcript indices
    resolve to identities through `transcript_index` — `[1]` was unexplainable before that field
    existed — including inside PROSE, under a rule narrow enough that a bare `[3]` is never a link.
  * **Trajectory: WIDTH IS TIME.** One of nine calls took 242.6s of 313.4s on a real run; a column of
    equal-height rows cannot show that. Plus turn-boundary buttons and a search over each turn's
    reasoning + code + output (one turn's REPL echo ran 16,038 characters).
  * **The meta column stopped stating more than its facts measure.** `draft -> read` and `0 / 3 read`
    both rendered TOOL-CALL counts as conclusions about the planner, whose main evidence path is the
    `transcripts` REPL variable that neither fact can see. Fact rows are labelled rather than
    de-underscored ("n non keep" is a variable name, not a label), criteria descriptions are SERVED
    from the run's own meta rather than copied, and run telemetry moved out of the drawer.
  * **`TRACES` left the header.** A diagnostic occupying the most valuable strip on the page, in a
    slot copied from siblings where it holds a short model name — so a path truncated to
    `/Users/operator/Documents/…`, losing the only segment anyone reads it for.
- **FIXED — three bugs found while integrating that work, none of which a green suite would catch.**
  * An **infinite loop**: `appendProseWithLinks` and `transcriptsCitedBy` drove the same module-level
    `/g` regexes with `exec`, and the render is re-entrant, so a shared `lastIndex` restarted the
    outer loop forever — a 4 GB heap exhaustion, not a slow page. Invisible on the first run, whose
    evidence is integer lists, so the loop never had a body to re-enter from. Now `matchAll`.
  * `syncMeta()` sat **after a `return`** in `renderRubric` — unreachable. The meta column still
    expanded, but only because `loadTelemetry` calls it too, so the layout was correct exactly when
    the `/iterations` fetch happened to succeed; its `catch` is a deliberate silent early return.
  * A **race**: `loadConfig()` and `loadRunsList()` were fired together, the second renders from
    state the first sets, and nothing re-renders it — losing was silent and permanent. Now sequenced.
  `studio/tests/app.test.js` is new and exists because of these: `static-contract.test.js` reads
  `app.js` as TEXT and `trajectory.test.js` covers the drawer, so the plan-review path — the
  console's actual product — had no behavioural coverage at all.

- **Reported and fixed a `payload_cause` defect UPSTREAM, then collapsed this project's copy into
  it.** rlm-harness's `trace.payload_cause` documents itself as the read-side mirror of
  `ModelToolResult.cause`, and the two disagreed on the case that matters most: the write side has
  always read `endpoint_error is not None`, the read side shipped as `endpoint_error or error`. They
  differ on the EMPTY STRING, which is the common case rather than a corner one — the field is
  `str(exc)`, and that is `''` for `httpx.ConnectTimeout`/`ReadTimeout`/`ConnectError`,
  `TimeoutError`, `OSError` and `RemoteDisconnected`. Under truthiness every one of those read as a
  validator rejection: a dropped connection labelled a content decline. `dataset.export_actions`
  carried the same trap one field over (`a or b` skips a present-but-empty `endpoint_error`), so a
  record could reach a trainer saying `cause: endpoint` beside `error: null`. Fixed in rlm-harness
  `6d010447` with a test asserting the two agree over every shape — not merely that each behaves.
  `trace_io.draft_cause` had taken the kit's KEY SET while pinning the divergence rather than
  adopting it; a differential over all 81 cause-less payload shapes now shows ZERO disagreement, so
  it delegates and the local derivation is gone. The GUARD stayed: the six empty-string transport
  failures are still asserted, now pointing through the delegation at an upstream regression.
- **The TA criterion's description claims only what it counts, and it is training input.** It ended
  "the planner read before it wrote", which is stronger than the fact supports: `transcripts` and
  `memory_index` are REPL variables on the task's signature, so a planner can read every transcript
  in full without one read tool — and a measured run did (all three to the sub-LM at turns 0-2,
  drafting at turn 3, the first read TOOL at step 9). `rl_export.rubric_signal` carries this string
  into the SFT bundle at `sft_turns[*].input.initial.rubric[*].description`, so the wording is read
  by a model being trained. It now names the unit and the blind spot in the same breath, pinned by a
  test. (It never reached the eval judge — `judge.py` is rubric-free by design.)
- **rlm-harness pin `4fcd50b2` → `fe00f401`.** Brings the classifier fix above, `export_actions`'
  `outcome.cause` / `outcome.error` (ADDITIVE — `rl_export` reads neither, and its own per-cause
  metrics keep coming from `draft_cause`), and the ROOT-CAUSE fix that followed: `make_model_tool`
  now records the exception TYPE when `str(exc)` is empty, so `endpoint_error` can no longer be
  present-but-falsy at all. Fixing the classifier stopped the misreading; this removes what made the
  misreading possible, and every consumer's error text gains a name where it had an empty string.
  Swept across all eight sibling projects: two carried the misreading through the shared helper,
  one had reproduced it independently in its own code, two were never exposed, and
  one had its `SystemExit` message reduced to a bare CVE id and a colon by it.

- **The studio's plan review is now a rail LIST + a middle STAGE, the sibling studios' shape.** The
  middle column used to render all ten candidates with every drafted body expanded: measured on a
  real run, **32,091 characters of draft across 10 rows**, each `<pre>` capped at 260px with its own
  scrollbar, inside a panel that ALSO scrolled. Reading candidate 5 meant scrolling the panel to it,
  scrolling inside its box, and being thrown to candidate 6 when the wheel escaped. Now: a scannable
  one-line list in the rail (index · action · name · `⚠`/`◆`), one candidate on the stage with an
  `Entry | Draft` switch, `↑`/`↓` to step. `key_fields` renders one row per field rather than one
  `JSON.stringify` line — its longest value is `reason`, a paragraph of model prose, which collapsed
  the whole object into an unreadable ribbon exactly where a reviewer must read most carefully.
- **One scroll track per panel, and the affordance that says a track continues.** `.panel` carried
  `overflow-y:auto` while its inner track did too, so every column had two nested scrollers and the
  wheel handed off mid-gesture. Panels are boxes now; scrolling belongs to one designated child, and
  every track carries the siblings' top/bottom `mask-image` fade. The old contract asserting
  `.candidate-draft` must cap its height and scroll ITSELF was replaced rather than relaxed: it
  encoded the layout just removed, and it passed against `max-height:none` while constraining
  nothing. The replacement pins "no nested scroller" and goes red under mutation.
- **The trajectory timeline encodes DURATION AS WIDTH.** It was a 260px side column of equal-height
  rows — the one place a duration can only ever be text. On the run this was rebuilt against, **one
  of nine calls took 242.6s of 313.4s (84.3%)** and the rest took 2-8s: a fact identical rows cannot
  show and a proportional strip cannot hide. Now a full-width horizontal strip with an axis, segment
  width = share of the run's wall clock (floored at 108px, scrolling horizontally rather than
  squeezing), a per-tool-family hue on each segment's top edge, and clickable `T<n> ▸` markers at
  turn boundaries. Plus a **search** over each turn's `reasoning + code + output` — one turn's REPL
  echo ran 16,038 characters, so "which turn touched X" was otherwise a manual read of every turn.
  A miss dims rather than being filtered out, because which turn matched is only meaningful against
  the run's sequence.
- **The meta column stopped rendering tool-call counts as conclusions about the planner.** Two
  headlines said more than their facts measure, the same mistake twice:
  * TA read `draft -> read`. Both numbers are step ids of TOOL CALLS, and the planner also receives
    `transcripts`/`memory_index` as REPL VARIABLES. A real run read all three transcripts IN FULL at
    turns 0-2 by printing the variable and escalating to the sub-LM, then drafted at turn 3 and
    called `list_memory_files` at step 9 — tool-wise draft-first, in substance evidence-first. Now
    `tools: draft first`, with a caveat block.
  * The unclaimed group read `0 / 3 read`. The FRACTION was the lie — it frames read-tool calls as a
    proportion of transcripts, asserting the rest went unread, which the trace cannot support. The
    numbers stay; the arithmetic is gone, and the group is named `outside the four criteria` after
    what it IS rather than after today's two members.
  Each module now carries the criterion's own `description`, **served** as `rubric_criteria` from the
  run's `run_start` meta rather than copied into `app.js` — a copy drifts the moment a criterion is
  reworded, silently, and reading it per run means an old trace explains itself with the rubric it
  actually ran under. Facts no category claims still surface, so a fact the server ADDS cannot
  vanish the way `n_transcripts` / `n_transcripts_read` did.
- **FIXED — a non-dict `payload["meta"]` raised out of `rubric_from_meta`.** `dict_events` normalized
  the event and its `payload`; `meta` is one level deeper and is the only payload key in this
  workspace unwrapped with `.get`, so `{"payload": {"meta": "nope"}}` — an object whose payload is an
  object, passing both filters — raised `AttributeError: 'str' object has no attribute 'get'`. It
  cost `GET /v1/runs/{id}` a 500 the moment that endpoint began serving the criteria, and it had
  already cost `ctx-distillery export` an entire bundle for one bad line. Fixed in `dict_events`, so
  both consumers inherit it.
- **The `TRACES` header chip shows the path's TAIL.** The siblings' `22ch` head-truncation is right
  for a MODEL NAME and wrong for a PATH: it rendered `/Users/operator/Documents/…`, hiding the only
  segment a reader is checking. Now `…/ctx-distillery/traces`, full value on the `title`.
- **`CLAUDE.md` records what a rendered transcript actually contains.** Measured: user 153,285 chars
  vs assistant 27,443, while the RAW content is 305,816 vs 392,030. `tool_use` collapsing to a label
  is this renderer's deliberate choice; **`thinking` renders to nothing because Claude Code does not
  persist the thinking TEXT** — the block carries an empty `thinking` and a ~1,840-char crypto
  `signature`, and across 60 session files 2,384 thinking blocks had 0 with content. The renderer is
  correct; the gap is upstream, and is now stated instead of hiding inside "deliberately LOSSY".

- **Subagent-transcript distillation, OPT-IN** (`ClaudeCodeAdapter.for_project(...,
  include_subagents=True)` / `ctx-distillery distill --include-subagents`). The corpus this was
  designed against is 875 subagent transcripts across 11 projects; three of the findings below are
  corrections to things this project previously stated as fact.

  1. **Discovery is a RECURSIVE walk under `<project storage>/<session-id>/subagents/`, filtered to
     `agent-*.jsonl`** (`adapters/claude_code.py::subagent_files` -> `SubagentTranscript`). Both
     halves are the SDK's own (claude-agent-sdk 0.2.116, `_internal/sessions.py::_collect_agent_files`),
     and both are load-bearing: a FLAT glob — what this project's docs described — misses the
     `subagents/workflows/<run-id>/` files, **34% of the corpus**; and `rglob("*.jsonl")` (the shape
     the SDK's store-MIRRORING helper uses) ingests the 9 `journal.jsonl` run-journals as if they
     were transcripts. The first-party specification existed on this machine the whole time; a
     `grep -rn subagents site-packages/` would have found it. **Look for a first-party spec before
     treating local observation as the ceiling.**
  2. **Containment is anchored on the STORAGE directory, with an exact-parent hop at BOTH levels**
     (`session_dir.resolve().parent == storage`, then `subagents.resolve().parent == session_dir`),
     and a prefix test only for the depth that is legitimately unbounded. The obvious form — resolve
     `subagents/` and `is_relative_to` it — was written, argued for ("both operands are resolved, so
     nothing can fool the prefix test") and REPRODUCED FAILING: resolving the root makes it move WITH
     the symlink. Three arrangements escaped into LM context (`subagents/` symlinked outside, the
     session directory symlinked outside, and `subagents/` symlinked to the storage directory's own
     `memory/` — the last defeats any check phrased as "did we stay under the project's directory"),
     and all three are now fixture tests that go red against that form. Un-resolving the root instead
     silently returns ZERO files on macOS (`/var` -> `/private/var`); both sides of each `==` are
     resolved.
  3. **The sidecar degrades per FIELD, never per file.** `agentType` + `spawnDepth` are the only
     REQUIRED keys (874/874); `description` and `toolUseId` are present on the 575 flat files and
     NONE of the 299 nested ones, and `parentAgentId` on only the 44 flat files at depth > 1. The
     parent link therefore has THREE cases tested PATH-FIRST — `workflow:<run-id>` / `agent:<id>` /
     `session:<id>` — because "depth 1 means the session owns it" is confidently wrong on a third of
     the corpus.
  4. **`render_transcript_events` / `render_transcript_file` gained `include_sidechain: bool = False`**,
     and the main-thread rendering is BYTE-IDENTICAL with the default. `isSidechain` is the field
     that separates the two STORES, not noise: `False` on 0 of 57,928 main-thread user/assistant
     events, `True` on 72,126 of 72,126 subagent ones — so the shipped renderer returned exactly 0
     characters on all 874 subagent files. The filter is turned OFF explicitly rather than deleted.
  5. **Each subagent becomes its OWN `transcripts[]` entry with a 3-line header** (index line / full
     identity + SDK-shaped `subpath` / `task:`), ordered session-then-its-own-subagents. Separate
     entries make redaction, auditability and judgeability STRUCTURAL — the text lands in the one
     list `run_distillation_artifacts` redacts, `read_transcript_chunk` closes over, and `eval/`'s
     judge reads. A new `RawSession` TEXT field would have bypassed all three (invariant 11's failure
     mode, for the third time); folding into the parent's text would blind the audit trail and make
     the largest entry 7 M characters, i.e. 351 sequential chunk reads against a 30-turn budget.
  6. **Orientation is an index line, bounded by `INDEX_LINE_MAX = 86`** — measured over the real
     corpus (subagent lines 65..86, median 76; session lines 22) — with the implied entry ceiling
     (`CD_MAX_OUTPUT_CHARS // (INDEX_LINE_MAX + 1)` = 459 by default) asserted from the SAME constant
     in the same test, and a `cli` warning above it. A `transcript_manifest` signature input was
     rejected: it would get the same fixed 1000-character `REPLVariable` preview, break every direct
     caller of `arun` (dspy raises on a missing input field), and duplicate the headers.
  7. **`RawSession.transcript_ids` + a CONDITIONAL `run_meta["transcript_index"]` stamp.** An ordered
     `{kind, id, session, parent}` list — four `str` fields, all derived from filenames, nothing
     model-authored — so a reviewer holding a trace can finally answer "what was transcript 7?".
     Nothing anywhere mapped that positional index to a file before. The stamp is conditional
     because an unconditional one makes `[]` mean two different things ("no identities reported" vs
     "no transcripts"), and every non-Claude-Code adapter would write it.
  8. **`PLANNER_PROMPT_VERSION = "ctxd-planner-v2"`** (`task.py`), stamped by the DRIVER into every
     run's `run_meta`. The instructions gained the findings-vs-agreement distinction — a subagent's
     FINDINGS are ordinary evidence, its AGREEMENT with its parent is an echo and never "multiple
     transcripts independently confirm", and a CONTRADICTION is a strong signal — plus the index-scan
     paragraph. `eval/`'s `PROMPT_VERSION` deliberately does NOT bump for the input change.
  9. **`eval/`'s judge was uncapped end to end, and that already failed WITHOUT this feature**: a
     cve-reverser run is a ~10.6 M-character prompt main-only. It fails as an ENDPOINT error, which
     `make_model_tool` is explicit must not trip the breaker — so a batch burns a full retry cycle
     per row returning nothing. `build_prompt` now caps the plan and each transcript and the total,
     with index-preserving `--- transcript {i} (elided: N chars) ---` stubs (dropping entries would
     renumber the labels), applied BEFORE the call. `PROMPT_VERSION` -> `atlas-ctxd-eval-v3`, and the
     caps ride in the scorecard footer as provenance.
  10. **`rubric.trace_facts` gained `n_transcripts` / `n_transcripts_read`**, closing a blind spot
      that predates this feature and that it makes worse: a run that read 2 of 414 transcripts and
      one that read 2 of 2 produced identical ATLAS facts. Both are deterministic trace-derived
      counts, neither decides met/unmet. This widens the module's sourcing to THREE places
      (`assemble()`, `tool_call` events, and now `run_start.meta`), stated rather than left to drift.
  11. **`studio/` renders the COMPOSITION** (`sessions=a subagents=b`) beside the count, in the feed
      and the Trajectory drawer, through ONE shape-guarded reader (`mapper.transcript_composition`,
      imported by `iterations.py`, not copied). Absent AND malformed both degrade to `None`, never to
      zero — an old trace carries no identity list, and reporting `0/0` would be a claim it never
      made.

  Default OFF, on two decisive grounds: `transcripts` is positional, so flipping it silently
  renumbers every entry and `read_transcript_chunk(3, …)` names a different conversation before and
  after; and redaction is pattern-based and admits false negatives by construction, so shipping ~1.5x
  more text — and up to 18.5x more ENTRIES — to a remote model is an operator's explicit act. Not a
  `CD_*` env var: that surface is models and budgets, this is input selection, and a CLI flag is
  visible in the shell history that produced the trace.

  **This used to say "Not built" here — that is now FALSE; see the newer, top-of-file entry on the
  eval scorecard's per-row `transcripts=<n> (sessions=<a> subagents=<b>)` suffix.** The reasoning
  below (ill-defined as an `EvalReport`-level aggregate, well-defined per row) is what that entry
  built against, so it stays rather than being deleted — but do not read this bullet as current
  status.

- **FIXED — a symlinked `~/.claude` silently yielded ZERO transcripts** (`claude_home`). The helper
  was `Path.home().resolve() / CLAUDE_DIRNAME`, which resolves the home component and leaves
  `.claude` itself unresolved; `transcript_files` then compares a RESOLVED session path's parent
  against that unresolved storage directory, the parents never match, and every session file is
  filtered out. `storage.is_dir()` is still True, so no "no storage here" branch fires and the run
  reports nothing found. Anyone who keeps `~/.claude` in a dotfiles repo or on an external volume —
  a common arrangement — got an empty distillation with no error. Now `(Path.home() /
  CLAUDE_DIRNAME).resolve()`, with a regression test whose CONTROL is the same content behind a real
  directory (measured pre-fix: 1 transcript vs 0). Pre-existing, unrelated to subagents, and found
  only because review asked why `subagent_files`' own `.resolve()` calls could not be mutated into
  failure — the answer being that its caller had already refused everything. A defence that cannot
  be made to fail is not necessarily strong; it may just be unreachable.

- **A drafting call's cause is now RECORDED by the source and READ by every consumer, and the two
  places that used to derive it are one shared helper.** rlm-harness `4fcd50b2` added
  `ModelToolResult.cause` (`"ok"` / `"invalid"` / `"endpoint"` / `"circuit_broken"`) and
  `.validator_ran`, plus the `CAUSE_*` constants; the pin moved to it. Three changes, in order of how
  much they matter:

  1. **`tools/drafting.py` records `cause` + `validator_ran`** onto every drafting `tool_call`,
     beside the `endpoint_error` / `circuit_broken` it already wrote, and `_errors_with_infra`
     branches on `result.cause` instead of re-deriving from the two flags. This module is the only
     one holding a live `ModelToolResult`; recording what it already knows beats every downstream
     reader reconstructing it, and makes a fresh trace self-describing. Both are PROPERTIES upstream,
     not dataclass fields — they never reach a trace unless someone puts them there.
  2. **`rl_export._draft_cause` and `schema._not_ok_problem` no longer each derive the cause** —
     both call the new `trace_io.draft_cause`, which PREFERS the recorded `cause` and falls back to
     rlm-harness's own chain (`circuit_broken` → `endpoint_error is not None` → `ok`) for the traces
     recorded before the key existed. This is the part that was a real finding rather than an
     adoption: the two derivations AGREED on every payload shape in the suite, but nothing pinned
     that they must, and one implementation per job is `CLAUDE.md` invariant 11. The argument came
     from a sibling consumer of the same kit, against itself: it had made the same collapse in two
     places and "fixed" it once already — first counting every `ok is False` as a gate rejection (a
     real trace showed calls=3, breaks=7, rejections=10, a "rejection rate" of 3.33), then excluding
     breaks, which looked correct while still counting an ENDPOINT failure as a gate rejection in a
     field named for the gate. A partial fix that looks complete is the more dangerous state, because
     nothing prompts a second look. `tests/test_draft_cause.py` pins both directions — identity (the
     same function object; `rl_export._draft_cause` must not come back) and behaviour (over five
     payload shapes, `_not_ok_problem`'s wording and `run_metrics`'s bucket name the same cause).
     Verified by sabotaging each surface in turn: re-forking `schema`'s derivation with the old
     truthiness bug reddens 2 cases, re-forking `rl_export`'s reddens 4.
  3. `run_metrics`'s counters now count rlm-harness's constants directly, and `draft_not_ok` is the
     complement of the `CAUSE_OK` count rather than "everything the classifier declined to label" —
     the four causes partition the calls, so the slices sum to the aggregate as arithmetic rather
     than as a property this module has to defend. The public metric key names are unchanged.

  **Correct-by-construction, not observed — stated plainly because the sibling's report had the same
  limit.** Their five real traces contained zero endpoint failures, so their second bug never fired
  on real data. Ours are the same: the two real live-run traces
  (`demo-durable-fact` / `demo-one-off-debugging`) carry six drafting calls between them, ALL
  `ok=True`, with zero endpoint failures and zero circuit breaks. Nothing in this change fixes an
  observed wrong number in a trace we hold; it removes the second place a future fix could drift out
  of sync.

  `rubric.trace_facts`'s `any_circuit_broken` deliberately did NOT move to the shared helper — it
  asks "did the breaker trip anywhere in this run", a run-level existence check, not a per-call
  classification. A comment at the fact says so, so a future reader does not "finish the job"
  wrongly.

- **`ctx_distillery/skills/memory-vs-skill-criteria.md` was reported as claiming the file is loaded
  via `load_skills_as_tools`; it is not, and nothing changed.** The sentence is a NEGATIVE claim —
  "not as something `ctx_distillery.task.DistillSession` reads via `load_skills_as_tools`" — and it
  agrees with `VENDOR.md`. Recorded here so the same misreading does not get "fixed" next time.

- **`apply_plan` no longer lets a raw `OSError` escape from a model-drafted skill name, and all four
  sluggers in the workspace now bound their output at 120 characters.** `_skill_target`'s final
  `is_symlink()`/`exists()`/`is_dir()` check sat OUTSIDE the `try/except (OSError, ValueError)` whose
  own comment states the guarantee it broke — *"a refusal is the right answer to an unusable slug —
  never an exception escaping `apply_plan` halfway through a run of candidates."* `_ignore_error`
  swallows only ENOENT/ENOTDIR/EBADF/ELOOP, so ENAMETOOLONG (reproduced at **300** characters, not
  only at 5000) came straight out of a multi-candidate `--approve 0,3` run as a stack trace, leaving
  it half-applied. The input is `slugify(frontmatter["name"])` — untrusted MODEL output, which is
  what made it reachable from a real plan; `promote_to_memory` and `prune` already refused correctly.
  Two independent fixes, both kept: the check is inside the `try` (so ANY caller deriving a slug
  differently still hits a wall), and an over-long slug is now bounded before it gets there. The
  bound closed the last two unbounded sluggers — `ctx_distillery.cli._slug` (reachable via
  `--run-id`, and the WRITE side: driving `_cmd_distill` really did raise) and `apply.slugify`.
  **They bound it DIFFERENTLY, and the asymmetry is deliberate rather than an oversight**: the three
  run-id sluggers TRUNCATE at their own 120 (`cli._slug`'s `_RUN_ID_MAX`, `studio.app._slug_id`'s
  `_RUN_ID_MAX`, `eval.cli._slug`'s `_TASK_ID_MAX` — none of them references `_SLUG_MAX`, which is
  `apply.py`'s own and belongs to the one that does NOT cut), while `slugify` does not truncate at
  all — `_promote` and
  `_promote_skill` REFUSE a slug longer than it. A run id is machine bookkeeping, so shortening it
  loses nothing a human was relying on; a promotion slug is the identity the operator approved, so
  installing under a shortened one substitutes a name they never saw, which is the same failure the
  empty-slug branch already refuses. `eval`'s docstring had cited `ctx_distillery.cli._slug` as
  "same reasoning" while that function had no bound at all; the cross-references are now true rather
  than aspirational.

- **`endpoint_error` is tested with `is not None`, never for truthiness — in all three places.**
  rlm-harness declares it `Optional[str]` and fills it with `str(exc)`, which is the EMPTY STRING for
  `httpx.ConnectTimeout`/`ReadTimeout`/`ConnectError`, `TimeoutError`, `OSError` and
  `RemoteDisconnected`. Under truthiness every one of those fell through to the validator branch:
  reported to a human as ``artifact 'a1' failed its format check: no detail recorded``
  (`schema._not_ok_problem`), counted in `draft_validator_rejects` as TRAINING SIGNAL
  (`rl_export._draft_cause`), and echoed with no message at all (`drafting._errors_with_infra`) —
  precisely the harm invariant 12 exists to prevent, arrived at through an empty string rather than
  a wrong branch. Severity is bounded (the shipped CLI uses the OpenAI SDK, which always supplies a
  message), so it is reachable only via a caller-supplied `chat_fn`. `_draft_cause`'s
  `validator_reject` fall-through is KEPT but now documented as a deliberate RESIDUAL with its own
  exactness argument, and the dead `_DRAFT_CAUSES` tuple that carried the partition claim (setting it
  to `()` left the suite green) is deleted in favour of stating it next to the chain that creates it.
  `schema._not_ok_problem`'s documented circuit-over-endpoint PRECEDENCE is now pinned too — swapping
  the two branches used to leave everything green, unlike its `rl_export` twin.

- **`rubric.py`'s TG criterion no longer says "format-valid" for a cause-blind fact.** It was a THIRD
  cause-blind surface and, unlike the two invariant 12 already lists (`apply._blocking_problem`, the
  studio's `applyBlocker`), it is not gated behind a `problems`-first check — it goes to the eval
  judge unconditionally on every run. The fact behind it is `draft_ok`, which answers "did this call
  yield usable bytes", the same answer for all three causes; the description now says that instead.

- **Seven measured figures corrected across the docs, each independently verified**, and each now
  stated once at its source rather than restated per site: only **two** of the seven tier-one patterns
  are genuinely unavailable from gitleaks (`bearer_token`'s lookbehind, `secret_assignment`'s
  replacement semantics), so the redundancy count is **five**; the golden corpus carries **54** rule
  ids, **40** with a string the rules match and **17** with an upstream true positive; the reference
  transcript is **426 KB**; the entropy delta is **33**.
- **Test-quality gaps closed, each verified by mutation.** `_excerpt`/`_SAMPLE_ECHO_CHARS` (the
  truncated `sample` echo) had ZERO coverage — deleting the truncation left the suite green, so the
  mitigation was a comment; now pinned in both directions. `test_redact_golden.py`'s end-to-end
  assertion checked only `"[REDACTED:" in out`, which passes for ANY label — 12 of 120 samples are
  redacted under a *different* rule's label, and each is now an enumerated row in
  `_COVERED_BY_ANOTHER_RULE` with a second test refusing a stale row.
  `test_the_growth_gate_and_the_budget_gate_each_catch_what_the_other_misses` failed 1 run in 3 under
  20-way CPU contention (the polynomial pattern got refused by the growth gate); it now tests each
  gate with THE OTHER ONE DISABLED, which is both deterministic and a literal statement of the claim.

- **A THIRD, operator-supplied redaction tier — `CD_REDACTIONS`, following toolscout's
  `TS_TOOLSPACE` convention with ONE deliberate divergence: it may only ADD.** Point the variable at
  a JSON file of `{label, regex, description, sample, replace_group?}` rules and they run after the
  7 hand-written patterns and the 120 vendored gitleaks rules. `redactions.example.json` ships at the
  repo root the way `toolspace.example.json` does in toolscout — `cp redactions.example.json
  redactions.json`, edit, point at it; the copy is git-ignored like `.env`.

  **The divergence is the whole design decision, and it is written down in four places so nobody
  "fixes" it back into symmetry** (`.env.example`, `README.md`, `redact.py`, `CLAUDE.md` invariant
  3). `TS_TOOLSPACE` REPLACES toolscout's built-in catalog, which is safe there because the worst
  case of a bad toolspace is FEWER TOOLS. Here the worst case is a LEAKED CREDENTIAL. So there is no
  `disable` key — the rule key set is CLOSED, so an unknown key is a hard refusal and one cannot be
  smuggled in later — no label shadowing, and no ordering knob. An operator can never turn tier one
  or tier two off.

  **`sample` is MANDATORY and EXECUTED at load. This is the single most valuable line in the
  feature.** A regex that compiles but never matches gives false confidence: the operator believes a
  shape is covered, the redactor removes nothing, and there is no error anywhere — the same failure
  class as the Airtable POSIX trap that motivated `scripts/port_gitleaks.py`'s strict compile,
  arriving through a different door. So every rule's own regex must redact that rule's own `sample`,
  or the load fails with `SystemExit` naming the file, the rule's position and its label. A second
  pass pins idempotence per rule, which is how `redact_transcript`'s existing "redacting twice is a
  no-op" promise survives an operator-supplied pattern.

  **ReDoS is refused at LOAD, not survived at run time — on TWO gates, because an adversarial review
  got past a single one three different ways.** Operator regexes run on Python's BACKTRACKING `re`
  over ~500 KB transcripts, and `re` has no timeout; the `regex` module does, but redaction is on the
  CORE path (`session.run_distillation` calls it unconditionally) and this project does not put a
  dependency there. So the mitigation is CALIBRATION: after compiling, each rule is run against an
  ascending ladder of short synthetic adversarial probes, 2→32 characters, and refused on either of
  two gates.

  *Gate 1, the GROWTH RATIO*, is the primary detector and it is what bounds what any ONE probe may
  cost (the AGGREGATE bound is the derived-family caps, below).
  Catastrophic backtracking is exponential in the input length, so it shows up as a blowing-up ratio
  between adjacent probe lengths long before any single probe is expensive — every classic shape
  measures a consistent ~4.0x per two extra characters. A rule is refused the first time a probe
  costs ≥ 5 ms AND ≥ 3x its own predecessor two characters shorter. *Gate 2, the absolute 20 ms
  per-probe budget*, catches POLYNOMIAL blow-up that grows too gently for the ratio and is ruinous
  anyway: `a*a*a*a*a*a*b` stays under 2x per step and still needs 22.8 ms at 28 characters.

  The 5 ms floor is the safety argument for the ratio: the slowest of the 127 built-in patterns needs
  ~1.6 µs for the grid's worst probe, a ~3000x margin, and a per-rule test pins that measurement, so
  gate 1 is structurally out of reach of a well-behaved pattern. A breach on either gate is re-timed
  and the SMALLEST measurement decides, because a refusal is permanent and a descheduled interpreter
  is not — this suite really did see a good rule refused by a scheduler spike under load.

  The three evasions this replaced, each reproduced before and after:
  - `CORPSECRETPREFIX-(\w+\s?)+$` — a literal marker plus a catastrophic tail, which is the MOST
    COMMON operator rule shape (it is what `redactions.example.json`'s own `corp-internal-token` is,
    and what any `X-Internal-Auth:` rule is). Probes of one repeated character never spell the
    marker, so the match failed at position 0 and the tail was never reached: calibration passed in
    0.3 ms while the rule really cost 1172 ms on `"CORPSECRETPREFIX-" + "a"*24`. The grid now emits
    a REACHING PREFIX, walked out of the pattern's own parse tree, with filler after it.
  - `(?:abcdef)?(z+z+)+Q` — `z` was the seventh distinct literal and derived seeds stopped at six.
    The cap is gone; every distinct literal character is a seed, and the count is bounded by the
    pattern's own length anyway.
  - `(a?a?a?a?a?a?a?a?a?a?)+$` — already astronomical at the SMALLEST probe (16.8 ms at 4 characters,
    3.3 s at 6). The old ladder started at 12 and measured elapsed time only after `search` RETURNED,
    so this did not fail the budget, it HUNG the import outright (still running at 120 s). Starting
    at 2 characters is what makes the ratio gate see 0.09 ms → 22 ms and refuse in ~40 ms.

  **The probe PREFIX is derived from the parse TREE, not scraped from the pattern text — and the
  probe COUNT is capped.** Both were found by a THIRD adversarial review, both reproduced through the
  real loader before and after. The first fix scraped literal RUNS of 3+ characters out of the
  pattern source, which a real operator rule can trivially fail to contain: `X-(\w+\s?)+$` (a
  2-character marker, under the minimum) loaded in ~107 ms and then hung for over 15 SECONDS on a
  46-character line, and `ORG-[0-9]{4}-(\w+\s?)+$` (a character CLASS inside the marker — the
  scraped run was `ORG-`, and no filler spells `1234-`) did the same. `XY-(\w+\s?)+$` WAS caught,
  which is exactly what made the 3-character cutoff invisible. Lowering the minimum to 1 would have
  closed only the first. `ctx_distillery/regex_walk.py` now walks the pattern's own `re` parse tree,
  emitting a concrete string for each literal, class and bounded repeat and recording a prefix at
  EVERY ambiguous quantifier — so a marker behind an earlier quantifier (`\d+-CORPSECRET-…`) is
  reachable too, as are backreferences and leading lookbehinds. It is ONE implementation shared with
  `scripts/derive_liveness_samples.py` (invariant 11), whose fixture regenerates byte-identically.
  Separately, the old scraper's prefix count was UNBOUNDED in pattern length: a 7,919-alternative
  pattern produced **1,014,880 probes and a 16.8 s load**, every individual probe cheap and gate 1
  therefore silent. Seeds were already bounded by the alphabet; prefixes are now capped by
  `_MAX_REACHING_PREFIXES`, and the same pattern produces **4,896**. Zero false refusals across the
  127 built-ins plus 24 realistic org-credential patterns (prefixed tokens, header/lookbehind rules,
  UUID/base64/JWT shapes, DSNs, alternation-heavy vendor lists); all 151 calibrate in 124 ms total.

  Calibration still runs BEFORE the sample check, because the sample check executes the pattern.
  Stated honestly in the module, including the gaps that are NOT closed: a contrived pattern can still
  evade any finite probe set; the load cost is bounded only down to the smallest probe, because
  nothing can time a search without running it; and the filler following a reaching prefix is a fixed
  set, so `X-(z+z+)+Q` is reached but not fed.

  **A tier-three `sample` must be SYNTHETIC, and now the docs say so.** It is the one field the
  schema requires to be credential-SHAPED, and a load refusal echoes it to stderr — into scrollback,
  a CI log, a crash report — with nothing anywhere previously warning against pasting a real one.
  `.env.example` and `README.md` both say to invent a token of the right shape, and the echo is
  capped at 64 characters as a mitigation rather than a licence.

  **`replace_group` — a closed vocabulary, still data, never code**, and finding it is *why* the
  schema has five keys rather than two. `secret_assignment` keeps `password = ` and replaces only the
  value; that is the one built-in a plain `{label, regex}` pair cannot express. Its pattern already
  uses named groups, so `"replace_group": "value"` says "substitute only the named group `value`,
  leave the rest of the match intact". Legal values are exactly `null` and a group the rule's own
  regex declares — anything else is refused at load, listing what IS available. Implemented
  generically, and independently useful: it is how you redact the value after `X-Internal-Auth:`
  while keeping the header name.

  **`redactions.example.json` is a REAL working file, not a fill-in-the-blank skeleton** — toolscout's
  example is what its own README tells you to point at, and ours has to hold that standard. It
  carries all seven built-in patterns expressed in this schema (the dogfood; including
  `secret_assignment` with its `replace_group`, which proves the field is necessary — a test asserts
  the generic substitution is byte-identical to the hand-written `_replace_assignment`) plus one
  illustrative `corp-internal-token` entry showing what an operator would actually add.
  `tests/test_redact_operator.py` loads it through the REAL loader and re-asserts every rule's
  sample, which is what keeps it from rotting.

  **The 4/7 overlap between tier one and tier two is now DELIBERATE rather than incidental.**
  Empirically, 4 of the 7 hand-written patterns are also covered by the vendored corpus
  (`private_key`→`private-key`, `aws_access_key_id`→`aws-access-token`, `github_token`→`github-pat`,
  `google_api_key`→`gcp-api-key`) and 3 are not (`bearer_token` — RE2 cannot express its lookbehind;
  `api_key` — private-proxy `sk-<uuid>` shapes are in no vendor corpus, measured against a real key;
  `secret_assignment` — replacement semantics). All 7 stay: tier two is REGENERATED from a moving
  upstream, so a refresh that renamed, narrowed or dropped `github-pat` would otherwise reduce
  coverage silently. `test_redact_golden.py` now carries an `_OVERLAP` table asserting both halves, so
  such a refresh surfaces as "this shape is now TIER-ONE-ONLY" instead of as nothing at all. Note
  `google_api_key` is not even fully redundant today: gitleaks' `gcp-api-key` requires a terminator
  from a fixed set where tier one ends on `\b`, so `AIza<35>]` is tier-one-only — also pinned.

  No new runtime dependency (stdlib `re`/`json`/`time`/`os`), no network, and `redact.py` stays
  inside `tests/test_no_write_capability.py`'s mutation scan — the tier reads a file and never writes
  one. The variable is resolved at IMPORT time and a broken file stops the process (`import
  ctx_distillery` exits non-zero, asserted in a subprocess): fail-closed, the same stance
  `_load_gitleaks_subset` already takes about a missing vendored artifact, because a redactor that
  silently gets weaker is the failure this module exists to avoid. **All THREE workspace suites** pop
  the variable at conftest IMPORT time — root, `eval/` and `studio/` — so none of them can inherit a
  developer's private rule file. That fix originally landed in the root suite only, and an
  adversarial review showed what the other two cost: both import `ctx_distillery` during COLLECTION,
  so a BROKEN file turned each into a collection-time `INTERNALERROR` and a VALID one just made them
  quietly non-hermetic. An autouse fixture cannot fix it — `redact` resolves the variable when the
  MODULE is imported, long before any fixture runs — so `eval/tests/conftest.py` pops it beside its
  existing `CD_*`/`CDEVAL_*` scrub list (`CD_REDACTIONS` joins that list too, and the list's drift
  test now scrapes `redact.py` as well as `config.py`, which is the blind spot that hid the name in
  the first place), and `studio/` gets a `conftest.py` whose entire job is that one line. Each member
  asserts its own hermeticity (`redact._TIER3 == ()`) rather than trusting the arrangement.
  Cost on a 500 KB rendered transcript: ~135 ms for tiers one and two, **+5.6 ms for one operator
  rule** (+49 ms for the example file's full 8); loading and calibrating those 8 rules takes 4.4 ms.
  `tests/test_redact_operator.py` carries **361** of them (this number is MEASURED, per a review
  that found the earlier `+221`/`+584` pair understated the batch by ~400: the two redaction test
  files hold 1,208 tests between them against a pre-batch root suite of 434).

- **Host-side redaction (invariant 3) goes TWO-TIER: 7 hand-written patterns + 120 gitleaks rules,
  vendored under MIT and graded against gitleaks' own regression corpus.** `redact.py` used to be
  seven regexes and an honest disclaimer. It still is — first — and then runs
  `ctx_distillery/patterns/gitleaks_subset.json`, a mechanical port of the ANCHORED subset of
  gitleaks `v8.30.1`'s rules (commit `83d9cd684c87d95d656c1458ef04895a7f1cbd8e`), generated by the
  checked-in `scripts/port_gitleaks.py`. Measured on shaped provider tokens, coverage went
  **4/14 → 14/14**; on a real 426 KB rendered Claude Code transcript the whole thing costs
  **~110 ms** (tier one alone: ~32 ms) with **zero false positives** on real content.

  **Why tier one had to stay, and stay first.** This was the finding that settled the design rather
  than an act of politeness to the old code. EXACTLY TWO of the seven are genuinely unavailable from
  gitleaks: `bearer_token` is anchored with a **lookbehind, which RE2 cannot express**, so no gitleaks
  rule ever will; and `secret_assignment` replaces the VALUE ONLY, keeping `api_key = ` legible.
  (`private_key` is NOT one of them — an earlier draft of this entry called it "a DOTALL BEGIN/END
  block matcher gitleaks has no equivalent for", which was checked and is FALSE: the vendored
  `private-key` regex ends `[\s\S-]{64,}?KEY(?: BLOCK)?-----`, RE2's own DOTALL idiom, and matched a
  full 202-character block at span `(0, 202)`. It stays in tier one for REFRESH RESILIENCE, the same
  reason the other four redundant patterns do.) And on the real transcript, the operator's own live API key — an
  `sk-<uuid-shaped>` **private-proxy** key — was matched by **none** of the 120 vendored rules,
  because it is not a published provider format. Tier one caught it. An anchored corpus of vendor
  prefixes is structurally blind to a key minted by whoever is in front of you. Running tier one
  first also keeps this project's own labels stable (`[REDACTED:github_token]`, not
  `[REDACTED:github-pat]`) no matter how the vendored corpus is refreshed later.

  **The load-bearing line in the porter is `warnings.simplefilter("error")`.** gitleaks'
  `airtable-personnal-access-token` is `pat[[:alnum:]]{14}\.[a-f0-9]{64}`. Handed to Python
  *unported* it COMPILES, emitting nothing but a `FutureWarning: Possible nested set` — and then
  means something else entirely (`[[:alnum:]]{14}` parses as a character set plus 14 literal `]`),
  so a real Airtable PAT never matches. In a scanner that is a missed finding; in a **redactor** it
  is a live credential flowing into a language model's context with no error anywhere. Compiling
  under `error` makes that class of silent mis-port a build failure, `redact.py` repeats the check
  at import, and `tests/test_redact_golden.py` pins the specific case in both directions (the
  unported form matches a real PAT *not at all*; the ported one does).

  **Two divergences from upstream, both deliberate, both argued next to the code.**
  (a) **Entropy floors are loaded for provenance and never enforced.** gitleaks tunes them for a
  SCANNER, where a false positive wastes an engineer's time; here a false negative ships a live
  credential, so the asymmetry is inverted — and since the port already dropped every rule whose
  confidence came from a nearby keyword, what is left is anchored on a literal prefix that IS the
  evidence. Measured against upstream's own corpus: all 85 true positives match either way, and
  dropping the floors redacts 33 more credential-SHAPED strings (documentation samples, `AIzaaaa…`
  placeholders) that cost the planner nothing. (b) **The keyword gate is kept exactly as upstream
  ships it** — an earlier draft here ungated any rule whose keywords its own pattern did not imply
  (to protect vendor-name-keyed rules like `airtable`), and on the real transcript that immediately
  redacted a genuine **git commit SHA**, because `sourcegraph-access-token` carries a bare
  `[a-fA-F0-9]{40}` alternative that only its keywords make safe. Reverted, and the measurement is
  recorded in the module. The gate's remaining false-negative direction is stated, not hidden.

  **What makes the vendored copy trustworthy rather than hopeful** is `tests/test_redact_golden.py`
  (**847** tests — measured, not the `+584` an earlier draft claimed): it replays gitleaks' OWN true/false-positive cases — scraped from upstream's Go
  generator by the checked-in `scripts/extract_gitleaks_golden.py` into
  `tests/data/gitleaks_golden.json`, 54 rule ids / 85 TPs / 180 FPs — against the ported patterns.
  The FPs are PARTITIONED on purpose: `fps_rejected` (109) must stay rejected, which is what catches
  a port transformation that silently WIDENED a rule into meaninglessness, and `fps_over_redacted`
  (71) records the over-redaction the dropped entropy floors and allowlists buy. A case moving
  between the two buckets goes red and forces a human to look.

  **That golden corpus carries a matching string for only 40 of the 120 rules, so every rule now
  also has a LIVENESS sample.** (54 rules have a case of SOME kind; just 17 have an upstream TRUE
  POSITIVE. An earlier draft said "45", which corresponds to none of the three counts.) Upstream
  hand-wrote `tps`/`fps` for the rules it felt like, which left 80 vendored rules
  with no test anywhere asserting they still MATCH anything — and an adversarial review showed the
  price: rewriting `adobe-client-secret`'s regex to the literal `ZZZ_MATCHES_NOTHING`, and separately
  widening its `{32}` to `{288}`, each left the whole suite green. A dead redaction rule is invisible
  by construction: nothing is removed, and nothing errors. `tests/data/liveness_samples.json`
  (generated by the checked-in `scripts/derive_liveness_samples.py`, which walks each rule's own `re`
  parse tree; 119 of 120 derived automatically, `kubernetes-secret-yaml` hand-written because its
  word boundaries need real YAML around them) pins one matching string per rule, and every rule is
  asserted to still match it AND to still be redacted end to end. The samples are CHECKED IN rather
  than derived inside the test on purpose: a sample derived from the rule's own current pattern moves
  with the rule, so the `{288}` mutation would regenerate a 288-character sample and stay green.
  This proves nothing about semantic fidelity to a vendor's real format — the golden TPs remain that
  gate — but it catches the dead-rule class, and `VENDOR.md`'s refresh recipe now regenerates and
  diffs the fixture as its own step.

  **The keyword gate's accepted false-negative direction is now ENUMERATED, and it is exactly two
  rules.** `redact.py` named `airtable-personnal-access-token` as "the" case where the gate is keyed
  on the vendor's NAME rather than on anything the token's shape implies; an adversarial review found
  a second, `facebook-access-token` (gated on `facebook`, pattern `\d{15,16}(\||%)[0-9a-z\-_]{27,40}`).
  The list is derived rather than hand-kept: a test checks each rule's liveness sample against its own
  gate and asserts the set is precisely those two, so a refresh that adds a third says so.

  **`_load_gitleaks_subset` gained the `re.purge()` its two siblings already document as
  load-bearing, and a test that pins the loader rather than the artifact.** `re.compile` caches by
  `(pattern, flags)` and a cache HIT never re-parses, so it never re-emits the `FutureWarning` the
  strict compile exists to catch — with the cache primed, a hand-edited artifact carrying the raw
  Airtable POSIX pattern imported cleanly and shipped a dead rule. Separately, the loader's
  `warnings.simplefilter("error")` was entirely unpinned: replacing it with `if True:` turned nothing
  red, because `test_every_ported_pattern_recompiles_with_warnings_as_errors` compiles the ARTIFACT's
  regexes itself and never runs the loader. There is now a test that drives the real loader against a
  hand-made artifact with the cache deliberately primed, under `simplefilter("always")` so only the
  loader's own filter can raise; each mutation turns it red on its own.

  No new runtime dependency (stdlib `re`/`json` only — redaction is on the core path and must not
  drag in an HTTP stack), and **no network call, ever**: TruffleHog's verify-by-POSTing-the-secret
  model is the exact inversion of a module that runs over the operator's own private history.
  `redact_transcript` stays idempotent and non-raising across both tiers, pinned over the whole
  golden corpus. `scripts/` sits outside `tests/test_no_write_capability.py`'s scan (the porter
  writes files; `redact.py` and everything else under `ctx_distillery/` stay write-free) — asserted,
  not assumed. Licence (MIT, Copyright (c) 2019 Zachary Rice), the pin, the transformation list,
  what was NOT taken (the 101 generic keyword-near-assignment rules, which need gitleaks'
  1,446-entry stopword allowlist; and `secrets-patterns-db`, whose CC-BY-SA-4.0 share-alike would
  poison this project's MIT status) and the **refresh command** are in `VENDOR.md`.

- **The studio's Trajectory drawer — the frontend half, REBUILT against `el()`/`clear()` rather than
  ported.** `studio/DESIGN.md` used to carry this as an explicit deferral ("*Not copied:* a §5.7
  Trajectory drawer … describing one would be fabrication"); the data layer
  (`studio/ctx_distillery_studio/iterations.py` + `GET /v1/runs/{run_id}/iterations`) landed first,
  and this closes it with a real §5.7, `static/trajectory.js`, drawer markup/CSS, and
  `studio/tests/trajectory.test.js`.
  **Why it could not be a port.** Every sibling studio's `trajectory.js` assigns `innerHTML` in
  **seven** places. This project forbids that absolutely (`app.js`'s header rule, `DESIGN.md` §7's
  first Don't, `CLAUDE.md` invariant 10), so the drawer was rebuilt node-by-node on the two helpers
  `app.js` actually has. **And here that rule IS the mitigation, not hygiene.** Pass 3's leak audit
  on a REAL live trace proved `timeline` and `initial` clean of paths, drafted bodies and evidence —
  and proved the opposite about turn text: **4 of 6 drafted bodies and all 6 evidence blobs appear in
  `iterations[*].code` / `iterations[*].output`**, because that text is the REPL's own echo (the
  planner printed a drafting call's return value and typed the evidence in as a literal). Surfacing
  turns is the drawer's entire reason to exist — `mapper.to_event` gives the feed `has_code: bool`
  and drops `output` outright — so the answer is RENDERING, not filtering. Said out loud in the
  module header, in §5.7, in §7's Don'ts and on screen in the REPL block's own caption, so nobody
  later reads Pass 3's leak tests as a promise that turn text is scrubbed.
  **The injected-deps roster is honest, not copied.** The siblings inject ten
  (`$`, `esc`, `ICONS`, `tint`, `fmtBytes`, `_linkify`, `formatElapsed`, `feedError`, `getRunId`,
  `isBusy`); `app.js` has exactly `el()` and `clear()` and no `esc` at all — nothing is escaped
  because nothing becomes markup — so the roster is `{ el, clear, getRunId, onError }`. `getRunId`
  stays a **getter, never a construction-time snapshot** (this page can load a second run without a
  reload), and because nothing else in the drawer touches a dep before the handle is clicked, the
  four are validated at CONSTRUCTION with an explicit `TypeError` — the siblings get that for free
  from calling an injected `$` while building their element map, and we would not have.
  **The timeline is FLAT and unconditional; `turn_index` is only an enrichment.** Forced by the two
  measured numbers `iterations.py` records: a real live run spans 20.4s → `per_turn_timing = true`,
  the offline scripted harness spans 0.0019s → `false`, and `turn_index` back-mapping runs only when
  it is true. So on EVERY trace this workspace's tests can produce, no timeline entry has one at all
  — a drawer reaching tool calls only through turn grouping would render an empty pane exactly there
  (an audit caught precisely that in an earlier design). Nothing about whether an entry renders reads
  `turn_index`; only the `.related` cross-highlight does, and it simply switches off. Pinned by
  `trajectory.test.js`'s offline-shaped fixture. Timing is stated rather than dressed up too:
  `timing_note` renders VERBATIM (`per_turn_timing` picks the tag and nothing else), an untimed turn
  says so instead of showing a zero, and every timeline entry carries the caveat in words — a
  `duration_s` is the **gap since the previous recorded event, planner-think + tool-exec**, because
  this project has no per-call instrumentation and calling it a tool latency would be a fabricated
  measurement.
  **Not built, each for a stated reason:** no `replay-core.js`, no ▶/⏸/speed transport, no progress
  bar, no expand-to-full, no in-drawer search, no `run-core.js`, no icon set, no vendored font, no
  model-role chips. A transport's payoff scales with tool-call count and this project's runs make a
  handful of calls; `app.js` has never even used the server's existing `?delay=` pacing. The ←/→
  stop-walk survives as ~12 INLINED lines (`buildStops` / stop index / step target) — vendoring
  `replay-core.js` to use a quarter of it would mean carrying a replay engine for a keyboard
  shortcut. One nuance recorded rather than glossed: §5.7's Init pane DOES show `planner`/`drafter`,
  which is not a breach of the "no model-role chips" Don't — those are recorded facts about one past
  run read from its own `run_start.meta`, rendered as `kv` rows, where the header chip would have
  fabricated a field `/v1/config` does not return.
  **The enforcement hole was closed in the same pass, which is the real lesson here.**
  `studio/tests/static-contract.test.js` read `static/app.js` and only `app.js`, so a brand-new
  `trajectory.js` — the file with by far the strongest pull toward markup — would have sailed past
  the no-`innerHTML` assertion reporting "ok". The scan now walks every `static/*.js`, still matching
  on CODE SHAPE rather than the bare identifier (both files NAME `innerHTML` in prose while promising
  never to use it), and a companion assertion pins that the scan actually FOUND `app.js` and
  `trajectory.js` — the same "a scan that reads nothing reports no offences" failure mode
  `tests/test_no_write_capability.py` was already hardened against on the Python side. Mutation-tested
  by planting `x.innerHTML = y` into `trajectory.js`: red, exit 1, restored byte-exactly. Two CSS
  contracts joined it — `.traj-well` needs the same `overflow-wrap:anywhere` + height cap + own
  scroll as `.candidate-draft`, since a turn's REPL echo is the same class of untrusted text.
  **`studio/tests/trajectory.test.js`** is the factory's DI contract on the siblings' harness shape
  (plain `node <file>`, CommonJS, `require("assert")`, no npm): facade `{ open, reset, showHandle }`,
  a missing dep refused per-name, the `getRunId` getter re-consulted (mutated between construction
  and call, and again between construction and `open()`, asserting the fetched URL), the full
  offline-trace timeline, the verbatim note, a hostile drafted body surviving as text through the
  REPL well, both fetch-failure paths reporting through `onError` without opening an empty drawer,
  and an empty envelope rendering rather than throwing. Its two adaptations are recorded in its
  header: the memoization the siblings put in an injected `$` moved into the stub `document`
  (`app.js` has no `$`), and there is no `ReplayCore` stub or `refreshTransport` entry to assert.
- **`ctx-distillery-eval run` + a real taskset — the eval member's three recorded blockers, closed
  additively.** All three fixes are ADDITIONS beside what shipped, not replacements of it.
  - **`session.run_distillation_artifacts` + `DistillArtifacts`.** The driver's `redacted_transcripts`,
    resolved `run_id`, and just-loaded `events` used to die as locals. They are now returned, and
    `run_distillation` is a one-line wrapper whose signature AND return type are unchanged — verified
    to need zero call-site edits (`cli.py`, `tests/test_session.py`, `tests/test_cli.py`'s
    monkeypatch double, `studio/`, `tests/test_public_api.py` all unmodified by it). The redacted
    transcripts are the load-bearing field: re-`ingest()`ing and re-`redact()`ing would score against
    a DIFFERENT redaction than the run saw, and reading them back from the trace is permanently ruled
    out on this repo's own record (`tools/transcript_reader.py` records offset/length and "never the
    text itself — that is the audit point"), so a trace-sourced substitute would be EMPTY, not
    lossier. These fields deliberately do NOT go on `AssembledPlan`: `render.plan_as_dict` is
    `dataclasses.asdict`, so transcript bodies would have landed in `ctx-distillery show --json`.
  - **`taskset.EvalTask` / `load_taskset` / `demo_taskset`, beside the existing `Task`/`collect_tasks`.**
    `project` is optional (a `{id, reference}`-only taskset is legal, and now useful); `run` refuses a
    task without one loudly. `project.claude_home` is its own overridable field because invariant 6
    forbids reading the machine's real `~/.claude`. **`demo_taskset(root)` MATERIALIZES**, alone in
    the family: a Claude Code project's storage directory is named from its ABSOLUTE path, so no
    static JSON constant can name it. The signature takes `root` for a lifetime reason — `mkdtemp`
    would leak a tree per invocation and a `TemporaryDirectory` would delete the transcripts before
    `run` read them — so the caller owns it (`run` passes `--out/demo`, tests pass `tmp_path`). Only the
    LAYOUT is generated; the transcript CONTENT stays checked in as
    `eval/ctx_distillery_eval/demo/*.jsonl`, keeping the demo taskset reviewable data. Its two tasks
    are the two real failure modes: a session of durable conventions that must be promoted, and a
    self-resolving debugging exchange that must not be over-promoted.
  - **`judge.build_prompt` grew a third positional `reference`, and `PROMPT_VERSION` bumped to
    `atlas-ctxd-eval-v2`** — the v1 docstring named this exact change as the reason the constant
    exists, and `eval/tests/test_judge.py`'s pin was updated deliberately, with the reason recorded
    in its docstring. **The `=== REFERENCE ===` section renders ONLY when there is one**, where all
    three siblings render it unconditionally with a `"(no reference provided; …)"` fallback: theirs
    always have a taskset, and this project's primary path (`score` without `--taskset`) does not, so
    a no-reference run keeps rendering the byte-identical v1 prompt. When it IS rendered,
    `REFERENCE_TRUST_RULE` is appended to `UNTRUSTED_DATA_RULE` — which enumerates exactly two
    untrusted bodies and would otherwise be silent about a third — stating that a taskset reference
    is TRUSTED input (a human wrote it into a checked-in file, unlike model output or somebody else's
    session) while still never a licence to change the scale or the output format. `Judge`,
    `StubJudge`, `make_eval_judge`'s inner judge and `score_run` all widened identically.
  - **`score --taskset` is an OPTION, not a third positional.** Every sibling's `score` takes a
    taskset positionally; ours cannot, because the shipped contract is `score <trace_glob>
    <transcript_path>...` and those must not move. Without this the `reference` field would have been
    dead on the only subcommand shipping today. Pairing is `Task.run_id == EvalTask.id` (verified:
    `collect_tasks` reads the trace ENVELOPE's `run_id`, not the filename stem — which is what lets
    `run` give a file a timestamped NAME while keeping the pairing). A run the taskset does not
    describe is scored with an EMPTY reference, never skipped; a taskset FILE that cannot be parsed
    is still a hard failure, because that is a typo to fix rather than a condition to degrade past.
  - **Three sibling behaviours `run` deliberately does not copy**, each a real defect there: no
    `os.remove` of a stale trace (forbidden in this project — the FILENAME carries a UTC stamp and is
    unique per invocation, with no `--force` that deletes); everything under `--out` (cve-reverser
    builds CWD-relative paths despite its docstring); and a failing task becoming an `unscored` ROW
    with its reason rather than a `SystemExit` that kills the batch on task 1 of 50. `SystemExit` is
    caught explicitly alongside `Exception` because it derives from `BaseException`;
    `KeyboardInterrupt` still is not.
  - **`eval/tests/test_boundary.py` gained an AST assertion that no eval module imports
    `ctx_distillery.apply`.** `tests/test_no_write_capability.py` scans `ctx_distillery/` ONLY, which
    was a complete guard until `cli._drive` started importing product code — an eval `run` is
    automation over a batch of projects, exactly the shape invariant 8 forbids a writer in. AST, not
    a textual scan: that file's own docstring names `apply_plan`. `eval/tests/conftest.py`'s autouse
    offline fixture also now scrubs `CD_*` alongside `CDEVAL_*`, so a developer machine with
    `CD_ROOT_LM` exported cannot start a live billed distillation from the suite.
- **FIXED: three different drafting failures were labelled as one, and the label named the wrong
  half — the user-visible surface said a 502 was the model's fault.** `rlm_harness.tools.model.make_model_tool`
  reports `ok=False` for THREE distinct causes (its `ModelToolResult` field comments say so three
  lines apart): the deterministic host-side validator declined the text, the model ENDPOINT failed
  after its transient retries (`endpoint_error`, `raw=""` — the validator never ran), or the CIRCUIT
  BREAKER short-circuited (`circuit_broken`, `raw=""` — the model was never called). `drafting.py`
  has always recorded both flags on the `tool_call` event, so the cause was always recoverable; three
  surfaces threw it away anyway. Reproduced before fixing: a pure connection failure rendered as
  ``artifact 'a1' failed its format check: Connection refused``, in the exact text `studio/`'s PLAN
  panel puts in front of a human deciding what to apply.

  `schema._not_ok_problem` (new) now names the real cause in that problem line, using the vocabulary
  `rubric.py`'s TA criterion already established for the breaker. `rl_export.run_metrics` splits its
  old `draft_rejects` into `draft_validator_rejects` / `draft_endpoint_errors` /
  `draft_circuit_breaks` — disjoint (`_draft_cause` classifies in a CHAIN, not as three independent
  predicates) and summing exactly to a new `draft_not_ok` aggregate. That is training signal, and
  folding a 502 into "rejects" would have taught a trainer to read flaky infrastructure as model
  dishonesty. `AssembledCandidate.draft_ok` and `run_labels`'s `n_draft_not_ok` stay deliberately
  cause-blind aggregates — they answer "did this call yield usable bytes", the same answer either
  way — but their docstrings no longer claim to be validator verdicts. Recorded as `CLAUDE.md`
  invariant 12.

  **Also fixed, in the same neighbourhood: an inline comment that its own green test disproved.**
  `run_metrics` claimed the circuit-break count was "a subset of the first's *cause*, not of its
  count"; `test_run_metrics_counts_rejects_and_breaks_separately` right beside it asserted
  `draft_rejects == 2` and `draft_circuit_breaks == 1` over two calls — the break WAS inside the
  total. The new partition makes the relation statable, and
  `test_run_metrics_causes_partition_the_aggregate` pins it (including a hand-written payload
  setting both flags, which `make_model_tool` never does, precisely because the chain keeps the
  identity true anyway).

- **FIXED: `_slug_id` had no length cap, and a path-traversal assertion that could not fail.** Two
  studio findings from the same review.

  **The cap.** `ctx_distillery_studio.app._slug_id`'s docstring said "Copied verbatim from
  `diff_sentry_studio.app._slug_id`" — TRUE, and that was the bug: diff-sentry has no cap either, so
  we inherited the gap by copying the older sibling. `toolscout_studio.app._slug_id` has
  `_RUN_ID_MAX`, plus a re-strip so a truncation landing on a `-`/`.` leaves no trailing separator.
  Adopted, with the provenance line corrected to say so. A slug becomes ONE filename component (255
  bytes on most filesystems); reproduced before fixing, `GET /v1/runs/<5000 x's>` raised a raw
  `OSError: [Errno 63] File name too long` out of `_load_trace`'s `path.exists()` (`Path.exists()`
  does NOT swallow ENAMETOOLONG) — a 500 where a 404 belongs, and the last hole in this module's
  "never raise on a bad run_id" contract. `eval/cli.py::_slug` had the identical gap on the WRITE
  side (a task id becomes a trace FILENAME there, handed to `TraceRecorder`) and gets the same cap,
  `_TASK_ID_MAX`. Tests pin the cap, both truncation-on-a-separator edges (`-` and `.`), and
  idempotence — every read path re-slugs.

  **The assertion.** `studio/tests/test_app.py`'s traversal test ended with
  `assert client.get("/v1/runs/..%2F..%2Fetc%2Fpasswd").status_code == 404`. Instrumented: that
  request **never reaches `_slug_id`** — Starlette normalises the path before routing, so the 404
  comes from the ROUTER and would still be a 404 with `_slug_id` deleted entirely. Same for
  `..%252f..%252fetc`; `%2e%2e`, `a%00b` and a 250-char id DO reach it. The three direct
  `_slug_id(...)` assertions above it were the real coverage and are kept; the decorative request is
  replaced by ones that survive routing, spied to PROVE they arrive, and a comment records why so
  nobody "restores" the traversal-looking one thinking it is stronger.

- **FIXED: the invariant-1/8 tripwire was structurally blind over most of two modules.** An
  adversarial review of this batch found `tests/test_no_write_capability.py::_code_lines` entered
  docstring-skip mode only when a stripped line STARTED with `"""`. Two modules assign a
  triple-quoted string to a name at module level — `task._INSTRUCTIONS`, `cli._CLI_DESCRIPTION` —
  so the opener was missed, the bare closing `"""` was read as an OPENER, and the parity stayed
  INVERTED for the rest of the file: real code was silently skipped as "docstring body". Measured
  against `tokenize` as ground truth, **81% of `cli.py` and 36% of `task.py` went unscanned**, and
  the new `_cmd_export` landed inside the blind region — the one function whose stated safety
  argument is `cli.py`'s own claim to be inside the scan. Proven, not inferred: planting
  `open(..., "w")`, `.write()`, `shutil.rmtree`, `subprocess.run(["rm","-rf","/"])` **and**
  `from .apply import apply_plan` into `_cmd_export` left the suite fully green at 31 passed; after
  the fix the same plant fails both `test_no_module_contains_a_write_or_delete_call[cli.py]` and
  `test_apply_is_unreachable_from_the_planner_path`. A scan that reads nothing reports no offences
  and is indistinguishable from the outside from one that reads everything. `_code_lines` is now
  `tokenize`-based (a line counts as code when it carries a token that is not a string, comment, or
  layout), which also keeps the two properties the old version juggled by hand: docstring prose that
  NAMES a forbidden call stays excluded, and a trailing comment on a real statement stays scanned.
  `test_the_scan_still_sees_code_after_a_module_level_triple_quoted_assignment` pins it — all three
  of its assertions fail against the old algorithm. **No violation was ever present**: a
  tokenize-based ground-truth sweep of all 20 non-writer modules found zero write calls and zero
  writer imports. The defect was in the guard, not the code.
- **Review fixes across this batch.** `ctx-distillery export` raised a raw `KeyError` on a
  dict-shaped trace line missing `type`/`payload` — `export_dataset` sat outside the `try`, and
  `dict_events` guarantees dict-NESS, not key presence; it is now guarded and reports cleanly at
  rc=1. The studio's `applyBlocker()` framed a bad `promote_to_skill` scope and a target-less
  `prune` as affirmatively "backed" though `apply_plan` refuses both, so both are now blocked, and
  `DESIGN.md` no longer claims red EQUALS the refusal set: `apply.py` refuses at 29 sites, five are
  trace-decidable, and the rest depend on the apply-time re-scan a studio cannot see — "red means
  refused; absence of red does not mean accepted." The Load placeholder never cleared `hidden`, so
  every load after the first showed a blank stage instead of "Loading…". Plus doc-truth
  corrections: both workspace members DO expose `__version__` (CLAUDE.md said neither did),
  `from_env` raises on three conditions not two, `make_chat_fn` was a sixth site still carrying the
  retired live-drive justification, `uv sync --extra judge` errors (the extra belongs to the eval
  member), `reward: null` is on ACTION records only (SFT turns have no reward key), two "live feed"
  prose sites survived the rename, and `--bad`/`--radius`/failure-path counts were wrong.
- **The studio's live-drive refusal, rewritten on reasons that are actually true — plus
  `studio/DESIGN.md` and a node static-contract suite.**
  **The old justification had gone false and was repeated in five places.** It said
  `run_distillation` needs "a caller-supplied `HarnessAdapter` + `chat_fn` already wired, a
  materially heavier precondition than a self-contained one-shot driver a web request could
  reasonably own end-to-end". `cli.py::_cmd_distill` IS that driver — it assembles the whole
  precondition from the `CD_*` env. (Not "five lines", as an earlier draft of the reasoning claimed:
  an audit measured ~55 lines with four distinct failure paths. The point survives the correction;
  the rhetoric did not.) The endpoint is STILL declined, now on three reasons that survive the CLI:
  (1) **no cancel seam** — a distillation is a multi-minute, up-to-30-turn sandboxed episode, and
  neither `run_distillation` nor anything in rlm-harness takes a `cancel_event`, so an HTTP-started run
  could only be hung or SIGKILLed into exactly the truncated trace the studio papers over with its
  synthesized terminal event; `diff-sentry`/`toolscout` ship live endpoints without cancel because
  their operations are SHORT, and `cve-reverser` — whose profile matches ours — is the one sibling
  that needed it, at the cost of a threaded `cancel_event`, a cancel route, a dedicated
  `shutdown.py` + tests, and SIGINT/SIGTERM wrapping to defeat a real uvicorn deadlock. The fix
  belongs upstream in rlm-harness. (2) **the import-level `live`-extra valve is unavailable** — every
  sibling gates its live path behind `live = ["<parent>"]` so a replay-only deploy physically cannot
  spend; ours makes `ctx-distillery` a CORE dependency because replay itself calls `assemble`.
  **Stated as contingent, not structural**, per an audit: `live = ["openai"]` would not restore it
  either (the planner spends through dspy/litellm, a core rlm-harness dep, long before any drafting
  call), and neither does the `schema.py` split, because `assemble` ships in the same distribution
  as the driver — splitting a package is the only route and is out of scope. The same audit killed
  the phrase "armed the moment `CD_ROOT_LM` is in the environment": route existence and credential
  presence are different things, and no sibling gates on env. (3) **the live input would be
  `project_dir`** — an unauthenticated HTTP parameter selecting whose ENTIRE Claude Code
  conversation history is rendered and shipped to a remote model, with no `_slug_id` analogue, while
  invariants 5/6's defenses all assume the caller chose the project; redaction is a filter, not an
  authorization decision. This one is the strongest. The positive case is stated too:
  `ctx-distillery distill` writes into `$CTXD_TRACES_DIR`, the SAME directory the studio globs, so
  `distill` → refresh → **Load** already delivers what a live endpoint would, from a process that
  owns its credentials and can be Ctrl-C'd. And the refusal is made FALSIFIABLE, with named
  reopening conditions: a cancel seam in rlm-harness; an opt-in gate that makes the route not exist by
  default; an allowlist of drivable project dirs sourced from the ENVIRONMENT, never the request
  body; a stated loopback-bind/auth posture.
  **Five sites, not four.** `CLAUDE.md` invariant 10, `studio/README.md` §Scope (the canonical long
  form), `studio/app.py`'s module docstring, and — the one the plan missed, found by audit —
  `studio/ctx_distillery_studio/__init__.py`, which carried the stale wording verbatim. The fifth,
  `config.py`'s docstring, was NOT stale (it says the studio "replays a finished trace and drives
  nothing", still true) and was only pointed at the real reasoning rather than rewritten.
  **`CHANGELOG.md`'s own earlier entries still carry the old wording, deliberately** — they are
  HISTORICAL records of what was decided at the time, and a changelog that edits its own past is
  worth less than one that doesn't. Do not "fix" them.
  **New `studio/DESIGN.md`** — a VISUAL & UX spec on toolscout's skeleton (theme · signature ·
  palette · typography · components · depth/motion · do-don't · exactly eight browser-checkable
  acceptance items), explicitly NOT an architecture doc: `studio/README.md` keeps the endpoints, the
  SSE vocabulary, scope and install/run, and the two files now cross-link. This is the one
  design-shaped file that IS a family convention, and it is a different species from the
  project-level blueprint this repo purged. Its §2 is ctx-distillery's own signature rather than a
  copied verdict axis: **the plan's `artifact_id` CLAIM ≠ the drafted BYTES** (invariant 2), with a
  three-state frame keyed only on what `assemble()` derived — `blocked` / `backed` / `inert`. Six
  things were deliberately NOT copied, each verified false here: model-role chips (`/v1/config`
  returns `{traces_dir}` only), a vendored font (`static/vendor/` does not exist), a primary POST
  button / run-id preview / `run-core.js`, a Trajectory drawer + `/iterations` route (named as
  DEFERRED, with where the fix belongs — never described as built), a "streams as it happens" claim,
  and `live`/`subscription` studio extras.
  **Writing the spec forced two real frontend fixes, because a spec that describes a UI that does
  not exist is worse than no spec.** (a) The feed panel was labelled `▾ Live feed` with a
  `streaming…` status while being replay-only; renamed to **Replay feed** / `replaying…`, and the
  real ordering caveat is now carried in both docs — `main_step` flushes POST-HOC with trailing
  `step_id`s, so a replay streams the run's ACTIONS BEFORE the reasoning turns that produced them.
  (b) §2's "flagged, never silently dropped" was only two-thirds true: `app.js` flagged a candidate
  with `problems`, but `apply.py::_blocking_problem` refuses on THREE conditions, and the third — a
  promotion whose assembled `draft` is empty — carries no `problems` and can even report
  `draft_ok === true`, so the candidate a reviewer most needs to see rendered as an ordinary row
  with a missing `<pre>` and no marker. `app.js` gained `applyBlocker()` mirroring that function
  exactly, `candidateState()` deriving the three frame states, a `⚠` refusal marker line, and
  `style.css` the `.candidate-row.state-{blocked,backed,inert}` frames. Two more genuine bugs fell
  out of writing §6: the draft `<pre>` had `white-space:pre-wrap` only, which wraps at WHITESPACE —
  one attacker-length unbroken token in untrusted model output gave the whole PAGE a horizontal
  scrollbar (fixed with `overflow-wrap:anywhere`); and the three fixed grid tracks (~1000px total)
  had no breakpoint at all, so "no overflow at 375px" was simply false (fixed with a `max-width:
  1040px` stack that also RELEASES the `calc(100vh - 56px)` pin, since one stacked column of
  fixed-height scroll tracks crushes every panel to a few rows).
  **New `studio/tests/static-contract.test.js`** — plain CommonJS, `require("assert")`, a ~5-line
  hand-rolled harness exiting non-zero, run as `node <file>`. No npm, no `package.json`, no
  `node_modules`, matching all three siblings. **Adapted, not ported:** an audit checked toolscout's
  five assertions and only THREE have an analogue here — the `[hidden]` guard, the `.layout`
  viewport-height pin, and `word-break` on model-supplied fields (`.fr-fields`,
  `.candidate-key-fields`, `.rubric-fact-value`). The other two assert selectors this studio does
  not have (there is no inline SVG in `index.html` at all, and no `.meta-col`/`.prose`/`.tchip`), so
  they were DROPPED rather than faked. Four assertions are ours with no sibling precedent: the draft
  `<pre>`'s `overflow-wrap` + height cap, the §2 derived-state frame classes and their `--bad`
  refusal color, the responsive stack releasing the height pin, and a no-markup scan of `app.js`
  itself — matched on CODE SHAPE (`.innerHTML =`, `insertAdjacentHTML(`, …) rather than the bare
  identifier, because `app.js` NAMES `innerHTML` twice while promising never to use it and a
  substring scan would flag the documentation OF the rule as a violation of it (the same failure
  mode `studio/tests/test_boundary.py` already solved by moving to `ast`).
  **CI: its own `studio-static` job, not a step in `studio-test`.** Uses toolscout's
  `shopt -s nullglob` form, not the other two's unguarded loop (with no `*.test.js`, `for f in
  studio/tests/*.test.js` runs `node 'studio/tests/*.test.js'` literally and fails
  module-not-found). The divergence from all three siblings — which append it as a step to their
  single studio job — is deliberate and flagged by audit: `studio-test` here is a 3-version Python
  matrix, and these assertions read files as TEXT with no Python involved, so a step there would run
  them three times identically for no signal. A separate job also skips `uv sync` entirely and names
  a CSS regression precisely instead of burying it inside "studio (py3.12)".
- **The eval member's LIVE judge (`CDEVAL_*`) — and the three shape changes that had to land first.**
  `eval/pyproject.toml` had declared `judge = ["openai>=1.0"]` since this package existed and NOTHING
  imported it: a dead extra, and an eval harness that could never be pointed at a real model is not a
  peer of the three siblings. `judge.make_eval_judge(config, chat_fn=...)` now builds it on
  `rlm_harness.tools.make_model_tool` — the same chat → transient-retry → validate → circuit-breaker core
  the rollout side's drafting tools use — with `from openai import OpenAI` LAZY inside the chat
  closure, `max_retries=0` (the retry loop has ONE owner; leaving the client's own retries on would
  multiply the two and turn a hard 60s timeout into minutes), `temperature=0.0`, and a strict
  `parse_eval_json` that strips a ``` fence, slices `{`…`}`, requires all four ATLAS categories as
  real numbers — **rejecting `bool` FIRST, because it subclasses `int` and `{"TF": true}` would
  otherwise clamp to a real-looking 1.0** — clamps to [0, 10], tolerates extra fields, and truncates
  notes at 2000 chars. `EvalJudgeConfig.from_env()` reads `CDEVAL_MODEL`/`_BASE_URL`/`_API_KEY`/
  `_TIMEOUT`, a SEPARATE surface from the root's `CD_*` because the judge must be pointable at a
  different model than the run it scores. `PROMPT_VERSION = "atlas-ctxd-eval-v1"`.
  **The client was the small half.** Three shape changes were preconditions, not follow-ups:
  1. **`Judge` returns a `JudgeVerdict(ok, score, reason)`**, not a bare `EvalScore`. A live judge has
     three distinct ways to produce no number — circuit-broken, endpoint error, off-schema — and a
     function returning `EvalScore` can express none of them without inventing one. The reasons are
     `"judge circuit breaker: too many unusable replies in a row"`, `"judge endpoint error: <exc>"`,
     and `"judge output off-schema: <validator errors>"`, kept distinct because their fixes differ.
  2. **`EvalRow.score` is now OPTIONAL, beside a `unscored_reason` that is REQUIRED whenever it is
     `None`** (a model validator refuses the blank-mystery row). It used to be required, so a failed
     judge had literally nowhere to land. `compute_means` drops unscored rows from the sum AND the
     DENOMINATOR — counting them there is arithmetically identical to scoring them 0, the exact lie
     "unscored, never a fake 0" exists to prevent. **Divergence from all three siblings, argued:**
     they store BOTH an `unscored: bool` and an optional `score`, two representations of one fact that
     can disagree; here `score is None` IS unscored, exposed as a derived `@property`.
  3. **`EvalReport` gained `n` / `n_unscored` / `judge_model` / `prompt_version`.** Without
     `prompt_version` a number is not attributable to the prompt that produced it, which is the whole
     point of the constant. No `taskset` field, unlike every sibling: there is no taskset concept here
     to name (see the deferral below), and the field would be unfillable.
  Also: `cli._pick_judge(force_stub)` (live iff `CDEVAL_MODEL` and not `--stub`; built ONCE per batch
  because the breaker lives in the closure), a `--stub` flag, an unscored row rendering as `--` +
  its reason with an `n=… (… unscored)  judge=…  prompt=…` footer, the siblings' `return 1` when
  nothing scored, `ctx_distillery_eval/__main__.py`, and `eval/tests/conftest.py` — whose autouse
  fixture scrubs `CDEVAL_*` so a developer with a live judge exported cannot have the CLI tests
  quietly start billing. **Two deliberate divergences beyond the `unscored` one**: the input contract
  stays two positional arguments (`judge(plan_text, transcript_texts)`) rather than the siblings'
  `inputs: dict` from a `build_judge_inputs` — theirs reconstructs FIVE slots from a trace, one of
  them judge-only ground truth from a taskset, while ours has exactly two already-typed values
  carried end-to-end from the CLI, and a dict would invite a `reference` key with no producer; and
  `StubJudge` stays a CLASS rather than becoming a module-level function, because it is
  parameterizable and `test_score.py`'s means test genuinely needs two rows with different scores.
  `eval/tests/test_judge.py` (19 tests, mirroring toolscout's — the deepest of the three) drives every
  path through an INJECTED `chat_fn`: no network, no monkeypatching of `openai`, and the one thing a
  callable cannot check (that `openai` is not imported at module scope) asserted structurally against
  the module's own AST. eval suite 41 → 84.
- **DEFERRED, with the three real blockers recorded** (`eval/README.md` + a CLAUDE.md
  known-simplification bullet): the eval `run` subcommand and a real taskset. `taskset.py` is NOT a
  taskset — `collect_tasks(glob)` enumerates `{run_id, trace_path}` from TRACES, where every sibling's
  `run` iterates a real `EvalTask` with an id, a planner-visible input and a judge-only `reference`;
  `judge.build_prompt` has no `{reference}` slot at all, so adding judge-only ground truth is a PROMPT
  change and prompt changes are what `PROMPT_VERSION` exists to make attributable; and
  `run_distillation` returns an `AssembledPlan`, not artifacts, and never returns the REDACTED
  transcript text it ingested — so an eval `run` would have to re-`ingest()`/re-`redact()` and could
  then score against a DIFFERENT redaction than the run actually saw (the clean fix is a returned
  artifacts object, a driver signature change belonging with the taskset design). None of it blocked
  the live judge: this project's judge takes `transcript_texts` as its ground-truth analogue and needs
  no `reference`, so it is exercisable end-to-end on `score` alone.
- **The `subscription` extra — run the PLANNER on a Claude Pro/Max account, with a hard refusal for
  the DRAFTER.** Five pieces mirroring the siblings: `subscription = ["claude-agent-sdk>=0.1.60"]`
  under `[project.optional-dependencies]`, a `subscription-sdk` dev-group MIRROR of it, a new
  `[tool.uv] default-groups = ["dev", "subscription-sdk"]` (an EXTRA is not synced by default, so a
  bare `uv sync` would prune the SDK out of the shared workspace venv and a subscription dev run
  would then crash; `dev` must stay listed because `default-groups` REPLACES uv's default rather
  than appending), the ~17-line `.env.example` block, and `config.SUBSCRIPTION_PREFIX` +
  `config._maybe_subscription_lm` wired as `main_lm=` / `sub_lm=` into the existing
  `rlm_harness.configure(...)` call. `ModuleNotFoundError` re-raise with the actionable
  `uv sync --extra subscription` hint follows diff-sentry/toolscout (cve-reverser omits it — the
  minority). **Divergence, in our favour:** all three siblings put the router in their dspy-BEARING
  task module because their `config.py` must stay import-clean; ours already imports `rlm_harness`
  inside `setup()`'s body, so it lives in `config.py` with `from rlm_harness import ClaudeAgentLM` inside
  the SENTINEL BRANCH — the module top stays dspy-free, asserted in a fresh interpreter by both
  `tests/test_public_api.py` and the new `tests/test_subscription.py` (13 tests).
  **The load-bearing piece is an UNCONDITIONAL `CD_DRAFT_LM` sentinel refusal in
  `DistillConfig.from_env`.** Every sibling has exactly one role that is a separate
  OpenAI-compatible client and may never carry the sentinel (cve-reverser's generator, diff-sentry's
  classifier, toolscout's judge); ours is the DRAFTER, since `make_chat_fn` builds an `openai.OpenAI`
  client directly. Two things make it mandatory rather than defensive, and they compound: BOTH
  drafting tools are ALWAYS wired in `DistillSession.__init__` (diff-sentry's exact "always
  registered, so a sentinel here would fail LATE" condition — mid-trajectory, on the one hard-budget
  attempt), and `draft_model` falls back to `sub_model` falls back to `main_model`, so a user who
  sets ONLY `CD_ROOT_LM=claude-agent-sdk/…` silently ships the sentinel to the drafting endpoint as
  a bogus model id. The error distinguishes *explicitly set* from *inherited* and names WHICH
  variable it was inherited from. `studio/app.py` needed no mirrored prefix — its `/v1/config`
  reports no model at all.
- **`ctx_distillery/rl_export.py` + `ctx-distillery export` — the reward-free SFT/RL dataset bundle,
  narrowed to fit this project's invariants.** Built on `rlm_harness.dataset`'s `export_actions` /
  `export_sft_turns` / `run_label_bundle`, emitting `{actions, drafting, orchestrator_tools, planner,
  sft_turns, labels, metrics, rubric_signal}` with `reward: null` on every record. Role split:
  `drafting` = the two `make_model_tool` tools (the analogue of the siblings' generator/classifier),
  `orchestrator_tools` = the three read-only lookups. Legacy-rubric backfill follows
  cve-reverser/diff-sentry (`rubric_from_meta(events).criteria or default_rubric().criteria`), NOT
  toolscout's bare form, which reports an empty rubric beside a full set of facts on an old trace.
  Four deliberate divergences, each recorded in the module docstring:
  1. **No writing `main()` and no `--out`.** The siblings' `rl_export.main()` and `cli._cmd_export`
     both do `open(out, "w")`, which would turn `tests/test_no_write_capability.py` red — and
     `CLAUDE.md` says a red tripwire IS the finding. So it is `ctx-distillery export <trace-glob>...`
     printing JSON to stdout, redirected with `>`, mirroring `show`. The form is
     `print(json.dumps(...))` and NOT `json.dump(..., sys.stdout)`: both pass the scan TEXTUALLY, but
     the second calls `.write` at runtime while only looking clean, which reads as evading the
     tripwire. An empty glob match REFUSES (exit 1) rather than printing a zero-run bundle, and the
     one-line summary goes to stderr so a redirect yields valid JSON and nothing else.
  2. **`run_labels` IS built, and the earlier reasoning against it was wrong.** An audit found the
     original refusal cited toolscout's model-decided `met` booleans — which live in `rubric_signal`,
     the surface KEPT here, not in `run_labels` at all. diff-sentry's `run_labels` and toolscout's
     are purely structural and map one-for-one onto `AssembledCandidate`'s real fields
     (`unbacked_*` ≈ `problems`, `finalized` ≈ `plan is not None`); only cve-reverser's
     `valid`/`complete` is oracle-flavoured, and its domain has ground truth. So the shipped shape is
     `{finalized, n_candidates, n_keep, n_prune, n_promote_memory, n_promote_skill, n_unbacked,
     n_draft_not_ok, plan_problems}` — every field recomputable from the same JSONL, zero oracle,
     zero fabrication, and a test asserts no `valid`/`complete`/`correct`/`score`/`reward`/`met` key
     ever appears.
  3. **Reads through `trace_io.load_trace` / `dict_events`**, never `rlm_harness.trace.load_events`
     (invariant 11's non-dict-line guard applies to a new reader exactly as to the old ones).
  4. **The `drafting` split is NOT filtered on `outcome.output`**, unlike cve-reverser's and
     diff-sentry's generator/classifier splits. `tools/drafting.py` records the authored bytes under
     `draft=`, and rlm-harness's `_action_record` reads only `raw`/`result`/`results`/`preview` — so
     `outcome.output` is `None` for EVERY ctx-distillery tool call and that filter would silently
     produce an empty split. Pinned by a test, with the reason, so the fix for an empty-looking split
     is not to re-add it. Re-source a draft's text the way invariant 2 already requires: from the
     `tool_call` event keyed by `artifact_id`, via `schema.assemble`.
  New `tests/test_rl_export.py` (31 tests). `ctx_distillery/README.md`'s layout and CLI sections
  updated (they had also never listed `schema.py`). Together with `SUBSCRIPTION_PREFIX` above, these
  eight names (`load_runs`, `export_dataset`, `run_labels`, `run_metrics`, `rubric_signal`,
  `DRAFTING_TOOLS`, `ORCHESTRATOR_TOOLS`) take the eager public surface from 44 to **52** — `load_runs`
  is exported where the siblings keep theirs private, because without a writing `main()` it is the
  entry a library caller needs to reach `export_dataset`.
- **`VENDOR.md`'s export paragraph rewritten.** It declined an exporter on the grounds that "there's
  no obvious reward signal for *was this the right thing to prune*" — an argument that defeats an
  ORACLE labels surface and nothing else, and that mis-described what the siblings actually built
  (mostly `sft_turns` + `actions`, all `reward: null`, with `run_label_bundle` structurally refusing
  the name `reward`). It now describes what exists, what is deliberately absent, and why. A
  `ClaudeAgentLM` bullet was added alongside, naming the drafter exclusion.
- **A real public surface on `ctx_distillery/__init__.py` — 44 eager names + 3 lazy ones, and the
  writer excluded on purpose.** The package was 8 lines (a docstring and `__version__`); it now
  follows the sibling projects' convention exactly: eager `from .x import ...` for every dspy-free
  module, `__all__` grouped by seam with a literal `# dspy-bearing (lazy):` block, `__version__`
  between `__all__` and a PEP 562 `__getattr__` that defers the dspy import to first use. The
  partition was MEASURED per module rather than inherited from a pre-`schema.py` design note — after
  the shapes split, only `task`, `session` and `cli` still pull dspy, so the lazy tail is just
  `DistillSession` / `run_distillation` / `main` and everything else (schema, config, render,
  trace_io, redact, rubric, `adapters.*`, `tools.*`) is eager.
  **`apply_plan`, `ApplyOutcome`, `slugify` and `ARCHIVE_DIRNAME` are deliberately absent**, and the
  module docstring says why and points at `from ctx_distillery.apply import apply_plan` / the
  `ctx-distillery-apply` console script: invariant 8 requires that no module on the RLM path can
  reach the human-gated writer, `__init__.py` is on that path (its imports run eagerly), and
  `test_no_write_capability.py`'s `IMPORTS_APPLY` regex matches every re-export form including an
  indented `from . import apply` inside `__getattr__`. That is a principled divergence from all
  three siblings, each of which re-exports its writer-adjacent helpers freely.
  New `tests/test_public_api.py` (6 tests) **supersedes and replaces `tests/test_import.py`** — two
  files both claiming to test importability was the drift being removed. It mirrors toolscout's four
  (fresh-subprocess dspy-free import, every `__all__` name resolves, `__version__` matches
  `pyproject.toml`, lazy names deferred) plus two of this project's own. **The originally-designed
  form of the writer test was wrong and is recorded as such in the test's docstring**:
  `assert not hasattr(ctx_distillery, "apply")` is red on day one, because `tests/test_apply.py` and
  `tests/test_apply_cli.py` import the submodule at MODULE level and importing a submodule binds it
  as an attribute of its parent package during pytest's COLLECTION phase, before any test runs. The
  shipped form asserts the names are absent from `__all__` (always valid) plus `hasattr` is False for
  the four that genuinely fall through `__getattr__`, and makes the module-attribute claim honestly
  in a fresh subprocess that never imports the writer.
  `__all__` carries the package's one `# noqa: RUF022`: the grouping is the dspy manifest, and an
  isort-style sort would scatter the lazy names through the eager ones. Every other module's `__all__`
  stays sorted.
- **`## Versioning` in `CLAUDE.md`** (all three siblings had one; this repo did not) — keep
  `pyproject.toml` `[project].version` and `ctx_distillery.__version__` in sync, fold a bump's changes
  into this file, plus two facts specific to this repo: the two workspace members carry their OWN
  `version` (both `0.1.0`) and NOTHING checks them; and the changelog rule that a new version is a
  RENAME of `[Unreleased]` plus a fresh empty one ABOVE it, never a section added under a shipped
  one. (0.1.0 is NOT cut — a heading was added once and reverted; a version number is a claim
  about the software, and this project has not reached a working first version.)
- **`pytest-asyncio` / `asyncio_mode = "auto"` DECLINED, recorded as a decision** (`CLAUDE.md`
  `## Verify`). All three siblings carry them; all three of this repo's suites have ZERO async tests,
  and the four async call sites are driven from synchronous tests through explicit `asyncio.run(...)`,
  so adopting it would add a dev dependency with no consumer.
- **`VENDOR.md` stub rot fixed.** Its `configure`/`RLMConfig` bullet still described `task.py` as an
  unwired stub — "not yet imported", the pyodide pin "not yet reflected in code" — and its
  `make_model_tool` bullet still pointed at a `task.py` TODO enumeration that no longer exists. All
  three claims were false: `config.setup()` calls `rlm_harness.configure(RLMConfig(...))`,
  `task._forced_config` enforces the pin via `dataclasses.replace`, and `DistillConfig.from_env`
  additionally refuses a non-`pyodide` `CD_INTERPRETER` loudly. (The `rl_export` paragraph is
  untouched — a later pass owns it.)
- **`ctx_distillery/schema.py` — the dspy-free shapes module every sibling already had.** A pure
  refactor plus two tests; no behaviour change. `DistillAction`/`DistillCandidate`/`DistillPlan`
  moved out of `task.py`, and `AssembledCandidate`/`AssembledPlan`/`PROMOTION_ACTIONS`/`assemble`
  out of `session.py`. **The gap this closes was measured, not assumed**: importing either workspace
  member's entry module put `dspy` in `sys.modules` (`eval` cli -> True, `studio` app -> True), while
  the same check on `diff_sentry_eval.cli` and `toolscout_eval.cli` returned False. Root cause was
  purely structural — the only route to `assemble` ran through `session.py`, which imports `task.py`,
  which does `from rlm_harness import RLMTask` — so a fully-offline `ctx-distillery-eval score --stub`
  run, and EVERY studio HTTP request, imported an LM framework neither one calls. `assemble` was
  verified to qualify rather than assumed to: its whole dependency set is `EVENT_TOOL_CALL` (a string
  constant), `trace_io.dict_events` and the dataclasses, i.e. a pure function over `(events, plan)`.
  **`task.py` and `session.py` re-export every moved name** (`__all__`), so `from
  ctx_distillery.task import DistillPlan` / `from ctx_distillery.session import assemble` resolve
  unchanged and to the IDENTICAL object — this is a move, not an API break. `run_distillation` stays
  in `session.py` (it constructs a `DistillSession`) and so does `render_memory_index` (prompt-side
  presentation for one task, not part of the plan's shape); `DistillSession` + the `pyodide` pin stay
  in `task.py`, where invariant 1 requires the pin to be stated. `render.py`, `apply.py`, `cli.py`,
  `rubric.py`, `eval/score.py` and `studio/app.py` now import the shapes from `schema`; `rubric.py`'s
  two function-local imports (`from .task import DistillPlan`, `from .session import assemble`) became
  module-level, since "keep the top light" was only ever true while those names lived beside an
  `RLMTask` and `schema.py` imports nothing from `rubric.py`, so there is no cycle to dodge. Fallout
  worth naming: `ctx_distillery.apply` — and so the `ctx-distillery-apply` console script — is now
  dspy-free too.
- **The two missing boundary gates, in a FRESH SUBPROCESS, one per member.** `eval/tests/` and
  `studio/tests/` each gained `import <root> must not pull the member` and `importing the member
  pulls the root one way AND stays light (no dspy, no openai)`, modelled on
  `diff-sentry/eval/tests/test_boundary.py`'s first two tests — the ones ctx-distillery could not have
  written before this refactor. They import each member's ENTRY module (`.cli` / `.app`) rather than
  the bare package, a deliberate divergence: diff-sentry's `__init__.py` eagerly re-exports its whole
  surface so a bare import walks the real graph, whereas both of ours are still a docstring plus
  `__version__`, which would make the assertions vacuous. The entry module is the stronger check
  anyway — it covers the whole real import graph, not a curated re-export list. `eval/`'s existing
  TEXTUAL scan is kept as-is (diff-sentry keeps its own third test textual; it is belt and braces,
  not the gate), which is why `schema.py`'s docstring names the two members by DIRECTORY and never by
  import name — the scan cannot tell prose from an import.

- **The CLI, as TWO console scripts — because one binary is provably impossible here.**
  `ctx-distillery distill [project]` discovers a project's Claude Code storage, wires the `chat_fn`
  from `CD_*`, runs the distillation, and prints the assembled plan; `ctx-distillery show <trace>
  [--json]` re-reads a finished run offline. `ctx-distillery-apply <trace> --project <dir> --approve
  0,3 [--confirm]` is the writer. The split is not stylistic:
  `tests/test_no_write_capability.py::test_apply_is_unreachable_from_the_planner_path` scans every
  `.py` under `ctx_distillery/` except `apply.py` and matches a **function-local** import as readily
  as a top-level one, so a shared CLI module importing both `run_distillation` and `apply_plan`
  turns it red — and `apply.py` is explicitly "not a precedent for a second exemption". Verified
  against the real regex before choosing: `apply.py` is excluded from `SOURCES` entirely, so hosting
  `main()` *in the writer* keeps both properties, adds no exemption, and makes applying a visibly
  different command at the shell — the same thing the API says by refusing to offer an apply-all
  call. The three alternatives were rejected on the record: relaxing the regex weakens the guard
  that makes the exemption safe; a second exempt module is what invariant 8 forbids by name; and a
  fourth workspace member would move the importer *outside* `PACKAGE.rglob`'s field of view, where
  the tripwire cannot see it at all. `importlib`-ing the writer is evading a tripwire by spelling.
  **Approval UX**: `--approve` takes indices (repeatable and/or comma-separated, matching
  `apply_plan`'s `approved_ids`, which are indices because `keep`/`prune` candidates carry no
  `artifact_id`), the default is a DRY RUN that prints the whole plan and writes nothing, `--confirm`
  is a second deliberate act, and there is no `--all` — pinned by
  `test_no_flag_ever_approves_the_whole_plan`, which checks the parser's real option strings rather
  than a `--help` substring (a substring check flagged `--allow-skill-scope` for containing
  "--all"). `--allow-skill-scope` defaults to **project only**: `~/.claude/skills` reaches every
  project the operator will ever open and a global skill *shadows* a project one of the same name,
  so it earns its own opt-in even behind `--confirm`. A mistyped index is refused (exit 2) before
  anything is written, in the dry run as well as under `--confirm`.
  **Two behaviours fall out of invariant 1** rather than being chosen: `cli.py` is inside the
  mutation scan, so `show` has no `--out` (redirect with `>`) and `distill` cannot `os.remove` a
  stale trace the way the sibling projects' `run()` does — `TraceRecorder` appends, so the default
  `--run-id` is `<project>-<UTC timestamp>` and a run whose trace file already exists is REFUSED
  rather than interleaved. There is deliberately no `--force` that deletes one. The new
  `cli.py`/`config.py`/`render.py`/`__main__.py` are named explicitly in
  `test_the_scan_actually_sees_the_package`, so "the planner-side CLI cannot write" is pinned, not
  incidental.
  **`.env.example` is finally true.** It declared four `CD_*` variables that no code read — the
  library entry point takes a caller-supplied adapter and `chat_fn` and never touches the
  environment. New `ctx_distillery/config.py` (`DistillConfig.from_env` / `setup` / `make_chat_fn`)
  reads them, adds `CD_SUB_LM` / `CD_DRAFT_*` / the budget knobs, documents `CTXD_TRACES_DIR` (the
  variable the studio already read, now shared so a finished run appears in its Load picker with no
  second directory to keep in sync), and refuses loudly on the two conditions worth refusing on: no
  `CD_ROOT_LM`, and a `CD_INTERPRETER` that is not `pyodide` — the latter changes no security
  property (`task._forced_config` would coerce it anyway) and exists so a misconfiguration is SEEN.
  `distill` also says *"no transcripts found under `<storage dir>`"* instead of proposing an empty
  plan and looking broken. Answering `CLAUDE.md` invariant 10's open question explicitly: a CLI CAN
  own `run_distillation`'s preconditions end to end where a web request cannot — an operator's shell
  already holds the credentials and a foreground process is where a multi-minute sandboxed episode
  belongs — so the studio's abstention from a live-drive endpoint stands unchanged, for its own
  reasons. New `tests/test_cli.py` (31) and `tests/test_apply_cli.py` (29).
- **`render_plan` promoted to `ctx_distillery/render.py` — the third function under invariant 11,
  and the promotion immediately caught a real bug.** It was defined in `eval/`'s `score.py` (written
  for the judge prompt); `ctx-distillery show` needs the identical text, because a reviewer deciding
  what to approve should read exactly what the judge reads. Rather than copy it a second time — the
  mistake invariant 11 exists to prevent, already made twice for `plan_from_events` and
  `load_trace` — it moved, and `eval/score.py` imports it while keeping it in `__all__` (so
  `from ctx_distillery_eval.score import render_plan` still works), with the identity pinned by a
  test rather than the behaviour alone. The bug: the no-candidates branch `return`ed early and
  DROPPED the run-level problems line, so the single case that matters most — `assemble(events,
  None)`, "no plan was produced by this run", a run that died before SUBMIT — rendered to both a
  reviewer and the judge as a bare, actively misleading "proposed no candidates" with no reason
  attached. Fixed once, in the one place; the two sections are now independent, which changes
  nothing for a plan that has candidates or a plan that has neither.
- **Closed the non-dict trace-line gap the Studio pass explicitly deferred — the shared library
  functions are now hardened, with ONE implementation instead of three.** `rlm_harness.trace.load_events`
  does no shape validation, so a JSONL line that is valid JSON but not an object (`42`, `null`,
  `[1,2,3]`, `"x"`) reached every `.get(...)` consumer as-is. Reproduced end to end:
  `ctx-distillery-eval score '<glob>' <transcript>` over one clean trace plus one carrying a single
  `42` line scored **zero** runs — the clean one included — dying in `collect_tasks` before a run was
  ever reached. Three separate crash points on that path, in order: `taskset.collect_tasks`'s
  `e.get("run_id")`; `load_events` ITSELF, whose own `run_id` filter is an unguarded
  `event.get("run_id")` (so no amount of hardening inside `ctx_distillery` could have saved
  `cli.py`'s call — only the call site could); and `session.assemble`, whose "none of them raise"
  docstring was literally false, since `_draft_calls` scans every event before the candidate loop and
  so raised even for an all-`keep` plan. Two more the original report missed:
  `rubric.plan_from_events` was ORDER-DEPENDENT (`reversed()` returns at the first `result` event, so
  a bad line before it was never visited and the bug looked absent), and
  `rubric.rubric_from_meta`/`criteria_facts` raised too. New `ctx_distillery/trace_io.py`
  (`load_trace` / `dict_events`) is now the ONE place JSONL bytes become events; `rubric`, `session`,
  `eval/taskset`, `eval/cli` and `studio/app._load_trace` all read through it, the last replacing its
  own inline copy — a de-duplication, not a removal, with its non-dict regression test unchanged and
  still green. `load_trace` re-implements the `run_id` filter rather than delegating `run_id=`, which
  is load-bearing: delegating puts the crash upstream of the guard. Hardening `load_events` upstream
  in `rlm-harness` remains a reasonable follow-up **there**, not a prerequisite here. New
  `tests/test_trace_io.py`, `eval/tests/test_taskset.py` (that module had zero coverage, and it was
  where the batch's first crash lived) and a batch-survival regression in `eval/tests/test_cli.py`
  asserting BOTH runs still score, not merely that the command exits 0.
- **`studio/tests/test_boundary.py` (new)** — invariant 10 has always required that `studio/` never
  call `ctx_distillery.apply.apply_plan`, but nothing asserted it (`eval/` had its boundary test, the
  root package had its RLM-path reachability test; the one member reachable over HTTP had neither).
  A static `ast` scan over the studio package, plus the reverse direction (`ctx_distillery` never
  imports `ctx_distillery_studio`) mirroring `eval/tests/test_boundary.py`. `ast` rather than a
  textual scan for a concrete reason: `ctx_distillery_studio/__init__.py`'s docstring NAMES
  `apply_plan` in the very sentence promising never to call it, and a text scan would flag the
  statement of the invariant as a violation of it.
- **`studio/` — a new `ctx-distillery-studio` workspace member (Phase 2 of the rubric/eval/studio
  initiative)** (root `pyproject.toml`'s `[tool.uv.workspace] members` now `["eval", "studio"]`): a
  REPLAY-ONLY FastAPI + zero-build vanilla-JS console over a finished `DistillSession` run's
  trace/v1 JSONL file — the only artifact `run_distillation` ever produces (it writes no
  `responses/{run_id}.json` or similar; the in-memory `AssembledPlan` is inert by design). Five
  endpoints: `GET /` (frontend shell), `GET /v1/config` (`{"traces_dir": ...}` only — this project's
  model is an injected `chat_fn`, not an env-var-selected one, so there is no `CTXD_ROOT_LM` to
  report), `GET /v1/runs` (discovery by globbing `{TRACES_DIR}/*.jsonl`, env `CTXD_TRACES_DIR`),
  `GET /v1/runs/{run_id}` (`plan_from_events` -> `session.assemble` -> `rubric.trace_facts`,
  returned as `{"plan": {...}, "rubric_facts": {...}}`), and `GET /v1/runs/{run_id}/events` (SSE
  replay via a pure `mapper.to_event`, sorted by `step_id`, synthesizing a terminal
  `distill.run.completed` when a truncated trace never emitted one). `run_id` is sanitized
  (`_slug_id`, copied verbatim from the real, cloned `diff-sentry-studio` precedent) before it ever
  becomes a path component. No live-drive endpoint this pass — `run_distillation` needs a
  caller-supplied `HarnessAdapter` + `chat_fn` already wired, a materially heavier precondition than
  a self-contained one-shot driver a web request could reasonably own end-to-end. Never calls
  `apply.apply_plan`. `mapper.to_event` maps `main_step`/`sub_call` (the planner's own reasoning
  turns and any recursive sub-LM escalation) IN ADDITION to `run_start`/`tool_call`/`result`/
  `run_end`/`final` — an earlier draft of this table silently dropped the first two, a real gap
  against this initiative's own motivating goal, fixed before it shipped. The frontend (a Load box,
  a live feed panel, the PLAN panel — the money shot: each candidate's `draft` rendered via
  `el.textContent` **only**, never `innerHTML`, and a Rubric panel) is zero-build vanilla JS/CSS, no
  bundler, no `node_modules`. `.github/workflows/ci.yml` gains a matching `studio-test` job.
- **Promoted `rubric._plan_from_events` to public `plan_from_events`** (prerequisite refactor for
  the Studio pass above — the Studio needed the SAME plan-from-trace reconstruction a third time,
  and neither "reach across `eval/`'s own package boundary into an underscore-prefixed helper" nor
  "duplicate it a third time" was an acceptable choice). `eval/ctx_distillery_eval/score.py` now
  imports and calls it instead of keeping its own local copy — deleting a real duplicate (and the
  three now-unused imports that deletion left behind: `pydantic.ValidationError`,
  `rlm_harness.trace.EVENT_RESULT`, `ctx_distillery.task.DistillPlan`, confirmed unused with `ruff`, not
  just by eye — F401 is a default-enabled rule and this repo's `lint` CI job runs a bare `ruff check
  .`). `tests/test_rubric.py`'s existing tests are renamed to match, with a new
  `eval/tests/test_score.py` regression guard asserting `ctx_distillery_eval.score` no longer
  defines its own `_plan_from_events` (drift between two copies of the same reconstruction has
  already bitten this project once — see the malformed-`ValidationError` fix below).
- **Added `--extra dev` to the `eval-test` job and the new `studio-test` job, for explicitness —
  CORRECTED per adversarial review, this was NOT fixing a live bug.** An earlier draft of this
  entry claimed `eval-test` (added in the Phase-1 fix, `--directory eval --package
  ctx-distillery-eval python -m pytest`) had been silently failing with "No module named pytest"
  since it landed, because `pytest` supposedly lives only in `ctx-distillery-eval`'s
  `[project.optional-dependencies] dev`. An adversarial review reproduced the EXACT pre-existing
  invocation against a real `uv` binary, from a fully fresh `.venv`, and it passed cleanly, every
  time — the claim was false. Root cause of the misunderstanding: this is a `uv` WORKSPACE, which
  shares ONE venv across all members; the ROOT `pyproject.toml`'s `[dependency-groups] dev =
  ["pytest>=8.0"]` installs pytest into that shared venv by default (no `[tool.uv]
  default-groups` override exists to disable it), regardless of which member's `--package`
  context a given `uv run` is scoped to — `--directory`/`--package` change which member's OWN
  `dependencies` resolve, not whether the shared venv already has pytest from the root's dev
  group. So `--extra dev` was never load-bearing for either job; it is added anyway because it is
  more explicit/self-contained and does not hurt, not because anything was broken.
- **Fixed a real 500 in the Studio, found by the same adversarial review**: `rlm_harness.trace.load_events`
  does no shape validation, so a JSONL line that is syntactically valid JSON but NOT an object
  (`42`, `null`, `[1,2,3]`, `"x"`) parsed fine and reached `plan_from_events`/`trace_facts`/
  `mapper.to_event`'s `.get(...)` calls as-is — a raw `AttributeError`, i.e. a genuine 500,
  reproduced against a running instance on both `GET /v1/runs/{run_id}` and
  `GET /v1/runs/{run_id}/events`. `studio/ctx_distillery_studio/app.py`'s `_load_trace` — the ONE
  entry point every endpoint's events pass through — now filters to dict-shaped entries only,
  immediately after `load_events`, delivering on the Studio's own "never 500 on a malformed
  trace" requirement for real. The same underlying gap pre-exists in `ctx_distillery.rubric`/
  `ctx_distillery.session` for a locally-invoked caller (e.g. `eval/cli.py`'s real-trace-file path)
  — stated explicitly as separate, tracked future work, not silently rolled into this fix.
- **Fixed three real bugs an adversarial review found in the rubric/eval pass**, before merge: (1)
  the `eval-test` CI job never actually ran `eval/tests/` — `--package` only selects which
  workspace member's ENVIRONMENT to use, not pytest's cwd/`testpaths` resolution, so it silently
  re-ran the root package's suite three times and never executed the one-way-boundary test gating
  the whole eval-member invariant; fixed with `--directory eval`, verified against a real `uv`
  binary. (2) `_plan_from_events` (duplicated in `rubric.py` and `eval/ctx_distillery_eval/score.py`)
  raised an unhandled `pydantic.ValidationError` on a well-formed dict with the wrong shape,
  reproduced end-to-end: one malformed trace in a glob killed the entire scoring batch. Now degrades
  to `None` on that shape too, matching `assemble(events, None)`'s own "none of them raise"
  philosophy. (3) "mandatory transcript" was only enforced structurally (a required CLI arg) — an
  EMPTY transcript file slipped straight through and scored to completion. `_read_transcripts` now
  refuses empty/whitespace-only content, loudly. Added `eval/tests/test_cli.py`, since the review
  noted `cli.py`/`taskset.py` had zero test coverage before this — exactly the surface two of these
  three bugs lived in.
- **ATLAS rubric facts + `ctx-distillery-eval` (Phase 1 of the rubric/eval/studio initiative)** —
  `ctx_distillery/rubric.py` (new): a reward-free, deterministic TF/TA/TG/PA rubric on top of
  `rlm_harness.rubric`. `default_rubric()` is the same fixed four-criterion skeleton every run carries
  (`DistillSession`'s task shape never varies); `trace_facts(events)` sources candidate-level facts
  from `session.assemble()`'s output (never re-derived from raw events) plus two trace-only facts
  `assemble` doesn't surface — `min_read_step`/`min_draft_step` (the MINIMUM `step_id` among
  evidence-gathering vs. drafting tool_calls, a real ordering fact, not an inference from two
  counts) and `any_circuit_broken`. `trace_facts` takes only `events` (matching
  `diff_sentry.rubric.trace_facts`'s single-arg signature), so it reconstructs the `DistillPlan`
  itself via `_plan_from_events` (the run's LAST `EVENT_RESULT` payload) before calling `assemble`.
  Adds `n_bad_skill_scope` (a `promote_to_skill` candidate whose `key_fields["scope"]` isn't
  `"project"`/`"global"`) as its own dedicated PA fact, since `session.assemble()` never inspects
  `key_fields` at all. `session.run_distillation` now records the rubric into `run_meta["rubric"]`
  (`rubric_to_meta(default_rubric())`) on every run — two new lines in `session.py`, per the
  implementation plan's own correction.
- **`eval/` — a new `ctx-distillery-eval` workspace member** (root `pyproject.toml` gains
  `[tool.uv.workspace] members = ["eval"]`), an offline, reward-free evaluation harness scoring the
  assembled PLAN artifact (not the trajectory) plus its transcript excerpt(s) against the same
  TF/TA/TG/PA codes, artifact-framed. A ONE-WAY reader of `ctx_distillery`'s public surface
  (`session.assemble`, `task.DistillPlan`) — never imported back (`eval/tests/test_boundary.py`),
  rubric-free judge prompt, static read only. **Resolved per implementation-plan audit**: a
  finished trace never carries the raw transcript verbatim (redacted host-side, passed as a task
  input, never a `tool_call`), and scoring against `read_transcript_chunk`/`read_memory_file`
  tool_call results is not a viable substitute either — those payloads carry only
  offset/length/path/chars metadata, never the body. So `score_run`/the CLI take the transcript
  path(s) as a MANDATORY second input alongside the trace path; there is no trace-only fallback.
  Ships with a fully offline, deterministic `StubJudge` (fixed scores) as the tested default path;
  a real judge is opt-in behind the `judge` extra, not wired up this pass. CLI:
  `ctx-distillery-eval score <trace_glob> <transcript_path> [<transcript_path> ...]` — one
  invocation's transcript(s) apply to every run `trace_glob` matches, a stated simplification for
  batches spanning more than one transcript set (documented in `cli.py`'s module docstring).
  `.github/workflows/ci.yml` gains a matching `eval-test` job.
- **Real Claude Code storage auto-discovery** — `ClaudeCodeAdapter.for_project(project_dir)`, a new
  alternate constructor (the explicit `ClaudeCodeAdapter(memory_dir, transcripts)` is UNCHANGED and
  still the right entry point for a test or advanced caller). It computes `sanitize(project_dir)`
  (every `/` of the absolute path → `-`), derives
  `<claude_home>/projects/<sanitized>/memory`, discovers every sibling `<session-id>.jsonl` as one
  transcript, and points skill enumeration at both skill roots. `home=` overrides `~/.claude`
  everywhere, which is how the tests stay hermetic — no test reads this machine's real `~/.claude`.
  Every part is confirmed, but the EVIDENCE differs and each part says which it rests on — a stale
  CONFIRMED label outliving the evidence that set it is the failure mode this project has now caught
  in its own docs twice (`CLAUDE.md` invariant 6 records both). The sanitization rule, the transcript
  layout and the global skill layout are CONFIRMED by direct inspection. The project-repo-relative
  `<project>/.claude/skills/` location is CONFIRMED by a dedicated control experiment: a scratch
  directory seeded with a probe skill WAS read by a genuinely fresh `claude -p` launched inside it
  (listed and invokable) while a sibling control directory without `.claude/skills/` was not,
  isolating the effect to the project-relative directory rather than a global leak. Two caveats it
  surfaced are BUILT ON by `make_skill_validator` and `apply_plan._promote_skill`: a global skill of
  the same name SHADOWS a project one, and a project's very first skills directory needs a Claude
  Code restart to be discovered. The `memory/` SUB-PATH began as this project's pre-existing
  assumption and is CONFIRMED by observation: 12 of 24 real project storage directories carry one,
  every one a direct child, holding 51 `.md` files and 9 `MEMORY.md` indexes.
- **A JSONL → text renderer** (`render_transcript_events` / `render_transcript_file`) turning raw
  events into the `list[str]` the pipeline already expects — deliberately LOSSY, and specified rather
  than improvised, covering the shapes really observed on disk: only `user`/`assistant` events are
  rendered (no other event type carries `message`/`timestamp`/`isSidechain` at all, so the renderer
  filters FIRST); `message.content` is handled as EITHER a plain string (which really occurs) or a
  list of blocks; `text`/`thinking` contribute their text verbatim, `tool_use` a `[used tool: X]`
  label, and `tool_result` a size label whose UNIT depends on ITS OWN content being a string
  (`N chars`) or a list (`N blocks`) — both occur, so neither shape is assumed; an unrecognized block
  type becomes `[unrecognized content block: X]` rather than raising or vanishing. A torn or
  non-JSON line is skipped, not fatal. `isSidechain` events are skipped BY DEFAULT — this entry used
  to call that "a DEFENSIVE NO-OP … not currently removing subagent noise" and to describe subagent
  distillation as "a deferred extension (same file shape, different glob)", and both are CORRECTED
  IN PLACE here (this section sits under the only version heading in the file, so it is not shipped
  history to supersede). `isSidechain` is the field that separates the two transcript STORES: `false`
  on 0 of 57,928 user/assistant events across 883 session files, and `true` on 72,126 of 72,126
  across 874 subagent files. Reading a subagent transcript therefore needs `include_sidechain=True`
  explicitly (see the subagent entry at the top of this section), and it is a recursive WALK under
  `<session-id>/subagents/`, not a different glob.
- **`list_targets()` now returns `kind="skill"` refs — for BOTH scopes**, closing the previously
  stated gap ("never returns `kind="skill"` yet") at both ends rather than only the global one:
  `~/.claude/skills/*/SKILL.md` as `scope="global"` and `<project_dir>/.claude/skills/*/SKILL.md` as
  `scope="project"`. A skill is a DIRECTORY, so a ref's name falls back to the DIRECTORY name, never
  the `SKILL` file stem (which would name every skill identically). The read-side containment
  discipline extends to the nested layout: a symlinked skill directory resolving outside the root
  never joins the trusted snapshot. Enumeration is OPT-IN on the explicit constructor, so
  `apply.py`'s re-scan can never silently reach into a real `~/.claude/skills`.
- **`ArtifactRef` gains `scope`** (`"global"` / `"project"`), with a KIND-DERIVED default rather than
  a blanket one: a skill defaults to `"global"`, while a memory or index ref is inherently
  `"project"` (this project's memory store has no global counterpart, so a blanket `"global"` default
  would flatly mislabel it). An unrecognized scope raises — `apply.py` routes a write by this field.
- **`draft_skill_file`'s frontmatter schema corrected** — `name` + `description` stay the ONLY
  required fields; `when_to_use` / `dispatch_intent` are accepted as OPTIONAL extras, passed through
  verbatim when present and never grounds for rejecting a draft. Every real installed skill inspected
  carries both, but all of them were one author's single homogeneous suite, and Anthropic's own
  documented Agent-Skills convention requires neither — mandating them would generalize from N=1. All
  THREE places that encode the shape moved together, because they drift apart otherwise: the
  validator, `_spec_for_skill`'s model-facing PROMPT TEXT, and `ClaudeCodeAdapter.schema_for("skill")`.
- **Scope-aware collision checking.** `drafting._existing_names(index, kind, scope)` filters by scope
  itself (the helper, not just its caller), and `draft_skill_file` now takes a `scope` argument the
  validator reads back for the current call — the two skill stores are independent namespaces, so the
  same name at the OTHER scope is not a collision and refusing it would block a legitimate draft. No
  stated scope falls back to the union: weaker for the drafter, never wrong for the store.
- **`task.py`'s `_INSTRUCTIONS` teach the `key_fields["scope"]` convention** for `promote_to_skill`
  (and how to DECIDE it: a finding tied to this project's own tooling/conventions is `"project"`, a
  genuinely portable technique is `"global"`), mirroring how `prune`'s `target_path` is already
  taught. Pinned by a test so the prompt half and the apply half cannot drift.
- **`apply.py`'s skill-write path — an architecture fix, not a new path string** (the biggest gap the
  audit found). The shipped `_promote` wrote a FLAT `<slug>.md` under ONE root and refused anything
  whose `resolved.parent != root`; a skill's real target is `<skills_root>/<slug>/SKILL.md`, one
  directory deeper and under a root that is never `memory_dir` — so the existing check would have
  REFUSED every legitimate skill write. `apply_plan` now takes roots PER KIND (`memory_dir` as before,
  plus `global_skills_dir=` / `project_skills_dir=`, derived with the same
  `global_skills_root()` / `project_skills_root()` helpers `for_project` uses, so reader and writer
  cannot disagree about a location), routes a `promote_to_skill` by its own `key_fields["scope"]`, and
  checks the nested target with its OWN function (`_skill_target`): the slug must carry no path
  separator or traversal segment, `<root>/<slug>` must resolve to a DIRECT child of the root, the
  `SKILL.md` there must resolve inside that directory, and `<root>/<slug>` must not already exist as
  something else (a non-directory is refused even WITH `overwrite`, which only ever replaces a drafted
  `SKILL.md`). A missing or bogus scope is refused rather than defaulted, and a scope whose root the
  caller did not pass is refused too — the caller decides where a skill may be installed.
  `test_a_skill_promotion_takes_the_same_write_path` is REPLACED (it pinned the flat behaviour the
  research showed to be wrong) by tests asserting the real nested shape, plus escape and collision
  refusals for both scopes.
- `ctx_distillery/apply.py` — **the apply step**: `apply_plan(memory_dir, assembled_plan,
  approved_ids)`, the human-gated, host-side action that finally turns an approved plan into real
  file changes. Structurally outside the RLM (nothing on the planner's path imports it; no adapter
  method was added for it), it takes explicit per-candidate approval by list index, and returns one
  `ApplyOutcome` per candidate (`applied` / `refused` + reason / `skipped`-not-approved / `noop` for
  a `keep`) so the one step that mutates disk is not the one step that leaves no audit record. The
  five gaps an independent design review found are all closed in the implementation: the collision
  authority is a FRESH `ClaudeCodeAdapter(memory_dir).list_targets()` re-scan at apply time (a plan's
  own snapshot is stale by construction); a promotion's filename is `slugify(frontmatter["name"]) +
  ".md"` with a degenerate slug refused rather than replaced by an invented fallback; the write side
  enforces the same containment check the read side does (`resolved.parent == memory_dir`) so a
  symlink in the memory store cannot redirect a write outside it; the file is created with
  `open(path, "x")` (O_EXCL) so a collision is caught atomically rather than by a racy
  check-then-write, with an `overwrite_ids` escape hatch scoped to individual candidates and never
  global; and `prune` ARCHIVES to `<memory_dir's parent>/_ctx_distillery_archive/<timestamp>-<name>`
  — outside the memory store, so no future scan can re-surface it as live — never deletes. A
  candidate carrying `problems`, `draft_ok is False`, or an empty promotion draft is refused
  regardless of approval, and `MEMORY.md` is never a valid promotion or prune target.
- `task.py`'s `_INSTRUCTIONS` (and `DistillCandidate.key_fields`' description) now state the
  `prune` target convention: a prune candidate MUST set `key_fields["target_path"]` to the exact
  path of an existing artifact, verbatim from `list_memory_files()`. `key_fields` stays the
  free-form dict it always was — the convention is documented and enforced at apply time (a
  missing / non-matching / `kind="index"` target is refused, never guessed at), and pinned by a test
  so the prompt half and the apply half cannot drift apart.
- `tests/test_no_write_capability.py` exempts `apply.py` from the mutation scan — it IS the
  human-gated writer — and pins the property that makes the exemption safe instead: a new
  reachability test asserts no module on the RLM path imports it.
- `DistillSession` is wired and offline-tested end to end: five READ-ONLY tools
  (`list_memory_files`, `read_memory_file`, `read_transcript_chunk`, `draft_memory_file`,
  `draft_skill_file`), the `pyodide` pin ENFORCED in code (`dataclasses.replace` on the config
  before `super().__init__`, not just documented), and a real scripted forward pass through
  `rlm_harness.testing.ScriptedInterpreter` covering planner → tools → SUBMIT.
- `ClaudeCodeAdapter` — the one in-scope harness adapter. Enumerates `memory/*.md` with real,
  NESTED-YAML frontmatter, plus `MEMORY.md` itself as a third `ArtifactKind`, `"index"` (needed so
  the plan can flag candidate `MEMORY.md` index lines at all: a kind that is never enumerated is
  unreachable through `read_memory_file`'s allowlist). Every path is stored `.resolve()`d.
- `ctx_distillery/frontmatter.py` (+ a `pyyaml` dependency) — `rlm_harness.skills`'s frontmatter reader
  only handles flat `key: value` lines and cannot express the memory schema's nested
  `metadata.type`, so parsing lives here and is used by BOTH the adapter and the drafting validators.
- `ctx_distillery/redact.py` — pattern-based, best-effort host-side redaction, applied immediately
  after the single `ingest()` so the redacted list is the only one the model can reach.
- `ctx_distillery/session.py` — `run_distillation` (ingest once, redact once, run once) and
  `assemble`, which re-sources each promotion's verbatim drafted text from its `tool_call` event by
  `artifact_id` and reports an unbacked candidate as a problem rather than trusting the plan.
- Tools close over an immutable index SNAPSHOT, never a live adapter — `HarnessAdapter` promises
  nothing about `list_targets()` being stable, so a live reference could shift the read allowlist
  mid-run. `read_memory_file`'s check is an exact resolved-path match, never a prefix/substring test.
- `tests/test_no_write_capability.py` — the design-mandated write-capability scan.
- Initial scaffold: `RLMTask` declaration stub (`DistillSession`, no tools wired yet),
  harness-adapter seam interface (Claude Code adapter deferred, not yet implemented), the planning
  reference doc, CI, project conventions synced from rlm-harness's downstream sibling consumers.
