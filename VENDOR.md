# Vendored / external dependencies

ctx-distillery deliberately vendors **nothing**. It is a downstream *consumer* of
[`rlm-kit`](https://github.com/qazbnm456/rlm-kit): it consumes the kit's PUBLIC surface and extends it the
sanctioned way — it never forks the harness, never re-implements tracing, and never copies kit source into
this tree.

## What it consumes from rlm-kit (public surface only)

- **`RLMTask`** — subclassed as `DistillSession` (`ctx_distillery/task.py`). The declaration carries the
  `signature`, `output_field`, `output_model` (`DistillPlan`), and `instructions`; retry/validation,
  sandbox selection, budget caps, and observability are inherited, not reimplemented here.
- **`configure` / `RLMConfig`** — feed rlm-kit's config from this project's own env-driven surface
  (`CD_*` vars, see `.env.example`). `ctx_distillery/config.py`'s `setup(config)` calls
  `rlm_kit.configure(RLMConfig(...))` with the resolved `CD_*` values (both imported lazily, inside
  the function body, so the env-reading half of that module stays stdlib-only). The interpreter pin to
  `pyodide` (see `CLAUDE.md` invariant 1, the structural no-mutation guarantee) is ENFORCED IN CODE, not
  merely committed to in the design: `task._forced_config` runs `dataclasses.replace(config,
  interpreter="pyodide")` before `super().__init__`, so a caller passing `interpreter="local"` still
  gets `pyodide`, and `DistillConfig.from_env` additionally REFUSES a non-`pyodide` `CD_INTERPRETER`
  loudly rather than coercing it silently. There is no `local` and no writable-mount `container` path.
- **`ClaudeAgentLM`** (the optional `[subscription]` extra) — used to run the PLANNER and the sub LM on
  the operator's own Claude Pro/Max subscription when their `CD_*` model id carries the
  `claude-agent-sdk/` sentinel. Imported lazily inside `config._maybe_subscription_lm`'s sentinel branch
  and injected through the public `configure(main_lm=…, sub_lm=…)` seam — not vendored, not wrapped, and
  never reimplemented. The DRAFTER is always a separate OpenAI-compatible endpoint (`config.make_chat_fn`
  builds an `openai.OpenAI` client directly), so it may never carry the sentinel: `DistillConfig.from_env`
  refuses one there unconditionally, including one inherited down the `CD_DRAFT_LM` → `CD_SUB_LM` →
  `CD_ROOT_LM` fallback chain.
- **The trace schema + `rlm_kit.trace` helpers** — every run's tool calls (`draft_memory_file`,
  `draft_skill_file`, and the read-only lookups) are recorded through the standard trace/v1 events. The
  trace is read for auditability (`ctx-distillery show`, `eval/`, `studio/`) **and** exported as a
  reward-free dataset.
- **`rlm_kit.dataset`'s `export_actions` / `export_sft_turns` / `run_label_bundle`** —
  `ctx_distillery/rl_export.py` builds the bundle `ctx-distillery export` prints:
  `{actions, drafting, orchestrator_tools, planner, sft_turns, labels, metrics, rubric_signal}`. Every
  action record carries `reward: null`, and `run_label_bundle` *refuses* a surface literally named
  `reward` (it raises), so "trajectories, never reward" is enforced at the transport rather than merely
  intended here.

  **What is deliberately absent, and the correction that got it right.** An earlier version of this file
  declined an exporter outright, on the grounds that "there's no obvious reward signal for *was this the
  right thing to prune*". That argument is sound — and it defeats exactly one surface: an **oracle**
  labels field. It does not touch `sft_turns` (pure behaviour cloning of the planner's own turns, no
  label at all), `actions`, `planner`, `metrics`, or `rubric_signal`, none of which need ground truth;
  and it mis-described what the sibling projects actually built, which is mostly those. So the exporter
  exists, and `run_labels` is **structural only** — action counts, `finalized`, unbacked/invalid-draft
  counts, the run-level problem strings — every field recomputable from the same JSONL by a second
  reader. Nothing claims a judgement was CORRECT. `cve-reverser`'s `valid`/`complete` is the one sibling
  label with real ground truth behind it (does this template match the patch?); ctx-distillery has no
  equivalent, and inventing one would fabricate the very signal the original objection was right about.

  Two smaller divergences from the siblings' exporters, both forced by this project's own invariants:
  there is **no writing `main()` and no `--out`** (`tests/test_no_write_capability.py` scans
  `rl_export.py` and `cli.py`, and a red tripwire is the finding, not a test to relax — so the CLI does
  `print(json.dumps(...))` to stdout, redirected with `>`, the same shape `show` already has); and
  reading goes through `trace_io.load_trace`, never `rlm_kit.trace.load_events` (`CLAUDE.md` invariant
  11's non-dict-line guard applies to a new reader exactly as to the old ones).
- **`make_model_tool`** — the base primitive for both of this project's LM-backed drafting tools,
  `draft_memory_file` and `draft_skill_file` (per `CLAUDE.md` invariant 2's judgement-only SUBMIT — the
  `output_model` carries only `{action, artifact_id, key_fields}`, never the drafted body; both tools
  are wired in `DistillSession.__init__` and defined in `ctx_distillery/tools/drafting.py`, whose
  validators own this project's frontmatter rules). Both are TEXT-ONLY tools — they author a candidate
  memory/skill body and an `artifact_id`, and never touch the memory directory or a `skill_dir` themselves.
  Two separate tool instances, one per drafting target, following `make_model_tool`'s "one tool per run,
  per-call breaker state in the closure" shape.
- **The skills convention (`skills.py`)** — used only as the TARGET SHAPE `draft_skill_file` must produce
  (frontmatter `name`/`description`, progressive disclosure), not as a loader ctx-distillery itself calls.
  This project does not use `load_skills_as_tools`/`list_skills`/`read_skill` to give its own planner LM a
  skills library — it produces `SKILL.md`-shaped output for a *different* consumer (a harness's own skills
  directory) to eventually read. Don't force a fit where ctx-distillery is both a skills producer and a
  skills consumer of rlm-kit's loader — right now it's only the former.

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
in `CLAUDE.md`'s "Harness scope", deliberately not designed yet. This is ctx-distillery's own seam,
layered on top of rlm-kit, not something rlm-kit itself exposes.

## How rlm-kit is pinned

rlm-kit is public but not yet on PyPI, so it comes in via a commit-pinned git source
(`[tool.uv.sources]` in `pyproject.toml` → GitHub, `branch = "main"`; `uv.lock` pins the exact commit).
Never `pip install` it. When co-developing the kit locally, overlay an editable install
(`uv pip install -e ../rlm-kit`).
