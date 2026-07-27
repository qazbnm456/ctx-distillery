# ctx_distillery (package guide)

The full design lives in `docs/DESIGN.md` and the hard invariants in `CLAUDE.md`; this file is the
map of what is actually in the package and how the pieces fit.

## The shape of one run

```
adapter.ingest()  ->  redact  ->  DistillSession (RLM)  ->  assemble()  ->  [a human reads it]  ->  apply_plan()
   read-only          host-side      judgement only        re-source from        approve some         the ONLY
                                                              the trace          candidates          writer
```

- **`adapters/`** — the harness seam (`ingest` / `schema_for` / `list_targets`), read-only by
  construction. `claude_code.py` is the one implemented adapter: it enumerates `memory/*.md` with
  nested-YAML frontmatter plus `MEMORY.md` as `kind="index"`, and only enumerates a resolved path
  whose parent is still `memory_dir` (a symlink inside the store must not fold its outside target
  into the trusted snapshot).
- **`redact.py`** — pattern-based host-side redaction, applied immediately after the single
  `ingest()` so the redacted list is the only text the model can reach.
- **`task.py`** — `DistillSession(RLMTask)`: the signature, the judgement-only `output_model`
  (`{action, artifact_id, key_fields}` per candidate), the instructions, and the five read-only
  tools wired from an immutable index snapshot. The `pyodide` interpreter pin is enforced in code.
- **`tools/`** — `list_memory_files` / `read_memory_file` (progressive disclosure over the store,
  allowlisted to exact snapshot paths), `read_transcript_chunk`, and the two drafting tools
  (`draft_memory_file` / `draft_skill_file`) that author text into the TRACE and never onto disk.
- **`session.py`** — `run_distillation` (ingest once, redact once, run once) and `assemble`, which
  re-sources each promotion's verbatim drafted bytes from its `tool_call` event by `artifact_id`,
  reporting an unbacked candidate as a problem rather than trusting the plan's own claim.
- **`apply.py`** — the human-gated writer. Everything above is inert; this is where disk changes.

## `apply.py` — the only module that writes

```python
from ctx_distillery.apply import apply_plan

outcomes = apply_plan(memory_dir, assembled_plan, approved_ids={0, 3})
```

`approved_ids` are LIST INDICES into `assembled_plan.candidates` — not `artifact_id`, because
`prune`/`keep` candidates carry `artifact_id=None`; the index is the one identifier every candidate
has. An index addressing no candidate raises `ValueError` before anything is written. There is no
"apply the whole plan" entry point on purpose.

Returned: one `ApplyOutcome(index, action, status, reason, path, source_path)` per candidate, with
`status` one of `applied` / `refused` / `skipped` (not approved) / `noop` (a `keep`).

Per action kind:

| action | what happens |
| --- | --- |
| `promote_to_memory` / `promote_to_skill` | writes the assembled draft to `slugify(frontmatter["name"]) + ".md"` inside `memory_dir`, created with `open(path, "x")` |
| `prune` | MOVES `key_fields["target_path"]` to `<memory_dir's parent>/_ctx_distillery_archive/<timestamp>-<name>` |
| `keep` | nothing — a no-op by construction |

The five things that make it safe, each one closing a gap an independent design review found:

1. **The collision/target authority is a fresh re-scan.** `apply_plan` calls
   `ClaudeCodeAdapter(memory_dir).list_targets()` itself, at apply time. A plan produced hours
   earlier has a snapshot that is stale by construction; it is never consulted here.
2. **Filename derivation is specified, and refuses rather than invents.** `slugify` lowercases,
   turns `[\s_]+` runs into a hyphen, and drops everything outside `[a-z0-9-]`. An empty result is a
   hard refusal — no fallback name is made up — and `MEMORY.md` is never a promotion target.
3. **Write-side containment mirrors the read side.** The computed path must satisfy
   `resolved.parent == memory_dir`, the identical check `list_targets()` applies when enumerating,
   before any write is attempted. A symlink in the store cannot redirect the write outside it.
4. **`open(path, "x")` (O_CREAT|O_EXCL) is the actual enforcement.** The re-scan is a friendly early
   message; the exclusive create is what makes a collision impossible to lose to a race. Overwriting
   is opt-in per candidate (`overwrite_ids=`), never a global flag.
5. **`prune` archives, never deletes**, and the archive lives OUTSIDE `memory_dir` so neither
   `list_targets()`'s `*.md` glob nor `rlm_kit.skills.discover_skills`'s `*/SKILL.md` walk can
   re-surface an archived file as live. A `purge` that really deletes is future work.

On top of that, any candidate whose `problems` list is non-empty, whose `draft_ok is False`, or whose
promotion draft is empty is refused regardless of approval — `apply_plan` re-checks all three rather
than trusting the caller to have filtered correctly.

### The `prune` target convention

`DistillCandidate.key_fields` is a free-form dict; a `prune` candidate MUST set
`key_fields["target_path"]` to the exact path of an existing artifact, copied verbatim from
`list_memory_files()`. `task._INSTRUCTIONS` asks the planner for exactly that, and `apply.py`
validates it by exact resolved-path match against the fresh re-scan — a missing, altered, or
`kind="index"` target is refused, never guessed at. The two halves are pinned by a test so they
cannot drift apart.

### Why it may write when nothing else in the package may

`tests/test_no_write_capability.py` scans every module here for mutation calls and `apply.py` is its
one exemption. What makes that safe is not the absence of a write call but its unreachability: no
module on the RLM path imports `apply`, and a test asserts it. Never import it from `task.py`,
`session.py`, a tool, or `__init__.py`.
