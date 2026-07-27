# Changelog

All notable changes to `ctx-distillery` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`ctx-distillery` is an RLM-driven distillation planner, built on [`rlm-kit`](https://github.com/qazbnm456/rlm-kit),
that reads AI coding-agent session transcripts plus a persistent memory store and proposes a plan —
what to prune, what to merge across sessions, and what to promote into a memory file or a Skill. It
never applies anything itself.

## [Unreleased]

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
  `rlm_kit.rubric`. `default_rubric()` is the same fixed four-criterion skeleton every run carries
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
  What the evidence actually supports is stated per-part rather than uniformly: the sanitization rule,
  the transcript layout, and the global skill layout are CONFIRMED by direct inspection; the `memory/`
  SUB-PATH is this project's pre-existing assumption carried forward, NOT independently re-verified;
  and the project-repo-relative `<project>/.claude/skills/` location is an UNCONFIRMED hypothesis
  (motivated by `.claude/rules/` genuinely being read project-relative) that nobody has verified by
  seeding a test skill and checking whether Claude Code offers it. This pass targets it as the best
  available option for project-scoped promotions and claims nothing more.
- **A JSONL → text renderer** (`render_transcript_events` / `render_transcript_file`) turning raw
  events into the `list[str]` the pipeline already expects — deliberately LOSSY, and specified rather
  than improvised, covering the shapes really observed on disk: only `user`/`assistant` events are
  rendered (no other event type carries `message`/`timestamp`/`isSidechain` at all, so the renderer
  filters FIRST); `message.content` is handled as EITHER a plain string (which really occurs) or a
  list of blocks; `text`/`thinking` contribute their text verbatim, `tool_use` a `[used tool: X]`
  label, and `tool_result` a size label whose UNIT depends on ITS OWN content being a string
  (`N chars`) or a list (`N blocks`) — both occur, so neither shape is assumed; an unrecognized block
  type becomes `[unrecognized content block: X]` rather than raising or vanishing. A torn or
  non-JSON line is skipped, not fatal. `isSidechain` events are skipped as a DEFENSIVE NO-OP, stated
  accurately: it was `false` on all 1216 real events checked, because subagent messages live in
  separate `subagents/agent-<id>.jsonl` files and are never inlined — the filter guards a future
  version that inlines them and is not currently removing "subagent noise". Distilling subagent
  transcripts is a deferred extension (same file shape, different glob).
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
  `rlm_kit.testing.ScriptedInterpreter` covering planner → tools → SUBMIT.
- `ClaudeCodeAdapter` — the one in-scope harness adapter. Enumerates `memory/*.md` with real,
  NESTED-YAML frontmatter, plus `MEMORY.md` itself as a third `ArtifactKind`, `"index"` (needed for
  `docs/DESIGN.md` success criterion (b): a kind that is never enumerated is unreachable through
  `read_memory_file`'s allowlist). Every path is stored `.resolve()`d.
- `ctx_distillery/frontmatter.py` (+ a `pyyaml` dependency) — `rlm_kit.skills`'s frontmatter reader
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
- `tests/test_no_write_capability.py` — the write-capability scan `docs/DESIGN.md` mandated.
- Initial scaffold: `RLMTask` declaration stub (`DistillSession`, no tools wired yet),
  harness-adapter seam interface (Claude Code adapter deferred, not yet implemented), `docs/DESIGN.md`
  planning reference, CI, project conventions synced from rlm-kit's downstream sibling consumers.
