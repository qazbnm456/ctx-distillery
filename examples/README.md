# examples

## `demo-run.jsonl`

A real, complete run: one small synthetic transcript in, one `promote_to_memory` candidate out,
with the memory file it drafted. Read it without credentials, a model, or a network connection:

```bash
ctx-distillery show examples/demo-run.jsonl
ctx-distillery show examples/demo-run.jsonl --json
```

Installed from PyPI rather than a checkout? Fetch it on its own:

```bash
curl -sO https://raw.githubusercontent.com/qazbnm456/ctx-distillery/main/examples/demo-run.jsonl
ctx-distillery show demo-run.jsonl
```

**It is a genuine artifact, not a hand-written fixture.** The transcript is the checked-in
`eval/ctx_distillery_eval/demo/durable-fact.jsonl`, materialized into a throwaway `~/.claude` layout
under `/tmp` so no real path appears anywhere in the trace, and then distilled by a live run. The
`planner` / `drafter` model ids in its metadata are the ones that actually produced it.

Regenerate it the same way — from the repository root, with `CD_*` credentials and Deno available:

```bash
uv run --directory eval --package ctx-distillery-eval \
  python -c "from ctx_distillery_eval.taskset import demo_taskset; demo_taskset('/tmp/ctxd-demo')"

ctx-distillery distill /private/tmp/ctxd-demo/demo-durable-fact \
  --claude-home /private/tmp/ctxd-demo/claude-home \
  --trace-dir /tmp/ctxd-demo/traces --run-id demo-run
```

A live model is not deterministic, so a regenerated plan will differ in wording and may differ in
judgement. One thing to watch for: if you have already applied the promotion into the demo store, a
re-run correctly proposes `prune` instead, because the fact is no longer new. Start from a fresh
`demo_taskset` root to reproduce the promotion.
