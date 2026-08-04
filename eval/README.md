# ctx-distillery-eval

Offline, reward-free evaluation for [`ctx-distillery`](../README.md) — a separate uv workspace
member, not a subpackage of `ctx_distillery` itself.

## What this scores

`ctx_distillery`'s `DistillSession` proposes a plan (prune / promote_to_memory / promote_to_skill /
keep) over one or more session transcripts + the current memory/skill index. This package reads that
plan back — re-assembled from the run's trace via `ctx_distillery.session.assemble`, exactly the way
the rollout side's own `ctx_distillery.rubric` does — together with the transcript(s) it was drawn
from, and asks an LLM-as-judge four artifact-framed questions, one per ATLAS category:

| category | question |
|---|---|
| TF | does the plan capture what's actually worth keeping from the supplied transcript(s)? |
| TA | did the plan's judgements follow a sensible evidentiary approach? |
| TG | is each candidate's rationale plausibly supported by the actual transcript content? |
| PA | are the drafted memory/skill files clear, well-scoped, and correctly targeted? |

Each answer is a 0-10 score. This package computes **per-category means only** — never a composite,
never a training signal. Scoring stays a downstream trainer's job, exactly as `rlm-harness`'s own
"trajectories, never reward" invariant states for the rollout side.

A run the judge **could not** score (endpoint failure, off-schema reply, tripped circuit breaker) is
reported as `unscored` with its reason and excluded from the means — **never silently a 0**. A 0 is a
claim the judge never made, and it would drag an aggregate down in a way that reads as a bad plan
rather than as broken infrastructure. Every report also pins `n` / `n_unscored` / `judge_model` /
`prompt_version`, so a number is attributable to the prompt and the model that produced it.

## Why the transcript path is a required input, not optional

A finished trace does not carry the raw transcript verbatim — it's redacted host-side and passed as
a task *input*, never itself recorded as a `tool_call`. The tool_calls that *read* it
(`read_transcript_chunk`, `read_memory_file`) record only offset/length/path/chars metadata by
design — never the body — so scoring against those would be scoring an *empty* substitute, not a
degraded one. `score`/`judge` therefore take the transcript text(s) as a mandatory second input
alongside the trace path; there is no trace-only fallback.

## Boundary

- A ONE-WAY reader of `ctx_distillery`'s PUBLIC surface (`session.assemble`, `task.DistillPlan`) —
  `ctx_distillery` never imports this package back (`tests/test_boundary.py`, both here and in the
  root package).
- Static read only, no tool execution: matches `ctx_distillery`'s own read-only stance
  (`tests/test_no_write_capability.py`).
- Rubric-free judge prompt: the judge never sees `ctx_distillery.rubric`'s deterministic facts —
  this is a genuinely independent, artifact-level read.
- Default path uses a fully offline, deterministic `StubJudge` (fixed scores, no model call) — the
  tested default and the whole of CI. The **live** judge is opt-in behind the `judge` extra
  (`pip install -e './eval[judge]'`) plus a `CDEVAL_MODEL` in the environment; see below.

## Usage

```sh
ctx-distillery-eval score "traces/*.jsonl" transcript-1.txt transcript-2.txt
ctx-distillery-eval score "traces/*.jsonl" transcript-1.txt --stub    # force offline, ignore CDEVAL_*
ctx-distillery-eval score "traces/*.jsonl" transcript-1.txt --taskset taskset.json
python -m ctx_distillery_eval score "traces/*.jsonl" transcript-1.txt # same entry, no console script

ctx-distillery-eval run demo --out ./output/eval --stub    # drive the built-in demo, then score
                                                           #   (--stub forces only the JUDGE offline;
                                                           #    the RUN still needs CD_* + a sandbox)
ctx-distillery-eval run taskset.json --out ./output/eval   # ... a real taskset (needs CD_* + a sandbox)
```

`score` scores every run found in the matched trace file(s) against the SAME given transcript(s) —
see `cli.py`'s module docstring for the stated scope of that convention (one transcript set per
invocation; a batch spanning different transcript sets needs one invocation per set). `run` has no
such ambiguity: it drives each task itself, so every row is judged against the redacted transcripts
*that* run actually saw, straight off `ctx_distillery.session.DistillArtifacts`.

Exit code is **1** when nothing scored — an unmatched glob, a batch in which every row came back
unscored, or (for `run`) a taskset in which every task failed to drive — so a CI gate keying on the
exit code cannot read an all-`--` scorecard as a pass.

## The taskset

A taskset is a JSON list of `{id, project?, reference?}` objects (or `{"tasks": [...]}`), paired to
runs by the family's `run_id == task.id` convention. See `taskset.example.json`.

| field | who reads it |
|---|---|
| `id` | both — the pairing key, and `run`'s `run_id` |
| `project` | `run` only — `{project_dir, claude_home}`, handed to `ClaudeCodeAdapter.for_project` |
| `reference` | the **judge only** — the plan a human expects; the planner never sees it |

`project` is optional, so a `{id, reference}`-only taskset is legal and useful with `score --taskset`
(which reads finished traces and needs no project at all). `run` refuses a task without one
**loudly** — as an `unscored` row naming the missing field, not as an aborted batch. `claude_home` is
its own overridable field on purpose: nothing here may read the machine's real `~/.claude`
(`CLAUDE.md` invariant 6).

`--taskset` is an **option** on `score`, not a third positional the way every sibling has it: the two
existing positionals are the shipped contract and did not move, and a taskset adds nothing to `score`
but judge-only `reference` text. A run the taskset does not describe is scored *without* a reference
rather than skipped — scoring traces a taskset does not cover is the normal case here. (A taskset
FILE that cannot be parsed is still a hard failure: that is a typo to fix, not a degrade.)

