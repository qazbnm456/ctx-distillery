# Changelog

All notable changes to `ctx-distillery` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`ctx-distillery` is an RLM-driven distillation planner, built on [`rlm-kit`](https://github.com/qazbnm456/rlm-kit),
that reads AI coding-agent session transcripts plus a persistent memory store and proposes a plan —
what to prune, what to merge across sessions, and what to promote into a memory file or a Skill. It
never applies anything itself.

## [Unreleased]

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
