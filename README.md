# ctx-distillery

`ctx-distillery` reads AI coding-agent session transcripts — plus the persistent memory store
those sessions built up — and uses an RLM ([`rlm-kit`](https://github.com/qazbnm456/rlm-kit)) to
propose a distillation plan: what's safe to prune, what should be cross-referenced or merged
across sessions, and what durable knowledge is worth promoting into a standing memory file or a
reusable Skill. It is a judgement engine, nothing more.

**Status: the planner is wired and offline-tested; the apply step exists; storage is auto-discovered.**
The five read-only planning tools, the Claude Code adapter, the assemble-on-read convention, the
human-gated `apply_plan`, and auto-discovery of Claude Code's real on-disk storage (transcripts +
both skill scopes) are implemented. Still missing: a CLI, subagent-transcript distillation, and any
harness other than Claude Code. See `docs/DESIGN.md` for the full design and `CLAUDE.md` for the
invariants.

## Point it at a project

```python
from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter.for_project("/path/to/your/project")
```

That's the whole setup. `for_project` finds Claude Code's real storage for that project — every past
conversation's transcript, the project's memory store, and the skills it can already see — instead of
making you assemble paths and text by hand. Concretely: a project's storage lives at
`~/.claude/projects/<the project's absolute path with every "/" replaced by "-">/`, each past
conversation is one `<session-id>.jsonl` file in there, and skills live one directory per skill
(`<name>/SKILL.md`) under `~/.claude/skills/` globally or `<project>/.claude/skills/` per project.

Two things worth knowing, because they are honest limits rather than polish:

- **Transcripts are rendered lossily on purpose.** A real long conversation's raw log is
  multi-megabyte; feeding it back verbatim would defeat the point of distilling it. What survives is
  what was said and decided — message text and thinking verbatim, tool calls as short
  `[used tool: X]` labels, tool results as size labels. Subagent conversations are stored separately
  and aren't read yet.
- **The project-scoped skills location is not confirmed to work.** `~/.claude/skills/` is verified —
  it's where your installed skills actually live. `<project>/.claude/skills/` is where this tool
  writes a project-scoped skill, and it is a *reasoned guess*: nobody has verified that Claude Code
  discovers a skill there. (The precedent is real — this repo's own `.claude/rules/` files are read
  project-relative — but precedent isn't proof.) If you care, check it first: seed a test skill in a
  project, start a fresh session there, and see whether it's offered. Until then, treat a
  project-scoped promotion as "written where we believe it belongs," not "installed and picked up."

## It never touches your files

Three things are true about every run, and they're worth stating up front because the operation
this tool reasons about — pruning or rewriting your own history — is irreversible if gotten
wrong:

1. **Every run produces a proposed plan, never a mutation.** The output is a list of judgements
   (`keep` / `prune` / `promote_to_memory` / `promote_to_skill`) over your transcripts and memory
   index. Nothing is deleted, rewritten, or created on disk by the run itself. Applying a plan is a
   separate, explicit action a human takes outside the RLM trajectory, after reading the plan —
   `ctx_distillery/apply.py`, described below.
2. **This is structural, not a convention we promise to honor.** rlm-kit's sandboxed interpreter
   (`pyodide`/Deno, the default and the only one this task uses) has no host filesystem write
   access at all. Combined with wiring zero write-capable tools, the model has no code path to
   mutate a file — full stop, not "we told it not to." See `CLAUDE.md` for the specific
   invariants this rests on.
3. **A human applies the plan, if anything ever does.** The dry-run plan is the only mode the RLM
   has. There is no "auto-apply" flag and never will be: the apply step is a separate module a
   human calls by hand, and nothing the planner can reach imports it.

## Applying a plan (the human-gated step)

```python
from ctx_distillery.adapters.claude_code import global_skills_root, project_skills_root
from ctx_distillery.apply import apply_plan

# You read the plan first. Then you name the candidates you approve, by list index.
for outcome in apply_plan(
    "path/to/project/memory",
    assembled_plan,
    approved_ids={0, 3, 7},
    # Only needed if you approved a skill promotion — and only the scope(s) you want to allow.
    global_skills_dir=global_skills_root(),
    project_skills_dir=project_skills_root("/path/to/your/project"),
):
    print(outcome.index, outcome.action, outcome.status, outcome.reason)
```

There is deliberately no "apply everything" call. A reviewer who approves eight of ten candidates
shouldn't have to fight the API to reject the other two — for an irreversible operation the safe
path should be the default one. What `apply_plan` guarantees:

- **A prune archives, it never deletes.** The file is moved to
  `_ctx_distillery_archive/<timestamp>-<name>` *beside* your memory directory — outside it, so no
  future scan mistakes an archived file for a live one. Deleting the archive for real would be a
  separate `purge` step, which doesn't exist yet.
- **It re-scans your memory store itself, at apply time.** The plan you're applying may be hours or
  days old; its own view of what exists is stale by construction, so it is never used as the
  authority for "does this name already exist" or "is this prune target real."
- **A promotion is created with an exclusive create** (`O_CREAT|O_EXCL`), so a name collision is
  caught atomically instead of by a racy check-then-write. It refuses that one candidate with a
  clear message; overwriting is an explicit opt-in for that one candidate, never a global flag.
- **Nothing is written outside the directory that kind of artifact belongs in.** A memory file must
  resolve to a direct child of your memory directory — the same containment check the read side uses,
  for the same reason (a symlink in the store resolves elsewhere). A *skill* has a different real
  shape, so it gets its own check: it goes to `<skills root>/<slug>/SKILL.md`, and that must resolve
  to exactly that, with nothing already sitting there. Which skills root comes from the candidate's
  own declared scope — global or project — and if you didn't pass a root for that scope, the
  promotion is refused rather than installed somewhere it wasn't invited.
- **A candidate the run itself flagged is refused even if you approved it** — a draft that failed
  its format check, an empty draft, or any assembled candidate carrying problems.
- **Every candidate comes back with an outcome**, including the ones you didn't approve. The step
  that mutates disk should be the last place to keep no record.

## Two distinct promotion targets

Not every durable finding belongs in the same bucket. The planner distinguishes:

- **Memory** — a fact about the user or the project: a decision that was made, a constraint that
  was discovered, a piece of state worth remembering ("this project froze merges on date X").
- **Skill** — a reusable *procedure*: a workflow, technique, or recipe worth documenting once and
  reusing on demand ("when doing Y, always check Z first, because of incident W"). A skill also
  declares a **scope**: `project` when it's tied to this project's own tooling and would be noise
  elsewhere, `global` when the technique is genuinely portable. The two live in separate directories
  and are separate namespaces — the same name existing in the other one is not a conflict. A skill
  file needs `name` and `description` in its frontmatter and nothing else; `when_to_use` and
  `dispatch_intent` are accepted if the draft offers them, never demanded.

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
  task.py              # DistillSession(RLMTask) — signature, output_model, instructions
  session.py           # run_distillation (ingest once, redact once, run once) + assemble()
  apply.py             # apply_plan — the human-gated writer, outside the RLM entirely
  redact.py            # host-side redaction, applied before any text reaches the model
  frontmatter.py       # nested-YAML frontmatter parsing (memory + skill shapes)
  tools/               # the five READ-ONLY planner tools
  adapters/
    base.py            # the read-only harness-adapter seam
    claude_code.py     # the one in-scope adapter
docs/
  DESIGN.md            # the full design doc this project is built against
tests/                 # fully offline: no live model, no Deno, no network
```

## Relationship to rlm-kit

`ctx-distillery` is a thin declaration on top of `rlm-kit`'s `RLMTask` — the retry/validation
loop, sandbox selection, budget caps, tracing, and dataset export are all inherited, not
reimplemented here. See rlm-kit's own README, "Building a consumer," for the pattern this project
follows.
