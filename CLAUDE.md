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

- This is a first scaffolding pass: there is no live-model integration yet, so there is
  nothing to run against real credentials or a sandbox. `tests/test_import.py` is the only
  test — it just confirms the package and `DistillSession` import cleanly.
- Once tools land, follow rlm-kit's own pattern: lint with `ruff check .` (line-length 110,
  matching rlm-kit's config) and run the suite with `pytest`, offline first via
  `rlm_kit.testing.ScriptedInterpreter` before any live `dspy.RLM` run.

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
   the sandbox, not a convention the planner could be prompted around.
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
   reachable from an RLM tool — the actual "apply" step, if it's ever built, stays a separate,
   human-gated action outside the RLM trajectory entirely.

## Harness scope

Claude Code is the only adapter being built — it's the only platform whose real persistence
format has been directly verified. Codex, Hermes, OpenClaw, and OpenCode are named future
targets in `docs/DESIGN.md`, deliberately **not** designed yet: their real on-disk formats
haven't been inspected, and guessing one would be speculation dressed as design. Don't add an
adapter for any of them until someone has actually looked at that harness's real format.
