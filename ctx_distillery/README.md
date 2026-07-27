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
  nested-YAML frontmatter plus `MEMORY.md` as `kind="index"`, plus `*/SKILL.md` under each skills root
  as `kind="skill"` at `scope="global"`/`"project"`, and only enumerates a resolved path still
  contained by the root it came from (a symlink inside a store must not fold its outside target into
  the trusted snapshot — for the nested skill layout that means `<root>/<slug>/SKILL.md` specifically).
  `ClaudeCodeAdapter.for_project(project_dir)` discovers the real storage; see below.
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

## Storage auto-discovery (`ClaudeCodeAdapter.for_project`)

```python
adapter = ClaudeCodeAdapter.for_project("/path/to/project")          # real ~/.claude
adapter = ClaudeCodeAdapter.for_project(project, home=tmp_claude)    # hermetic, for tests
```

Derivations, all exported so `apply.py` uses the SAME ones (one derivation, two consumers — if they
drifted, the reader and the writer would disagree about a location):

| helper | result |
| --- | --- |
| `sanitize_project_dir(p)` | the absolute path with every `/` → `-` |
| `project_storage_dir(p, home=)` | `<claude_home>/projects/<sanitized>` |
| `memory_dir_for_project(p, home=)` | `<that>/memory` |
| `transcript_files(p, home=)` | every contained `<session-id>.jsonl`, sorted + resolved |
| `global_skills_root(home=)` | `<claude_home>/skills` |
| `project_skills_root(p)` | `<p>/.claude/skills` |

