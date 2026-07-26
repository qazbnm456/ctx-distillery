# Vendored / external dependencies

ctx-distillery deliberately vendors **nothing**. It is a downstream *consumer* of
[`rlm-kit`](https://github.com/qazbnm456/rlm-kit): it consumes the kit's PUBLIC surface and extends it the
sanctioned way — it never forks the harness, never re-implements tracing, and never copies kit source into
this tree.

## What it consumes from rlm-kit (public surface only)

- **`RLMTask`** — subclassed as `DistillSession` (`ctx_distillery/task.py`). The declaration carries the
  `signature`, `output_field`, `output_model` (`DistillPlan`), and `instructions`; retry/validation,
  sandbox selection, budget caps, and observability are inherited, not reimplemented here.
- **`configure` / `RLMConfig`** — will feed rlm-kit's config from this project's own env-driven surface
  (`CD_*` vars, see `.env.example`) once `DistillSession` is wired to a live run — not yet imported in
  `ctx_distillery/task.py`'s current stub. The design *commits* to pinning the interpreter explicitly to
  `pyodide` (see `docs/DESIGN.md`, "structural no-mutation guarantee") and never switching to `local` or
  a writable-mount `container` config — that pin still needs to land as a real constructor kwarg once
  the task is wired up; it is not yet reflected in code.
- **The trace schema + `rlm_kit.trace` helpers** — every run's tool calls (`draft_memory_file`,
  `draft_skill_file`, and the read-only lookups) are recorded through the standard trace/v1 events. Per
  `docs/DESIGN.md`, this project's use of the trace is for auditability, not for producing an RL dataset —
  there's no obvious reward signal for "was this the right thing to prune." That's a reasonable variant,
  not a misfit: rlm-kit's "trajectories, never reward" invariant constrains what the *kit* computes, not
  what a consumer must do with the trace, and no `export_*` call is forced on a consumer.
- **`make_model_tool`** — the base primitive for both of this project's LM-backed drafting tools,
  `draft_memory_file` and `draft_skill_file` (per `docs/DESIGN.md`'s "judgement-only SUBMIT" section, and
  `ctx_distillery/task.py`'s TODO enumeration). Both are TEXT-ONLY tools — they author a candidate
  memory/skill body and an `artifact_id`, and never touch the memory directory or a `skill_dir` themselves.
  Two separate tool instances, one per drafting target, following `make_model_tool`'s "one tool per run,
  per-call breaker state in the closure" shape.
- **The skills convention (`skills.py`)** — used only as the TARGET SHAPE `draft_skill_file` must produce
  (frontmatter `name`/`description`, progressive disclosure), not as a loader ctx-distillery itself calls.
  This project does not use `load_skills_as_tools`/`list_skills`/`read_skill` to give its own planner LM a
  skills library — it produces `SKILL.md`-shaped output for a *different* consumer (a harness's own skills
  directory) to eventually read. See `docs/DESIGN.md`, "Skill promotion as a distinct target," for the
  distinction; don't force a fit where ctx-distillery is both a skills producer and a skills consumer of
  rlm-kit's loader — right now it's only the former.

## The three sanctioned extension points (and only these)

1. Subclass `RLMTask` — `DistillSession`.
2. Add tools the base/wrap way — `draft_memory_file` / `draft_skill_file` on `make_model_tool`, plus the
   read-only lookups (`list_memory_files`, `read_memory_file`, `read_transcript_chunk`) as generic
   host-side wrappers, all sourced from a `HarnessAdapter` (below) so the planner core stays
   harness-agnostic.
3. Read results through the trace + exporters.

## This project's OWN extension seam (not rlm-kit's)

The `ctx_distillery.adapters.HarnessAdapter` interface (`ctx_distillery/adapters/base.py`) is how a future
agent-platform target (Codex/Hermes/OpenClaw/OpenCode) plugs in — read-only (`ingest`/`schema_for`/
`list_targets`), no write path. Only a Claude Code adapter is in scope to build today, because it's the
only platform whose real on-disk format has actually been inspected; the others are named future targets
in `docs/DESIGN.md`, deliberately not designed yet. This is ctx-distillery's own seam, layered on top of
rlm-kit, not something rlm-kit itself exposes.

## How rlm-kit is pinned

rlm-kit is public but not yet on PyPI, so it comes in via a commit-pinned git source
(`[tool.uv.sources]` in `pyproject.toml` → GitHub, `branch = "main"`; `uv.lock` pins the exact commit).
Never `pip install` it. When co-developing the kit locally, overlay an editable install
(`uv pip install -e ../rlm-kit`).
