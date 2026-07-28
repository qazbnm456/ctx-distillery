# ctx_distillery (package guide)

The hard invariants live in `CLAUDE.md`; this file is the map of what is actually in the package
and how the pieces fit.

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
  `ingest()` so the redacted list is the only text the model can reach. Three tiers, in order: 7
  hand-written patterns (the DOTALL private-key block, the lookbehind-anchored `Authorization:`
  header, the value-only `key = …` assignment, and private-proxy API keys — none of which a vendor
  corpus can express), then 120 anchored rules ported from gitleaks into `patterns/`, then the
  operator's own rules from `CD_REDACTIONS` (empty unless set). See `VENDOR.md` for the pin and the
  refresh command, `CLAUDE.md` invariant 3 for the reasoning, and "Tier three" below for the schema.
- **`task.py`** — `DistillSession(RLMTask)`: the signature, the judgement-only `output_model`
  (`{action, artifact_id, key_fields}` per candidate), the instructions, and the five read-only
  tools wired from an immutable index snapshot. The `pyodide` interpreter pin is enforced in code.
- **`tools/`** — `list_memory_files` / `read_memory_file` (progressive disclosure over the store,
  allowlisted to exact snapshot paths), `read_transcript_chunk`, and the two drafting tools
  (`draft_memory_file` / `draft_skill_file`) that author text into the TRACE and never onto disk.
- **`session.py`** — `run_distillation_artifacts` (ingest once, redact once, run once; returns the
  plan PLUS the redacted transcripts, resolved `run_id`, trace path, events and memory index),
  its unchanged `run_distillation` wrapper, and `assemble`, which re-sources each promotion's
  verbatim drafted bytes from its `tool_call` event by `artifact_id`, reporting an unbacked candidate
  as a problem rather than trusting the plan's own claim.
- **`apply.py`** — the human-gated writer. Everything above is inert; this is where disk changes.
- **`cli.py` / `config.py` / `render.py`** — the shell in front of all of it. `cli.py` is
  `ctx-distillery` (`distill` runs the pipeline above; `show` re-reads a finished trace);
  `config.py` resolves the `CD_*` environment into an `RLMConfig` and the `chat_fn` the two drafting
  tools call; `render.py` owns the one plan rendering that `show`, the dry run, and the eval judge
  all read. The writer's own CLI lives in `apply.py` — see below for why.

## Tier three — the operator's own redaction rules (`CD_REDACTIONS`)

```bash
cp redactions.example.json redactions.json     # then export CD_REDACTIONS=./redactions.json
```

Modelled on toolscout's `TS_TOOLSPACE`: an env var naming a JSON file, a checked-in `*.example.json`
you copy and edit, and a README example that points at the shipped file directly so it works out of
the box. One rule is `{label, regex, description, sample, replace_group?}`, and `redact.py` resolves
the variable at IMPORT time.

**The divergence, stated so nobody removes it: `CD_REDACTIONS` may only ADD.** `TS_TOOLSPACE`
*replaces* toolscout's built-in catalog — safe there, where the worst case is fewer tools. Here the
worst case is a leaked credential, so there is no `disable` key (an unknown key is a hard refusal, so
adding one silently is not possible either), no label shadowing, and no ordering knob. Tier three
runs last, after everything tiers one and two matched is already a placeholder.

`load_operator_rules(path)` is public — point it at a file to validate one by hand. Every rule must
survive four checks, each closing a way a redaction rule fails *silently*; every failure is a
`SystemExit` naming the file, the rule's position and its label.

| check | why it exists |
| --- | --- |
| the CLOSED key set | a mistyped `replace_group` would otherwise quietly mean "replace the whole match" |
| strict compile (warnings → errors) | the Airtable `[[:alnum:]]` trap, arriving through the operator's file instead of the vendored corpus |
| **ReDoS calibration** | Python's `re` has no timeout, and redaction is on the core path so the `regex` module is not an option |
| **`sample`, EXECUTED** | a regex that compiles but never matches gives false confidence, and nothing downstream would ever say so |

**The calibration**, and it is TWO gates over one probe grid — an ascending ladder of short synthetic
adversarial strings, 2→32 characters.

