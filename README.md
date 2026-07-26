# ctx-distillery

`ctx-distillery` reads AI coding-agent session transcripts — plus the persistent memory store
those sessions built up — and uses an RLM ([`rlm-kit`](https://github.com/qazbnm456/rlm-kit)) to
propose a distillation plan: what's safe to prune, what should be cross-referenced or merged
across sessions, and what durable knowledge is worth promoting into a standing memory file or a
reusable Skill. It is a judgement engine, nothing more.

**Status: early scaffold.** This repo pins down the project structure, the dependency on
rlm-kit, and the output contract. The actual planning tools (transcript/memory readers, drafting
tools) are not implemented yet — see `ctx_distillery/task.py` for the honest, clearly-marked
skeleton and `docs/DESIGN.md` for the full design this scaffold is built against.

## It never touches your files

Three things are true about every run, and they're worth stating up front because the operation
this tool reasons about — pruning or rewriting your own history — is irreversible if gotten
wrong:

1. **Every run produces a proposed plan, never a mutation.** The output is a list of judgements
   (`keep` / `prune` / `promote_to_memory` / `promote_to_skill`) over your transcripts and memory
   index. Nothing is deleted, rewritten, or created on disk by the run itself. Applying a plan —
   if that step exists at all — is a separate, explicit action a human takes outside the RLM
   trajectory, after reading the plan.
2. **This is structural, not a convention we promise to honor.** rlm-kit's sandboxed interpreter
   (`pyodide`/Deno, the default and the only one this task uses) has no host filesystem write
   access at all. Combined with wiring zero write-capable tools, the model has no code path to
   mutate a file — full stop, not "we told it not to." See `CLAUDE.md` for the specific
   invariants this rests on.
3. **A human applies the plan, if anything ever does.** The dry-run plan is the only mode. There
   is no "auto-apply" flag now, and if one is ever added it will be a separate, explicit,
   human-confirmed action — never something the planner's own tools can trigger.

## Two distinct promotion targets

Not every durable finding belongs in the same bucket. The planner distinguishes:

- **Memory** — a fact about the user or the project: a decision that was made, a constraint that
  was discovered, a piece of state worth remembering ("this project froze merges on date X").
- **Skill** — a reusable *procedure*: a workflow, technique, or recipe worth documenting once and
  reusing on demand ("when doing Y, always check Z first, because of incident W").

These are authored by two separate drafting tools and validated against two separate structural
schemas (frontmatter shape, non-colliding name) — never one undifferentiated "promote" action.
The plan's own output only ever carries `{action, artifact_id, key_fields}` per candidate; the
actual drafted text is re-sourced, on read, from the tool-call event that produced it — never
trusted from the plan's own claim about what it wrote.

## Harness-agnostic by design — Claude Code today

The planning core is meant to work over any AI coding agent's transcript + memory format, not
just one. That's bridged through a thin **adapter seam** — `ingest()` / `schema_for(kind)` /
`list_targets()` — the same base/wrap split rlm-kit uses for tool extension, applied one layer up:
the harness is the "provider," not a model or API. See `ctx_distillery/adapters/base.py` for the
interface.

Right now, **only a Claude Code adapter is in scope to build**, because it's the only platform
whose real on-disk persistence format (the memory frontmatter schema, the `MEMORY.md` index,
scratchpad conventions) has actually been inspected and verified. Codex, Hermes, OpenClaw, and
OpenCode are named as **future targets** in `docs/DESIGN.md` — deliberately not designed yet.
Their real formats haven't been looked at from here, and guessing one would be speculation
dressed as design, not genuine multi-harness support. When one of them is actually in scope, the
honest next step is the same one taken for Claude Code: read its real current format first, then
write the adapter.

## Project layout

```
ctx_distillery/
  task.py            # DistillSession(RLMTask) — signature, output_model, instructions
  adapters/
    base.py           # the read-only adapter interface (no implementation yet)
docs/
  DESIGN.md            # the full design doc this scaffold is built against
tests/
  test_import.py       # smoke test
```

## Relationship to rlm-kit

`ctx-distillery` is a thin declaration on top of `rlm-kit`'s `RLMTask` — the retry/validation
loop, sandbox selection, budget caps, tracing, and dataset export are all inherited, not
reimplemented here. See rlm-kit's own README, "Building a consumer," for the pattern this project
follows.
