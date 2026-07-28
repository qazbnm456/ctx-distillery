# Context preservation (read before auto-compacting)

ctx-distillery routes durable knowledge into its tracked docs — keep using them, and when the conversation
is about to compact, preserve only what they do NOT already hold:

- **Stable invariants** → the **Invariants** section of `CLAUDE.md`.
- **Resolved decisions / shipped changes** → `CHANGELOG.md` (under the current version).
- **Open / proposed work** → the issue tracker, or the CHANGELOG's `[Unreleased]` section.

So a handoff summary should carry the *in-flight session state* those files miss. Prioritize, in order:

1. **Decisions we agreed on this session** not yet in CHANGELOG/CLAUDE — design choices and the *reason*.
   e.g. "the plan's `artifact_id` is the single generic key for both `promote_to_memory` and
   `promote_to_skill` — the earlier `memory_file_id`-specific key didn't survive the skill split," or
   "redaction happens host-side in the ingestion tool, never deferred to the planner's judgement."
2. **Files / symbols changed**, as `path:symbol` one-liners on the *final* shape — e.g.
   `task.py:DistillSession` — signature/output_model/instructions only, tools still `[]`;
   `adapters/base.py:HarnessAdapter` — the `ingest`/`schema_for`/`list_targets` read-only interface, no
   concrete subclass yet; `task.py:DistillCandidate.artifact_id` — resolved on read by matching a
   `draft_memory_file`/`draft_skill_file` tool-call event, never trusted from the plan's own claim.
3. **Current status.** What passes the suite (and the count), what's broken, last command + result.
4. **Open suggestions / TODOs** not yet tracked — mark each `proposed`, `accepted-not-done`, or `rejected`.
   e.g. the memory-vs-skill promotion split (a fact about the user/project vs. a reusable how-to) and the
   redaction policy (sensitive transcript content redacted before it becomes LM context) are both
   `accepted-not-done` — the design was agreed and written down, no tool implements either yet.
5. **The seams' status** — especially which harness adapters exist vs. are still deferred. Right now:
   Claude Code adapter buildable (real format inspected), Codex/Hermes/OpenClaw/OpenCode deliberately
   *not* designed (formats unverified) — don't let a session imply progress on those without a real
   format inspection to back it.
6. **In-flight user intent + acceptance criteria** for this session. Without it a resumed session drifts.

**Do NOT preserve** (reconstructable / already durable):

- Anything already in `CLAUDE.md`, `CHANGELOG.md`, `README.md`, `VENDOR.md`, `.env.example`, or
  `pyproject.toml`.
- Tool-call transcripts, `grep` output, file listings, full file contents readable from disk.
- Step-by-step exploration narration; speculative reasoning that led to no decision.

**Format for a handoff summary** (use when compaction is imminent or the user asks for a recap):

```
## Session state
- Goal: <one sentence>
- Status: <what passes the suite, what doesn't, last command + result>

## Decisions
- <decision> — <why>   (→ promote to CLAUDE.md invariant / CHANGELOG.md)

## Changed
- <path:symbol> — <what & why>

## Open
- [proposed|accepted-not-done|rejected] <item>   (→ issue tracker if durable)

## Seams
- <harness adapter> — <buildable | deferred, and why>
```

Keep it under ~40 lines. If something fits one of the tracked docs, put it THERE instead of in the
summary.
