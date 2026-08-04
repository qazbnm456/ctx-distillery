---
name: ctx-distillery-plan
description: Propose a distillation plan over a project's Claude Code history — what is safe to prune, and what durable knowledge is worth promoting into a memory file or a reusable Skill. Reads transcripts and the memory store, proposes judgements, and writes nothing. Use when asked to distil, review, prune, or clean up a project's accumulated agent memory, or to re-read a finished run's plan.
when_to_use: The user asks to distil or clean up their Claude Code memory/history, to find what is worth promoting into a memory file or Skill, to review a ctx-distillery run, or to read a trace file produced by a previous run.
---

# ctx-distillery — propose a distillation plan

`ctx-distillery` reads a project's past Claude Code conversations plus its persistent memory
store, and proposes a plan: `keep` / `prune` / `promote_to_memory` / `promote_to_skill`, one
judgement per candidate. **It proposes. It never applies.** Applying is a separate binary a human
runs by hand, and nothing in this skill may run it — see "Never apply" below.

**Before running `distill`, tell the operator what it does with their data.** `distill` reads
*every* stored conversation for the named project, redacts secrets host-side in three tiers, and
then sends the redacted text to whatever model endpoint `CD_BASE_URL`/`CD_ROOT_LM` name. It bills
their account. Redaction is best-effort pattern matching, not a completeness guarantee. If the
operator has not explicitly asked for a live run, prefer `show` (step 2) — it is offline, free, and
needs no credentials.

## Step 0 — resolve the binary

```
command -v ctx-distillery
```

If it is missing, ask the operator to install it. The `[cli]` extra is required for `distill`; a
bare install omits the model client and fails *mid-run*, after the trace file already exists:

```
uv tool install "ctx-distillery[cli] @ git+https://github.com/qazbnm456/ctx-distillery"
```

**On macOS that command currently fails**, and the error is a Rust/`maturin` build failure that
says nothing about this project. Cause: `litellm` (pulled in transitively by `dspy`) ships no macOS
wheel at 1.95+, so it is built from source. Add a constraint:

```
uv tool install --with "litellm<1.95" "ctx-distillery[cli] @ git+https://github.com/qazbnm456/ctx-distillery"
```

Inside a checkout of ctx-distillery itself, `uv run ctx-distillery ...` also works and needs neither
workaround — its lockfile already pins a buildable version.

Then confirm the version matches what this skill describes:

```
ctx-distillery --version
```

This skill was written against **0.1.0**. If the installed version is older, treat every flag below
as unverified and check `ctx-distillery --help` before using it.

## Step 1 — a live run (only when explicitly asked)

Needs credentials (`CD_ROOT_LM`, `CD_API_KEY`, and `CD_DRAFT_LM` if the planner rides a Claude
subscription) and a Deno sandbox (`deno --version`; `brew install deno`). Check both before
running, and say which one is missing rather than letting the run fail partway.

```
ctx-distillery distill /path/to/project
```

Add `--include-subagents` only if asked: it renumbers every transcript index and ships
substantially more text to the model. Omit the path to distil the current directory. The run writes
exactly one file, a trace at `./traces/<run-id>.jsonl` (or `$CTXD_TRACES_DIR`), and nothing else.

## Step 2 — read the plan

This is the default path, and the whole of it is offline:

```
ctx-distillery show traces/<run-id>.jsonl
```

Add `--json` for the machine-readable form. Then walk the operator through it. The rendering is one
block per candidate:

```
[0] action=promote_to_skill artifact_id='a1'
    key_fields={'scope': 'project'}
    draft (ok=True):
---
name: ...
```

The drafted body is emitted flush-left, under an indented header line — so the `---` starting its
frontmatter sits at column 0 and can look like a section break. It is the draft.

The leading `[0]` is the **list index**, and it is exactly what the apply step takes. Read
`reference.md` for what each field means, which candidates are already disqualified, and the two
promotion targets (a *fact* belongs in memory; a reusable *procedure* belongs in a skill).

Explain candidates in the operator's own terms — what this would add or remove, and why the run
proposed it. Say plainly when a draft looks wrong; the plan is a proposal, not an authority.

## Step 3 — hand the decision over, and stop

**Choosing which candidates to apply is the operator's decision, not yours.** Do not pick indices
for them, do not suggest "apply all", and do not run the apply binary. Print the command with the
indices *they* named, and stop there:

```
ctx-distillery-apply traces/<run-id>.jsonl --project /path/to/project --approve 0,3
```

Without `--confirm` that is a dry run that writes nothing — which is the right thing to read first.
Adding `--confirm` is what writes. Installing a skill into `~/.claude/skills` additionally needs
`--allow-skill-scope global`; the default is project-only, because a global skill reaches every
project the operator will ever open and shadows a project skill of the same name. Mention that flag
only if they ask for a global install.

## Never apply

Do not run `ctx-distillery-apply`, in any spelling, for any reason — not with `--confirm`, and not
as a "harmless" dry run. It is the one component in this project that writes to disk, and it is
deliberately a separate binary so that a human decides, per candidate, after reading the drafted
text. Print the command and let the operator run it. If they ask you to run it for them, say that
this skill does not do that and show them the command instead.

## Other commands

```
ctx-distillery export "traces/*.jsonl"
```

Prints a reward-free SFT/RL dataset as JSON on stdout — offline. Quote the glob. There is no
`--out` flag anywhere in this CLI (nothing on the planner's side may open a file for writing);
redirect with `>` if the operator wants a file.

## Additional resources

- [reference.md](reference.md) — the plan's output format field by field, which candidates the
  apply step will refuse, the environment variables, and what the transcript rendering drops.
