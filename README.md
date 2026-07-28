# ctx-distillery

`ctx-distillery` reads AI coding-agent session transcripts — plus the persistent memory store
those sessions built up — and uses an RLM ([`rlm-kit`](https://github.com/qazbnm456/rlm-kit)) to
propose a distillation plan: what's safe to prune, what should be cross-referenced or merged
across sessions, and what durable knowledge is worth promoting into a standing memory file or a
reusable Skill. It is a judgement engine, nothing more.

**Status: the planner is wired and offline-tested; the apply step exists; storage is auto-discovered;
both are driveable from the command line.** The five read-only planning tools, the Claude Code
adapter, the assemble-on-read convention, the human-gated `apply_plan`, auto-discovery of Claude
Code's real on-disk storage (transcripts + both skill scopes), and the two console scripts are
implemented. Two sibling `uv` workspace members round it out: **`eval/`**
(`ctx-distillery-eval`) — a reward-free LLM-as-judge scoring the assembled plan against its
transcript(s), offline by default and pointable at a real model with `CDEVAL_MODEL` — and
**`studio/`** (`ctx-distillery-studio`) — a replay-only FastAPI + zero-build-vanilla-JS console
previewing each candidate's drafted text next to its plan entry, purely from a finished run's trace
file. Both reward-free/read-only by construction; see their own `eval/README.md`/`studio/README.md`.
Still missing: subagent-transcript distillation, an eval `run` subcommand over a real taskset
(deferred with three named blockers — see `eval/README.md`), and any harness other than Claude Code. See `CLAUDE.md` for the hard invariants this project is built against.

## Install and run

```bash
git clone https://github.com/qazbnm456/ctx-distillery && cd ctx-distillery
uv sync --extra cli          # the `cli` extra pulls the model client `distill` needs
cp .env.example .env         # then fill in CD_ROOT_LM / CD_API_KEY
set -a; . ./.env; set +a     # nothing auto-loads .env
brew install deno            # the sandbox a live run executes in
```

```bash
# 1. propose a plan for a project you have used Claude Code in (this writes only a trace file)
uv run ctx-distillery distill /path/to/your/project

# 2. read it again whenever you like — offline, no model, no credentials
uv run ctx-distillery show traces/<run-id>.jsonl

# 3. apply ONLY the candidates you name, by the index `show` printed. Without --confirm this is a
#    dry run that writes nothing; there is no flag that approves the whole plan.
uv run ctx-distillery-apply traces/<run-id>.jsonl --project /path/to/your/project --approve 0,3
uv run ctx-distillery-apply traces/<run-id>.jsonl --project /path/to/your/project --approve 0,3 --confirm

# 4. (optional) export finished runs as a reward-free SFT/RL dataset — offline, JSON on stdout
uv run ctx-distillery export "traces/*.jsonl" > dataset.json
```

Applying is a **separate binary** on purpose, not a third subcommand: nothing on the planner's side
of the codebase is allowed to import the one module that writes, and a test enforces it. Installing a
skill into `~/.claude/skills` (rather than into the project you named) needs one more explicit flag,
`--allow-skill-scope global`, because a global skill reaches every project you will ever open — and
shadows a project skill of the same name.

Offline commands need none of the setup above: `show`, `export`, `ctx-distillery-apply`, the eval
scorer, and the studio all work on a finished trace file alone.

`export` has no `--out` for the same reason `show` doesn't — nothing on the planner's side may open a
file for writing — so redirect it. The bundle is `{actions, drafting, orchestrator_tools, planner,
sft_turns, labels, metrics, rubric_signal}`; every ACTION record carries `reward: null` (SFT turns
carry no reward key at all), and `labels` counts
only what the trace structurally establishes (how many candidates, how many unbacked, whether the run
finalized). Nothing in it claims a judgement was *right* — there is no oracle for "was this the right
thing to prune", and inventing one would fabricate the signal. See `VENDOR.md`.

**Running on a Claude subscription.** Give `CD_ROOT_LM` / `CD_SUB_LM` the `claude-agent-sdk/<id>`
prefix and the planner runs on your own Claude Pro/Max plan instead of an API key
(`uv sync --extra subscription`, Claude Code CLI logged in, `ANTHROPIC_API_KEY` unset). The
**drafter** cannot ride it — it talks to an OpenAI-compatible endpoint directly — so set `CD_DRAFT_LM`
to a real model id; leaving it unset makes it inherit the sentinel and the run refuses to start rather
than 404ing mid-trajectory. See the subscription block in `.env.example`.

## Point it at a project

The CLI does this for you; in Python it is one line:

```python
from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter

adapter = ClaudeCodeAdapter.for_project("/path/to/your/project")
```

That's the whole setup. `for_project` finds Claude Code's real on-disk storage for that project —
every past conversation's transcript, the project's memory store, and the skills it can already see
— instead of making you assemble paths and text by hand. See the guide's
[Storage auto-discovery](https://github.com/qazbnm456/ctx-distillery/blob/main/ctx_distillery/README.md#storage-auto-discovery-claudecodeadapterfor_project)
for exactly what's derived and how confirmed each piece is.

Two things worth knowing, because they are honest limits rather than polish:

- **Transcripts are rendered lossily on purpose.** A real long conversation's raw log is
  multi-megabyte; feeding it back verbatim would defeat the point of distilling it. What survives is
  what was said and decided — message text and thinking verbatim, tool calls as short
  `[used tool: X]` labels, tool results as size labels. Subagent conversations are stored separately
  and aren't read yet.
- **A project skill can be silently shadowed by a global one of the same name.** Claude Code gives a
  personal/global skill precedence over a project skill sharing its name — confirmed by a direct
  empirical test, not assumed — so both `draft_skill_file` and `apply_plan` refuse a project-scope
  name a global skill already holds, rather than installing a skill that could never be reached.

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

From the shell this is `ctx-distillery-apply` (above). The Python API it wraps is the same shape,
and remains the way to point at a layout the CLI's `--project` derivation doesn't cover:

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

There is deliberately no "apply everything" call — for an irreversible operation, the safe path
should be the default one. `apply_plan` archives instead of deleting on a prune, re-scans the real
store at apply time rather than trusting the plan's own (possibly stale) snapshot, and refuses a
candidate the run itself flagged even if you approved it. Every candidate comes back with an
outcome, including the ones you didn't approve — the step that mutates disk should be the last
place to keep no record. See the guide's
[`apply.py` — the only module that writes](https://github.com/qazbnm456/ctx-distillery/blob/main/ctx_distillery/README.md#applypy--the-only-module-that-writes)
for the exact write-safety guarantees (exclusive create, per-kind containment checks, per-scope
skill roots).

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
schemas — never one undifferentiated "promote" action. The drafted text is never trusted from the
plan's own claim about what it wrote; see the guide's
[The shape of one run](https://github.com/qazbnm456/ctx-distillery/blob/main/ctx_distillery/README.md#the-shape-of-one-run)
for the assemble-on-read mechanics.

## Harness-agnostic by design — Claude Code today

The planning core is meant to work over any AI coding agent's transcript + memory format, not
just one. That's bridged through a thin **adapter seam** — `ingest()` / `schema_for(kind)` /
`list_targets()` — the same base/wrap split rlm-kit uses for tool extension, applied one layer up:
the harness is the "provider," not a model or API. See `ctx_distillery/adapters/base.py` for the
interface.

Right now, **only a Claude Code adapter is in scope to build**, because it's the only platform
whose real on-disk persistence format (the memory frontmatter schema, the `MEMORY.md` index,
scratchpad conventions) has actually been inspected and verified. Codex, Hermes, OpenClaw, and
OpenCode are named as **future targets** in `CLAUDE.md`'s "Harness scope" — deliberately not
designed yet. Their real formats haven't been looked at from here, and guessing one would be
speculation dressed as design, not genuine multi-harness support. When one of them is actually in
scope, the honest next step is the same one taken for Claude Code: read its real current format
first, then write the adapter.

## Documentation — the guide

The deep reference lives in the package doc, [`ctx_distillery/README.md`](ctx_distillery/README.md)
— the module map, the storage-discovery derivations and their confirmation status, and the exact
write-safety guarantees `apply_plan` enforces:

- [The shape of one run](https://github.com/qazbnm456/ctx-distillery/blob/main/ctx_distillery/README.md#the-shape-of-one-run) — what each module owns, end to end
- [Storage auto-discovery](https://github.com/qazbnm456/ctx-distillery/blob/main/ctx_distillery/README.md#storage-auto-discovery-claudecodeadapterfor_project) — the derivations, and which are confirmed vs. inherited
- [`apply.py` — the only module that writes](https://github.com/qazbnm456/ctx-distillery/blob/main/ctx_distillery/README.md#applypy--the-only-module-that-writes) — collision handling, containment checks, the skill-shadowing refusal
- [Why it may write when nothing else in the package may](https://github.com/qazbnm456/ctx-distillery/blob/main/ctx_distillery/README.md#why-it-may-write-when-nothing-else-in-the-package-may) — the unreachability guarantee, test-enforced
- [Layout](https://github.com/qazbnm456/ctx-distillery/blob/main/ctx_distillery/README.md#layout) — the module-by-module tree

For the ATLAS rubric facts, the eval member, and the Studio (Phase 1/2 of the rubric/eval/studio
initiative), see [`eval/README.md`](eval/README.md) and [`studio/README.md`](studio/README.md).
For the hard invariants and the rationale behind each one, see [`CLAUDE.md`](CLAUDE.md).

## Relationship to rlm-kit

`ctx-distillery` is a thin declaration on top of `rlm-kit`'s `RLMTask` — the retry/validation
loop, sandbox selection, budget caps, tracing, and dataset export are all inherited, not
reimplemented here. See rlm-kit's own README, "Building a consumer," for the pattern this project
follows.
