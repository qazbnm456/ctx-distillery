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
never a training signal. Scoring stays a downstream trainer's job, exactly as `rlm-kit`'s own
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
python -m ctx_distillery_eval score "traces/*.jsonl" transcript-1.txt # same entry, no console script
```

Scores every run found in the matched trace file(s) against the SAME given transcript(s) — see
`cli.py`'s module docstring for the stated scope of that convention (one transcript set per
invocation; a batch spanning different transcript sets needs one invocation per set).

Exit code is **1** when nothing scored — an unmatched glob, or a batch in which every row came back
unscored — so a CI gate keying on the exit code cannot read an all-`--` scorecard as a pass.

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

## Deferred: `run` + a real taskset

Every sibling eval member has a second subcommand — `run <taskset>`, which drives the rollout package
per task and then scores the fresh trace. This one deliberately has **only `score`**. Three concrete
blockers, none of them polish:

1. **There is no taskset concept here.** `taskset.py` is not one: `collect_tasks(glob)` enumerates
   `{run_id, trace_path}` from *traces*. Every sibling's `run` iterates a real `EvalTask` list with
   an id, a planner-visible input, and a judge-only `reference`. Building that means deciding what a
   ctx-distillery *task* is — and its planner-visible input would be a **project directory plus a
   transcript set**, which has no sibling analogue.
2. **`judge.build_prompt` has no `{reference}` slot at all.** Adding judge-only ground truth is a
   prompt change, and prompt changes are exactly what `PROMPT_VERSION` exists to make attributable.
3. **`run_distillation` returns an `AssembledPlan`, not artifacts.** The siblings return a
   `RunArtifacts` carrying `events`/`run_id`/`trace_path`, so scoring the fresh run is one call.
   Worse, the judge needs the **redacted** transcript text, which the driver ingests internally and
   returns nowhere — so an eval `run` would have to re-`ingest()` and re-`redact()`, and could then
   score against a *different* redaction than the run actually saw. The clean fix is a returned
   artifacts object, which is a change to the driver's public signature and belongs with the taskset
   design rather than bolted onto the eval.

None of this blocks the live judge, which is why it shipped first: ctx-distillery's judge takes
`transcript_texts` as its ground-truth analogue and needs no `reference`, so it is exercisable
**end-to-end on `score` alone** against traces produced by `ctx-distillery distill`.

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