### `demo`

`run demo` uses a built-in two-task set covering both of a distillation planner's real failure modes:
one session full of durable project conventions that **should** be promoted, and one one-off
debugging exchange that resolved itself and should **not** be over-promoted.

It is the only `demo_taskset` in the family that **materializes** rather than returning a constant,
and that is forced rather than chosen: Claude Code stores a project's transcripts under
`<claude_home>/projects/<sanitize(absolute project path)>/`, so the directory name depends on where
the checkout lives and cannot be checked in. `demo_taskset(root)` therefore builds the *layout* at
call time under a caller-supplied root — `run` passes `--out/demo`, tests pass `tmp_path`, and nothing is
ever created outside a directory the caller named. The transcript **content** stays checked in, as
`ctx_distillery_eval/demo/*.jsonl`, so the demo taskset is still reviewable data.

### What `run` deliberately does not copy from the siblings

1. **No `os.remove` of a stale trace** — that call is forbidden in this project. `TraceRecorder`
   appends, so the trace *filename* is unique per invocation (`<slug(task.id)>-<UTC stamp>.jsonl`) while
   `run_id` stays `task.id`. There is no `--force` that deletes.
2. **Everything lands under `--out`** (traces, and any materialized demo taskset).
3. **A failing task is an `unscored` row, not an aborted batch** — a missing `project`, a missing
   `CD_ROOT_LM`, or a planner that explodes is reported per-row and the rest of the taskset runs.

`--stub` forces the offline **judge** only; `run` still drives a real distillation, which is what
needs `CD_*` credentials and a sandbox.

## Judge environment (`CDEVAL_*`)

The judge is **live iff `CDEVAL_MODEL` is set and `--stub` was not passed**. With no `CDEVAL_MODEL`
the deterministic stub runs instead: fully offline, zero credentials, zero network. The prefix
follows the family convention (`TSEVAL_`/`CREVAL_`/`DSEVAL_`) and deliberately does **not** share the
root package's `CD_*` surface — the judge must be pointable at a different model, endpoint and key
from the run it is scoring.

```sh
CDEVAL_MODEL=            # judge model id; empty = use the offline stub judge
CDEVAL_BASE_URL=         # any OpenAI-compatible base URL (empty = the openai default)
CDEVAL_API_KEY=          # API key for that endpoint
CDEVAL_TIMEOUT=60        # per-call HARD timeout, seconds
```

`openai` is imported lazily inside the chat closure, so nothing needs it installed unless you
actually go live. One judge is built per invocation, not per run: the circuit breaker lives in that
closure, so a systematically off-schema judge stops burning calls after a few declines instead of
paying for one per trace in the glob.

## How the three former blockers were cleared

`run` was deliberately absent for a while, behind three concrete blockers. All three are now closed,
and how matters more than that they are:

1. **There was no taskset concept.** There is now: `taskset.EvalTask` / `load_taskset` /
   `demo_taskset`, above. The genuinely unbudgeted part — that a ctx-distillery task's
   planner-visible input is a *project directory* whose storage path is machine-dependent — is why
   `demo_taskset` materializes instead of being a static constant.
2. **`judge.build_prompt` had no `{reference}` slot.** It has one now, as a third positional
   argument, and `PROMPT_VERSION` bumped to `atlas-ctxd-eval-v2` for exactly the reason the constant
   exists. The section renders **only when there is a reference**, so a `score` run without a taskset
   produced the byte-identical v1 prompt (the divergence from the siblings' unconditional
   `"(no reference provided; …)"` fallback is argued in `judge.py`).

   `PROMPT_VERSION` is **`atlas-ctxd-eval-v3`** as of the subagent-distillation change, which added
   per-excerpt and total CHARACTER CAPS to prompt assembly. So the byte-identity above now holds
   only below those caps — which is the honest statement, and why the bump happened: a capped prompt
   is a different prompt, and scores either side of it are not comparable. `judge.py`'s own
   docstring carries the same qualification.
3. **`run_distillation` returned an `AssembledPlan`, not artifacts.** The fix was ADDITIVE:
   `ctx_distillery.session.run_distillation_artifacts` returns a `DistillArtifacts` carrying the
   plan plus `events` / `run_id` / `trace_path` / the **redacted** transcripts / the memory index,
   and `run_distillation` is now a one-line wrapper over it with its signature and return type
   unchanged. That last field is the whole point: re-`ingest()`ing and re-`redact()`ing would score
   against a *different* redaction than the run saw, and the trace records only offset/length
   metadata for a transcript, never the body — so a trace-sourced substitute would be *empty*, not
   merely lossier.

## Install (workspace member)

From the repo root (a `uv` workspace member — see the root `pyproject.toml`'s
`[tool.uv.workspace]`):

```sh
uv sync
uv run --directory eval --package ctx-distillery-eval --extra dev pytest
```

(`--package` alone only selects which workspace member's *environment* to use — it does not
change pytest's cwd or which `pyproject.toml`'s `testpaths` gets resolved, so it silently runs the
root package's suite instead. `--directory eval` makes pytest resolve `eval/pyproject.toml`'s own
`testpaths`; see `.github/workflows/ci.yml`'s `eval-test` job, fixed the same way.)

Or, for a plain-pip environment where the workspace `[tool.uv.sources]` reference can't resolve
(e.g. a bare venv, not a `uv` project): install `ctx-distillery` first, then this package in
editable mode, e.g. `pip install -e . -e ./eval` from the repo root.
