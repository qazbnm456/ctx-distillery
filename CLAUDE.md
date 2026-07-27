# ctx-distillery — agent guide

`ctx-distillery` is a downstream consumer of [`rlm-kit`](https://github.com/qazbnm456/rlm-kit):
an RLM-driven planner that reads AI coding-agent session transcripts + a persistent memory
store and proposes a distillation plan (what to prune, what to merge across sessions, what to
promote into a memory file or a Skill file). It never applies anything itself. See
`docs/DESIGN.md` for the full design and `README.md` for the overview.

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

## Invariants — do not break

These are the hard constraints from `docs/DESIGN.md`; they exist because the operation this
project reasons about (pruning/deleting a user's own history) is irreversible.

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
6. **`apply.py` is the ONE writer, and it is unreachable from the RLM.** It is human-called
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

## Known simplifications (stated, not hidden)

- **`read_memory_file` reads through `ArtifactRef.path` directly**, not through a fourth adapter
  method. The ABC answers "what exists" and "give me everything", not "give me one body on
  demand"; every in-scope harness is a local filesystem, so a plain read of the enumerated,
  already-resolved path is honest. Whether a future non-filesystem harness needs a different read
  seam is deferred to when that harness is actually designed.
- **`ClaudeCodeAdapter` does not locate Claude Code's transcript storage.** The caller supplies
  already-loaded transcript text; finding the real on-disk location is future work.
- **`list_targets()` never returns `kind="skill"` entries yet** (that storage location hasn't been
  inspected), so `draft_skill_file`'s collision check currently runs against an empty set — weaker
  than it will be, not wrong.
- **No CLI entry point**, and no adapter for any harness other than Claude Code. `apply_plan` is
  called from Python (or a REPL) by a human who has read the plan; a thin CLI wrapper over it is
  future work.
- **`apply.py` archives, and nothing purges.** A pruned file is moved to
  `_ctx_distillery_archive/`, never deleted; deleting the archive for real is a separate, explicit
  `purge` operation that does not exist yet. That is deliberate — "still recoverable" beats
  "irreversible" even at the human-approved step.
- **`apply_plan` only knows the Claude Code layout** (it builds a `ClaudeCodeAdapter` directly).
  Generalising the apply step across harnesses waits for a second adapter to actually exist.

## Harness scope

Claude Code is the only adapter being built — it's the only platform whose real persistence
format has been directly verified. Codex, Hermes, OpenClaw, and OpenCode are named future
targets in `docs/DESIGN.md`, deliberately **not** designed yet: their real on-disk formats
haven't been inspected, and guessing one would be speculation dressed as design. Don't add an
adapter for any of them until someone has actually looked at that harness's real format.
