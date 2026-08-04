---
name: ctx-distillery-plan
description: Distil THE CURRENT PROJECT's Claude Code TRANSCRIPT history — dozens to hundreds of past conversations, far more than fits in context — into a reviewable plan of what durable knowledge is worth promoting into a memory file or a reusable Skill, and which existing memories are safe to prune. One run covers one project, the working directory, and there is no mode that reads every project at once. Reads transcripts plus the memory store, proposes judgements, and writes nothing. Needs either a finished run's trace file, or credentials and a Deno sandbox for a live run that bills the operator. NOT for auditing a handful of memory files that fit in context — read those directly instead, which is faster, free, and lets you verify each one against the current code.
when_to_use: The user wants durable knowledge mined out of a LARGE body of past conversations in the project they are working in ("what did I work out across all my sessions here", "turn my history with this project into something reusable"), or wants to read/review a ctx-distillery trace file. Do NOT invoke it merely because the user said "memory" or "clean up" — if the target is a few files you can open, open them.
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
uv tool install "ctx-distillery[cli]"
```

**On macOS that command currently fails**, and the error is a Rust/`maturin` build failure that
says nothing about this project. Cause: `litellm` (pulled in transitively by `dspy`) ships no macOS
wheel at 1.95+, so it is built from source. Add a constraint:

```
uv tool install --with "litellm<1.95" "ctx-distillery[cli]"
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

Needs a Deno sandbox (`deno --version`; `brew install deno`) and model credentials. Check both
before running, and say which one is missing rather than letting the run fail partway.

**Check credentials by PRESENCE in the environment. Never open `.env`, `.envrc`, or any other
credential file, and never print a key's value.** What you need to know is whether a variable is
set, which the environment answers without the secret ever entering this conversation:

```
printenv CD_ROOT_LM CD_DRAFT_LM
```

That prints the model ids, which are safe and are what you actually need; it says nothing about the
keys' values. For the keys, test only that they are non-empty — `[ -n "$CD_API_KEY" ] && echo set`.
If the variables are unset, say so and let the operator load their own environment (`set -a;
. ./.env; set +a`) rather than reading the file yourself. A key you read lands in the transcript,
and this project's whole purpose is feeding transcripts to a model later.

There are two seats, and they are configured separately:

* the **planner** (`CD_ROOT_LM`) — the long-running one, and where nearly all the tokens go;
* the **drafter** (`CD_DRAFT_LM`) — writes the memory/skill file bodies.

**Offer the subscription route when it applies.** Two conditions, both answerable without opening
anything:

```
command -v claude && printenv ANTHROPIC_API_KEY
```

If `claude` is present and `ANTHROPIC_API_KEY` prints nothing, the operator can run the *planner* on
their own Claude Pro/Max subscription instead of an API key, by setting
`CD_ROOT_LM=claude-agent-sdk/<model-id>`. **Ask them first, and say plainly that it consumes their
subscription usage** — do not switch them onto it silently, and do not treat a Claude Code session
as consent.

Two things to state when you offer it, both of which will otherwise bite:

1. **It does not remove the need for an API key.** The drafter cannot ride the subscription — it
   talks to an OpenAI-compatible endpoint directly — so `CD_DRAFT_LM` must name a real model and
   have a key. Leaving it unset makes the drafter inherit the `claude-agent-sdk/` sentinel and the
   run refuses to start. That refusal is deliberate and it is the correct behaviour; do not work
   around it. The saving is most of the *cost*, not the *setup*.
2. **It needs the `subscription` extra**, so the install becomes
   `uv tool install --with "litellm<1.95" "ctx-distillery[cli,subscription]"`,
   and the Claude Code CLI has to be logged in.

Driving the Agent SDK from inside a Claude Code session is not something this project has verified.
If it fails, say so and fall back to an API key for the planner rather than debugging it in place.

```
ctx-distillery distill
```

**One run covers one project, and that project is the current working directory.** There is no
mode that reads every project at once — a run resolves exactly one storage directory from the
project path. Pass a path only if the operator explicitly names a *different* project than the one
they are working in, which is unusual and worth confirming before you do it.

Add `--include-subagents` only if asked: it renumbers every transcript index and ships
substantially more text to the model. The run writes exactly one file, a trace at
`./traces/<run-id>.jsonl` (or `$CTXD_TRACES_DIR`), and nothing else.

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
ctx-distillery-apply traces/<run-id>.jsonl --project . --approve 0,3
```

`--project` is required and names the store that would be written — the current project unless the
operator distilled a different one, in which case it must be that same one.

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
