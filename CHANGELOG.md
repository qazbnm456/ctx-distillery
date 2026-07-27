# Changelog

All notable changes to `ctx-distillery` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`ctx-distillery` is an RLM-driven distillation planner, built on [`rlm-kit`](https://github.com/qazbnm456/rlm-kit),
that reads AI coding-agent session transcripts plus a persistent memory store and proposes a plan —
what to prune, what to merge across sessions, and what to promote into a memory file or a Skill. It
never applies anything itself.

## [Unreleased]

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