**The evidence is not uniform, and the code says so.** CONFIRMED by direct inspection: the
sanitization rule, one-JSONL-per-conversation, and the global skill layout (a DIRECTORY per skill).
INHERITED but not re-verified: the `memory/` sub-path — that convention predates this research and no
`memory/` directory existed on the machine it ran on. UNCONFIRMED: that Claude Code reads a
project-repo-relative `<project>/.claude/skills/` at all. That last one is a motivated hypothesis
(this repo's `.claude/rules/` IS read project-relative), targeted as the best available option for
project-scoped promotions — it is NOT evidence that a skill written there gets picked up, and the
empirical check (seed a test skill, start a fresh session, see if it's offered) has not been done.

`render_transcript_events` / `render_transcript_file` turn raw JSONL into the `list[str]` the pipeline
expects. Deliberately lossy, and the rules are pinned by tests rather than left implicit: filter to
`user`/`assistant` FIRST (no other event type carries `message` at all); `message.content` may be a
plain string OR a list of blocks; `text`/`thinking` verbatim; `tool_use` → `[used tool: X]`;
`tool_result` → `N chars` or `N blocks` depending on ITS OWN content's shape; anything unrecognized →
`[unrecognized content block: X]`, never dropped. `isSidechain` is filtered as a defensive no-op —
subagent messages are in separate `subagents/agent-<id>.jsonl` files, not inlined, so it filters
nothing today. `home=` exists so no test ever reads the real `~/.claude`.

Skill enumeration is opt-in on the explicit constructor (`global_skills_dir=` / `project_skills_dir=`)
so `apply.py`'s bare `ClaudeCodeAdapter(memory_dir)` re-scan stays machine-independent.

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
| `promote_to_memory` | writes the assembled draft to `slugify(frontmatter["name"]) + ".md"` inside `memory_dir`, created with `open(path, "x")` |
| `promote_to_skill` | writes it to `<skills root for key_fields["scope"]>/<slug>/SKILL.md` — a NESTED target under a DIFFERENT root, same exclusive create |
| `prune` | MOVES `key_fields["target_path"]` to `<memory_dir's parent>/_ctx_distillery_archive/<timestamp>-<name>` |
| `keep` | nothing — a no-op by construction |

Roots are PER KIND. `memory_dir` is positional as before; `global_skills_dir=` /
`project_skills_dir=` are keyword-only and OPTIONAL — leaving one unset refuses the skill promotions
that would need it (with a message saying so) rather than inventing a location. Derive them with
`global_skills_root()` / `project_skills_root(project_dir)` above.

The five things that make it safe, each one closing a gap an independent design review found:

1. **The collision/target authority is a fresh re-scan.** `apply_plan` calls
   `ClaudeCodeAdapter(memory_dir).list_targets()` itself, at apply time. A plan produced hours
   earlier has a snapshot that is stale by construction; it is never consulted here.
2. **Filename derivation is specified, and refuses rather than invents.** `slugify` lowercases,
   turns `[\s_]+` runs into a hyphen, and drops everything outside `[a-z0-9-]`. An empty result is a
   hard refusal — no fallback name is made up — and `MEMORY.md` is never a promotion target.
3. **Write-side containment mirrors the read side — with a SEPARATE check for the nested skill
   shape.** A memory file's computed path must satisfy `resolved.parent == memory_dir`, the identical
   check `list_targets()` applies when enumerating; a symlink in the store cannot redirect the write
   outside it. A skill is `<root>/<slug>/SKILL.md`, one level deeper and under a root that is never
   `memory_dir`, so the flat check would have refused every legitimate skill write — `_skill_target`
   is its own function instead: no path separator or traversal segment in the slug, `<root>/<slug>`
   resolving to a DIRECT child of the root, `SKILL.md` resolving inside that directory, and
   `<root>/<slug>` not already existing as something else (a non-directory is refused even with
   `overwrite`, which only ever replaces a drafted `SKILL.md`).
4. **`open(path, "x")` (O_CREAT|O_EXCL) is the actual enforcement.** The re-scan is a friendly early
   message; the exclusive create is what makes a collision impossible to lose to a race. Overwriting
   is opt-in per candidate (`overwrite_ids=`), never a global flag.
5. **`prune` archives, never deletes**, and the archive lives OUTSIDE `memory_dir` so neither
   `list_targets()`'s `*.md` glob nor `rlm_kit.skills.discover_skills`'s `*/SKILL.md` walk can
   re-surface an archived file as live. A `purge` that really deletes is future work.

On top of that, any candidate whose `problems` list is non-empty, whose `draft_ok is False`, or whose
promotion draft is empty is refused regardless of approval — `apply_plan` re-checks all three rather
than trusting the caller to have filtered correctly.

### The `key_fields` conventions (`prune`'s target, `promote_to_skill`'s scope)

`DistillCandidate.key_fields` is a free-form dict, so two things live there BY CONVENTION — each
taught to the planner in `task._INSTRUCTIONS`, enforced at apply time, and pinned by a test so the
prompt half and the apply half cannot drift apart:

- a `prune` candidate MUST set `key_fields["target_path"]` to the exact path of an existing artifact,
  copied verbatim from `list_memory_files()`. `apply.py` validates it by exact resolved-path match
  against the fresh re-scan — a missing, altered, or `kind="index"` target is refused, never guessed.
- a `promote_to_skill` candidate MUST set `key_fields["scope"]` to `"project"` (tied to this project's
  own tooling/conventions) or `"global"` (a portable technique), and pass the same value as
  `draft_skill_file`'s `scope` argument. It selects which root the skill is written into and which
  existing-name set counts as a collision — the two skill stores are independent namespaces. A missing
  or bogus scope is refused, never defaulted: the two scopes are different places on disk.

### The skill frontmatter shape lives in THREE places

`name` + `description` are the only REQUIRED frontmatter keys; `when_to_use` / `dispatch_intent` are
optional extras, passed through verbatim when present and never grounds for rejection (requiring them
would generalize from one author's skill pack, and Anthropic's documented convention requires
neither). Three encodings must move together or they drift: `make_skill_validator`,
`_spec_for_skill`'s model-facing prompt text, and `ClaudeCodeAdapter.schema_for("skill")`.

### Why it may write when nothing else in the package may

`tests/test_no_write_capability.py` scans every module here for mutation calls and `apply.py` is its
one exemption. What makes that safe is not the absence of a write call but its unreachability: no
module on the RLM path imports `apply`, and a test asserts it. Never import it from `task.py`,
`session.py`, a tool, or `__init__.py`.
