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
  tested default. A real judge is opt-in behind the `judge` extra (`pip install -e
  './eval[judge]'`) and is not wired up in this pass.

## Usage

```sh
ctx-distillery-eval score "traces/*.jsonl" transcript-1.txt transcript-2.txt
```

Scores every run found in the matched trace file(s) against the SAME given transcript(s) — see
`cli.py`'s module docstring for the stated scope of that convention (one transcript set per
invocation; a batch spanning different transcript sets needs one invocation per set).

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