*Gate 1, growth.* Catastrophic backtracking is exponential in the input length, so it shows as a
blowing-up **ratio** between adjacent probe lengths long before any one probe is expensive: every
classic shape measures a remarkably consistent ~4.0× per two extra characters. A rule is refused the
first time a probe costs ≥ 5 ms *and* ≥ 3× its own predecessor two characters shorter. The 5 ms floor
is what makes that safe — the slowest of the 127 built-in patterns needs ~1.6 µs for the grid's worst
probe, a ~3000× margin, pinned rule-by-rule by a test — and below it the ratio is ignored as noise. A
breach is re-timed and the *smallest* measurement decides, because a refusal is permanent and a
descheduled interpreter is not.

*Gate 2, the absolute budget.* No single probe may exceed **20 ms**. This catches *polynomial*
blow-up, which grows too gently for the ratio and is ruinous anyway: `a*a*a*a*a*a*b` stays under 2×
per step and still needs 22.8 ms at 28 characters.

The ladder starts at **2** characters, not 12, because gate 1 is also what bounds the load cost:
`(a?a?a?a?a?a?a?a?a?a?)+$` costs 16.8 ms at four characters and 3.3 *seconds* at six, so a grid that
began at 12 wedged the import outright instead of refusing anything. Every shape above is now refused
within ~200 ms.

The probe grid is built from the rule's *own* pattern in two ways: every distinct literal **character**
(which is what catches `(x+x+)+y`, and why there is no cap — a cap of six let `(?:abcdef)?(z+z+)+Q`
through), and a **reaching prefix** walked out of the pattern's own `re` parse tree, emitted with
filler after it (which is what catches `CORPSECRETPREFIX-(\w+\s?)+$` — the most common operator rule
shape, and one a probe of repeated single characters never reaches past position 0).

The prefix is derived from the **tree**, not by scraping the pattern text. A scraper that took literal
runs of 3+ characters was the first version, and it could not spell a 2-character marker
(`X-(\w+\s?)+$`) or one containing a character class (`ORG-[0-9]{4}-(\w+\s?)+$`) — both loaded clean
and then hung on real input. The walk emits a concrete string for every literal, class and bounded
repeat until it reaches an ambiguous quantifier, and records one prefix at *each* such quantifier, so
a marker sitting behind an earlier one (`\d+-CORPSECRET-(\w+\s?)+$`) is reachable too. It is
`regex_walk.reaching_prefixes`, the same walk `scripts/derive_liveness_samples.py` derives its
liveness fixture from.

Both derived families are **capped**, and that is what bounds the load cost in *aggregate* — gate 1
bounds what one probe may cost, not how many there are. Seeds are bounded by the alphabet itself
(62 alphanumerics at most); prefixes by `_MAX_REACHING_PREFIXES`. Without the second cap a
7,919-alternative pattern produced 1,014,880 probes and a 16.8 s load; it now produces 4,896.

It is a heuristic, not a decision procedure. Two gaps are stated rather than papered over: a
contrived pattern can still evade any finite probe set, and the load cost is bounded only *down to*
the smallest probe — nothing can time a search without running it, so a pattern already astronomical
on a two-character input would still wedge the import.

**`replace_group`** is a closed vocabulary — `null`, or the name of a group the rule's own regex
declares. It replaces that group's span only and keeps the rest of the match. This is the field
finding `secret_assignment` forced: it is the one built-in a plain `{label, regex}` schema cannot
express, and `redactions.example.json` carries it, with a test asserting the generic substitution is
byte-identical to the hand-written `_replace_assignment`.

**`redactions.example.json` is a REAL file, not a skeleton** — the seven tier-one patterns expressed
in this schema plus one illustrative `corp-internal-token` entry. `tests/test_redact_operator.py`
loads it through the real loader, so it cannot rot the way a decorative example can.

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
CONFIRMED by a dedicated control experiment: that Claude Code reads a project-repo-relative
`<project>/.claude/skills/` at all — a scratch directory seeded with a probe skill was read by a
fresh session launched inside it (listed, and invokable), while a sibling control directory without
one was not. Two caveats came with it: a global skill of the same name SHADOWS a project one, and a
project's very first skills directory needs a restart to be discovered. INHERITED but not
re-verified: the `memory/` sub-path — that convention predates this research and no `memory/`
directory existed on the machine it ran on.

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
`session.py`, a tool, `cli.py`, or `__init__.py`.

