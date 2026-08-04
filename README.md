# ctx-distillery

**Your coding agent's memory rots. Distil it.**

Every project you work on with an AI coding agent accumulates two things: a pile of past
conversations too large for anyone to read, and a memory store that grows, drifts, and quietly starts
contradicting the code. ctx-distillery reads both and proposes what to do about them — what is safe
to prune, what is worth promoting into a durable memory file, and what has become a reusable Skill.

It proposes. It never writes. Applying anything is a separate command you run, per candidate, after
reading the drafted text.

```bash
uv tool install "ctx-distillery[cli]"

cd ~/code/your-project
ctx-distillery distill                       # proposes a plan; writes only a trace file
ctx-distillery show traces/<run-id>.jsonl    # read it again any time — offline, free
```

On macOS add `--with "litellm<1.95"` to that install: a transitive dependency ships no macOS wheel at
1.95+ and its source build needs Rust. See [Honest limits](#honest-limits).

Prefer to drive it from your agent instead of the shell? It ships as an Agent Skill:

```bash
npx skills add qazbnm456/ctx-distillery
```

Want to see what a plan looks like before spending anything? [`examples/demo-run.jsonl`](examples/)
is a real finished run — read it offline, with no credentials and no model:

```bash
curl -sO https://raw.githubusercontent.com/qazbnm456/ctx-distillery/main/examples/demo-run.jsonl
ctx-distillery show demo-run.jsonl
```

## What it proposes

Four judgements, one per candidate, each backed by the transcripts it was drawn from:

| Action | Meaning |
|---|---|
| `keep` | Still true, still earning its place. |
| `prune` | Superseded, contradicted, or never mattered. Applying it **archives** the file — nothing is deleted. |
| `promote_to_memory` | A *fact* worth remembering: a decision that was made, a constraint that was discovered. |
| `promote_to_skill` | A reusable *procedure*: a workflow worth documenting once and reusing on demand, scoped to this project or to everything you open. |

A promotion arrives with its file already drafted — real frontmatter, real body, ready to read before
you decide. Skills can carry supplementary `references/` and `scripts/` files, drafted the same way.

## It cannot touch your files, and that is structural

The operation this tool reasons about — pruning your own history — is irreversible if it gets it
wrong. So the guarantee is not a promise to behave:

- **The model has no write capability at all.** It runs inside a sandboxed interpreter with no host
  filesystem access, wired to six read-only tools. There is no code path to a mutation, and a test
  scans every module in the package to keep it that way.
- **Applying is a different binary.** `ctx-distillery-apply` is the one module that writes, and
  nothing on the planner's side of the codebase is allowed to import it — also test-enforced.
- **Approval is per candidate, by index, and there is no flag that approves everything.** Without
  `--confirm` the apply step is a dry run. A prune archives; a promotion refuses to overwrite an
  existing file unless you name that index again.

```bash
ctx-distillery-apply traces/<run-id>.jsonl --project . --approve 0,3            # dry run
ctx-distillery-apply traces/<run-id>.jsonl --project . --approve 0,3 --confirm  # writes
```

## Secrets never reach the model

Your transcripts are your own history, so they contain your own credentials. Redaction runs
host-side, on every transcript, before a single character becomes model context — never as a
judgement handed to the model. Three tiers, in order:

1. **7 hand-written patterns** covering shapes a vendor corpus structurally cannot: an
   `Authorization:` header value (a lookbehind, which RE2 cannot express), a `key = value` assignment
   where only the value is replaced, and — measured on a real 426 KB transcript — a private-proxy API
   key that no vendor rule matched.
2. **120 rules ported from [gitleaks](https://github.com/gitleaks/gitleaks)**, mechanically, and
   graded against gitleaks' own true/false-positive corpus.
3. **Your own rules**, via `CD_REDACTIONS`. Additive only: there is no key that disables a built-in
   tier. Every rule must redact its own sample at load, and is refused if its regex can be made to
   backtrack catastrophically.

Matches become labelled placeholders (`[REDACTED:github_token]`), so a plan can still say a
credential appeared without carrying it. No network call is involved at any point. It is best-effort
pattern matching, not a completeness claim.

## Watch a run, and train on it

Two workspace members turn a finished run into something more than a plan.

**The studio console** ([`studio/`](studio/README.md)) replays a run from its trace file: every
iteration, every tool call, and each candidate's drafted text beside its plan entry. It is how you
see *why* something was proposed rather than only *what*. Replay is the default; a live-drive
endpoint exists but stays unreachable unless you opt in with `CTXD_LIVE_PROJECTS`.

```bash
uv run --package ctx-distillery-studio ctx-distillery-studio
```

**Trajectories for training.** Every run records a full trace, and `export` turns a directory of them
into a reward-free dataset: the planner's actions, the drafting calls, and structural labels read
back from the assembled plan. The intended use is fine-tuning a smaller model for the planner role
(`CD_ROOT_LM`) on your own history, so the expensive tier gets cheaper the longer you run it. Nothing
in the bundle claims a judgement was *correct* — there is no oracle for "was this the right thing to
prune", and inventing one would fabricate the signal. Scoring lives outside; what ships here is the
rollout source.

```bash
ctx-distillery export "traces/*.jsonl" > dataset.json
```

**A reward-free scorecard.** The [`eval/`](eval/README.md) member scores a recorded run's plan with an
independent LLM judge against the transcripts it was drawn from, means only, no composite. It reads
the trace one way and never feeds training.

## Models, and running on a Claude subscription

Two roles, set by environment: `CD_ROOT_LM` (the planner) and `CD_DRAFT_LM` (writes the memory/skill
bodies). Point them at any OpenAI-compatible endpoint; see [`.env.example`](.env.example).

The planner can run on a **Claude Pro/Max subscription** instead of an API key — give `CD_ROOT_LM` a
`claude-agent-sdk/<model>` value with the `subscription` extra installed. The drafter cannot follow
it, so `CD_DRAFT_LM` still needs its own endpoint; leaving it unset makes the run refuse to start
rather than fail halfway through.

## Honest limits

**A rendered transcript is mostly your own words.** Tool calls collapse to `[used tool: X]` labels by
design, and assistant *thinking* survives not at all — not because this renderer drops it, but
because Claude Code stores the block with an empty body (measured: 2,384 thinking blocks across 60
session files, none with content). If a plan misses something the assistant worked out silently,
this is usually why.

**One run covers one project.** The working directory's storage, not every project at once.

**Claude Code and Codex are ingested; only Claude Code is written.** The Codex adapter is read-only,
so a Codex-sourced plan can be reviewed but not applied into Codex's own store — stated as a scope
boundary, and enforced: the apply step refuses every write for a run it cannot correctly interpret.
Hermes and OpenClaw are named future targets whose on-disk formats nobody has inspected; guessing one
would be speculation dressed as design.

**A live run costs money and needs a sandbox** (`brew install deno`). `show`, `export`, the apply
step, the studio and the eval scorer are all fully offline and need neither.

**The macOS install caveat is real and not ours.** `litellm` arrives transitively through `dspy` and
from 1.95 ships wheels for manylinux and Windows only; on macOS it builds from an sdist that needs a
Rust toolchain and fails inside `maturin`, naming nothing you recognise. `--with "litellm<1.95"`
avoids it. A `git clone && uv sync` checkout is unaffected — the lockfile pins a buildable version.

**Applying archives, and nothing purges.** A pruned file moves to `_ctx_distillery_archive/`. Emptying
that for real is a separate operation that does not exist yet, deliberately: still recoverable beats
irreversible, even at the human-approved step.

## Development

```bash
make check          # everything CI runs: lint + the root suite + both members + the frontend contracts
```

Fully offline — no live model, no network, no Deno. The invariants this project is built against, and
the reasoning behind each, are in [`CLAUDE.md`](CLAUDE.md); the module-by-module reference is
[`ctx_distillery/README.md`](ctx_distillery/README.md).

RLM harness: [`rlm-harness`](https://github.com/qazbnm456/rlm-harness).