That test is also why the CLI is **two console scripts** rather than one binary with three
subcommands. Its regex matches a function-local import as readily as a top-level one, so a shared
CLI module importing both `run_distillation` and `apply_plan` would turn it red — and `apply.py` is
not a precedent for a second exemption. So `apply.py` hosts its own `main()`:

```
ctx-distillery        = ctx_distillery.cli:main     # distill / show / export — never imports apply
ctx-distillery-apply  = ctx_distillery.apply:main   # the writer, with its own entry point
```

Two visible consequences of the same rule, both deliberate: neither `show` nor `export` has an
`--out` (`cli.py` may not open a file for writing — redirect with `>`, and the form is
`print(json.dumps(...))`, never `json.dump(..., sys.stdout)`, which would only *look* clean while
calling `.write` at runtime), and `distill` REFUSES a run id whose trace file already exists rather
than deleting it, because `TraceRecorder` appends and `os.remove` is forbidden here. There is no
`--force`. All three sibling projects' `export` takes a positional output path and opens it for
writing; that is the one part of their exporter that cannot be ported here.

## Layout

```
ctx_distillery/
  README.md            # this file — module map, internals, testing
  __init__.py          # the public surface — eager dspy-free names, lazy dspy-bearing ones,
                       #   and NO route to the writer (invariant 8)
  cli.py               # `ctx-distillery` — distill / show / export. Never imports apply.
  __main__.py          # `python -m ctx_distillery ...` -> cli:main
  config.py            # DistillConfig.from_env — the CD_* surface; setup() + make_chat_fn();
                       #   the `claude-agent-sdk/` subscription sentinel + its drafter refusal
  schema.py            # the dspy-free plan shapes + assemble() (eval/ and studio/ read them)
  task.py              # DistillSession(RLMTask) — signature, output_model, instructions
  session.py           # run_distillation_artifacts (ingest once, redact once, run once) +
                       #   its run_distillation wrapper + assemble()
  apply.py             # apply_plan — the human-gated writer, outside the RLM entirely,
                       #   plus `ctx-distillery-apply`'s own main() (see above)
  render.py            # render_plan / plan_as_dict — the ONE plan rendering (eval/ imports it)
  redact.py            # host-side redaction, applied before any text reaches the model: tier one
                       #   (7 hand-written), tier two (the vendored gitleaks subset), tier three
                       #   (load_operator_rules, from CD_REDACTIONS — ADDITIVE ONLY, empty by default)
  patterns/            # gitleaks_subset.json — GENERATED by scripts/port_gitleaks.py, never
                       #   hand-edited (MIT, see VENDOR.md)
  frontmatter.py       # nested-YAML frontmatter parsing (memory + skill shapes)
  rubric.py            # ATLAS TF/TA/TG/PA facts (reward-free), sourced from session.assemble()
  rl_export.py         # the reward-free SFT/RL bundle `ctx-distillery export` prints. No main(),
                       #   no --out, structural labels only (no oracle) — see VENDOR.md
  trace_io.py          # load_trace / dict_events — the ONE place JSONL bytes become events
  tools/               # the five READ-ONLY planner tools
  adapters/
    base.py            # the read-only harness-adapter seam
    claude_code.py     # the one in-scope adapter
  skills/              # memory-vs-skill-criteria.md — shipped in the wheel, read by the planner
eval/                  # ctx-distillery-eval — a separate uv workspace member, judges the ARTIFACT
studio/                # ctx-distillery-studio — replay-only console over a finished trace
tests/                 # fully offline: no live model, no Deno, no network
```

`redactions.example.json` lives at the REPO root (next to `.env.example`), mirroring where toolscout
puts `toolspace.example.json` — it is operator configuration, not package data.
