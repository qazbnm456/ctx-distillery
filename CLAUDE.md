# ctx-distillery — agent guide

`ctx-distillery` is a downstream consumer of [`rlm-kit`](https://github.com/qazbnm456/rlm-kit):
an RLM-driven planner that reads AI coding-agent session transcripts + a persistent memory
store and proposes a distillation plan (what to prune, what to merge across sessions, what to
promote into a memory file or a Skill file). It never applies anything itself. See
`README.md` for the overview and `ctx_distillery/README.md` for the package-level guide.

`rlm-kit` is pinned as a git dependency (see `pyproject.toml`). For local co-development against
an in-progress rlm-kit checkout, install it editable over the top:

```
uv pip install -e ../rlm-kit
```

## Verify

**`make check` is the entry point — it runs all five below, in CI's order, and is the ONE command to
reach for.** Everything it runs is fully offline (no live model, no Deno, no network). This lead-in
used to say "Run BOTH before pushing", which had been an undercount since the `eval/`, `studio/` and
node suites landed — five commands across four suites reachable only by reading the prose below.
`Makefile` is not a module the planner can reach, so it is outside invariant 1's mutation scan
(`tests/test_no_write_capability.py` scans `ctx_distillery/**.py` only); a target per suite still
exists (`make lint` / `test` / `eval-test` / `studio-test` / `static-test`) for running one alone.
The raw commands stay documented here because CI invokes them directly, not through `make` — keep
the two in step, which is what `tests/test_doc_claims.py` pins. One deliberate divergence: `make
test` runs `uv run python -m pytest -q`, not the bare `pytest -q` below, which assumes an ACTIVATED
venv and otherwise dies with "No such file or directory".

- `uvx ruff@0.16.0 check .` — lint (line-length 110, matching rlm-kit's config). **The version pin is
  deliberate and CI carries the same one.** An unpinned `uvx ruff check .` resolves the LATEST ruff at
  run time, so a release that widens the DEFAULT rule set turns the job red with nobody having touched
  a line of code — ruff 0.16's expansion did exactly that to two sibling projects (256 and 224 fresh
  errors, same pyproject, same source). Bump the pin on purpose, and land the resulting fixes in the
  SAME commit as the bump. A bare `ruff` is not
  installed in this workspace; `uvx` is how CI runs it too.
- `pytest -q` — the whole suite. The dspy-bearing tests (`test_task.py`, `test_session.py`)
  drive a REAL `dspy.RLM.aforward` through `rlm_kit.testing.ScriptedInterpreter` +
  `scripted_lm`, so the planner → tools → SUBMIT chain executes (each tool's own tracing runs)
  at zero cost; they `importorskip("dspy")`.
- `tests/test_no_write_capability.py` is the tripwire for invariant (1): a static scan over
  every module under `ctx_distillery/` — except the deliberate, human-gated `apply.py` — asserting
  none contains a write/delete call. If it goes red, someone added a writer — that is the finding,
  not a test to relax, and `apply.py` is not a precedent for a second exemption.
- `tests/test_apply.py` needs no dspy, no rlm-kit model wiring, and no network: applying a plan is
  plain host-side file I/O, so it runs against real files under `tmp_path`.
- A LIVE run additionally needs real credentials and a Deno/pyodide sandbox
  (`brew install deno`). Don't do it in CI; it costs money.
- The `eval/` and `studio/` workspace members each carry their OWN test suite and must be run
  separately — they are not collected by a bare root `pytest -q` (each has its own
  `pyproject.toml` `testpaths`; `--directory eval`/`--directory studio` is what makes `uv run`
  resolve the RIGHT `testpaths` — `--package` alone does not, an earlier Phase-1 fix). `uv run
  --directory eval --package ctx-distillery-eval --extra dev python -m pytest` / `uv run
  --directory studio --package ctx-distillery-studio --extra dev python -m pytest` (matching
  `.github/workflows/ci.yml`'s `eval-test`/`studio-test` jobs). **`--extra dev` is added for
  explicitness, not because it's load-bearing — corrected per adversarial review, which found an
  earlier draft's claim that omitting it breaks the job was FALSE**: this is a `uv` workspace,
  which shares ONE venv across all members, and the ROOT `pyproject.toml`'s `[dependency-groups]
  dev = ["pytest>=8.0"]` already installs pytest into that shared venv on every `uv sync`,
  regardless of which member a given `uv run --package` is scoped to. In a plain-pip environment
  (no `uv`), install each member editable instead: `pip install -e . -e ./eval -e ./studio` from
  the repo root, then run `pytest` from inside each member's own directory.
- `for f in studio/tests/*.test.js; do node "$f"; done` — the studio's frontend static contracts.
  Plain CommonJS, no npm/`package.json`/`node_modules`; `studio/tests/static-contract.test.js` reads
  `static/style.css` and `static/app.js` as TEXT and pins the rules nothing in the Python suite can
  see (the `[hidden]` guard, the `.layout` viewport-height pin, `word-break` on every model-supplied
  field, the draft `<pre>`'s `overflow-wrap`, the `DESIGN.md` §2 derived-state frame classes, the
  responsive stack, and the absolute no-`innerHTML` rule). CI runs it in its OWN `studio-static` job,
  NOT as a step in the 3-version `studio-test` matrix, where it would run three times identically.
- `tests/test_public_api.py` gates the package's public surface: `import ctx_distillery` must stay
  dspy-free (checked in a FRESH subprocess — `sys.modules` in the pytest process is polluted by the
  dspy-bearing tests), every `__all__` name must resolve, `__version__` must match `pyproject.toml`,
  and the writer must be absent (see invariant 8 and the `## Versioning` section below).
- `tests/test_subscription.py` runs WITHOUT the `[subscription]` extra installed: the drafter-hazard
  tests touch only the dspy-free `DistillConfig.from_env`, and the router tests monkeypatch
  `rlm_kit.ClaudeAgentLM`. One sharp edge, recorded because it is easy to get wrong —
  `monkeypatch.setattr(rlm_kit, "ClaudeAgentLM", ...)` does a `getattr` FIRST, which trips rlm-kit's
  package `__getattr__` and pulls dspy into the test process. Fine in a test (they
  `importorskip("dspy")`); never let it become a module-level import.
- **`pytest-asyncio` / `asyncio_mode = "auto"` are DECLINED, deliberately** — all three sibling
  projects carry them, this one does not, and that is a decision rather than an oversight. All three
  of this repo's suites contain ZERO async tests: no `async def test_*`, no `@pytest.mark.asyncio`.
  The four async call sites (`run_distillation` and friends) are driven from synchronous tests
  through an explicit `asyncio.run(...)`, and the only `async def` anywhere in the suites is a
  nested, underscore-prefixed fake pytest never collects. Adding the plugin would change no
  behaviour and buy a dev dependency with no consumer. Reopen it when the first genuinely async
  test needs writing — not for symmetry with the siblings.

## Running — always through the CLI

- **Drive runs via `cli` (`distill` / `show`), never an ad-hoc script.** `ctx-distillery distill
  [project]` is THE entry point: it discovers the project's Claude Code storage
  (`ClaudeCodeAdapter.for_project`), wires the `chat_fn` from `CD_*` (`config.DistillConfig.from_env`
  → `config.setup` → `config.make_chat_fn`), runs `session.run_distillation`, and prints the
  assembled plan. It records `<trace-dir>/<run-id>.jsonl` and writes NOTHING else — there is no
  `responses/` artifact in this project. Don't drive `run_distillation` / `assemble` from a private
  script; extend `cli.py`. Offline re-read: `ctx-distillery show <trace> [--run-id ID] [--json]`.
- **`show` has no `--out`, and `distill` never deletes a stale trace.** Both fall out of invariant 1:
  `cli.py` is inside `tests/test_no_write_capability.py`'s mutation scan, so it may not open a file
  for writing (redirect with `>`) and may not `os.remove` a trace the way the sibling projects' `run()`
  does. `TraceRecorder` appends, so the default `--run-id` is `<project>-<UTC timestamp>` and a run
  whose trace file already exists is REFUSED. Never add a `--force` that deletes one.
- **Applying is a SECOND binary: `ctx-distillery-apply` (`apply.py:main`).** See invariant 8 — this
  is structural, not stylistic. `ctx-distillery-apply <trace> --project <dir> --approve 0,3` is a DRY
  RUN; `--confirm` is what writes. `--allow-skill-scope` defaults to `project` only; installing into
  `~/.claude/skills` needs `--allow-skill-scope global`.

## Invariants — do not break

These are the hard constraints this project is built against; they exist because the operation
this project reasons about (pruning/deleting a user's own history) is irreversible.

1. **No tool ever writes or deletes anything, and the interpreter stays pinned to `pyodide`.**
   Both halves matter: never add a tool that can `open(..., "w")`, delete, or otherwise mutate a
   transcript or memory/skill file — the read-only tool set (`list_memory_files`,
   `read_memory_file`, `read_transcript_chunk`, `draft_memory_file`, `draft_skill_file`,
   `draft_skill_extra_file`) is closed, not a starting point to extend with a writer.
   **`draft_skill_extra_file` widened this enumeration from five tools to six** — it drafts a
   skill's supplementary `references/`/`scripts/` files (see the Known simplifications bullet this
   closed), and it is read-only in EXACTLY the same sense the other five are: it returns text and
   records it to the trace, and never touches a `skill_dir` or any other path. Widening the
   enumeration is not a weakening of "no writer, ever" — the substantive guarantee is unchanged; a
   future addition still has to clear the same bar (text out, nothing written) to join this list,
   not merely extend it by precedent. And never switch the sandbox off the
   explicitly-pinned `pyodide` interpreter — that pin is stated in the task, not left to the
   default, because the "no mutation" guarantee depends on never routing through a
   writable-mount config. Together these make "propose, never apply" a structural property of
   the sandbox, not a convention the planner could be prompted around. The pin is ENFORCED IN
   CODE, not documented: `task._forced_config` runs `dataclasses.replace(config,
   interpreter="pyodide")` before `super().__init__`, so a caller passing `interpreter="local"`
   still gets `pyodide`. (`RLMTask(interpreter=<object>)` still bypasses it — that is rlm-kit's
   documented test seam, where the caller supplies and owns the double, not a config path.)
2. **`output_model` carries only `{action, artifact_id, key_fields}` — never drafted content
   directly.** A promotion candidate's actual markdown+frontmatter body is authored by
   `draft_memory_file` or `draft_skill_file` (both `make_model_tool`-based) and recorded as a
   `tool_call` event. Assemble the real text on READ by matching that event's `artifact_id` —
   never trust the plan's own claim about what it drafted. This is what keeps a label from
   drifting from the bytes it describes. A `promote_to_skill` candidate's supplementary
   `references/`/`scripts/` files follow the SAME rule one level down: `draft_skill_extra_file`
   records each one as its own `tool_call`, keyed by the SAME `artifact_id` plus its own
   `relative_path`, and `schema.assemble` re-sources them the same way — never a field on the plan
   itself.
3. **Sensitive transcript content is redacted host-side before it becomes LM context, in THREE
   TIERS, in that order.** Redaction is not the planner's judgement call — do it in the
   tool/ingestion layer, before any transcript text is exposed to the RLM, the same stance
   rlm-kit already takes for other untrusted content (fetched URLs, MCP output). `redact.py` runs
   **tier one** (7 hand-written patterns), then **tier two** (120 rules mechanically ported
   from gitleaks, `ctx_distillery/patterns/gitleaks_subset.json`), then **tier three** (the
   operator's own rules, from the `CD_REDACTIONS` env var — empty unless set).
   - **Tier one is NOT redundant and must stay FIRST**, on measured grounds. **EXACTLY TWO of the
     seven are genuinely unavailable from gitleaks**: `bearer_token` uses a LOOKBEHIND, which RE2
     literally cannot express, so no gitleaks rule ever will; and `secret_assignment` substitutes the
     VALUE ONLY, keeping `api_key = ` visible, where every gitleaks rule replaces a token. **This
     used to list `private_key` as a third — "a DOTALL BEGIN/END block matcher gitleaks has no
     equivalent for, because upstream's rules match the header not the body". That is FALSE and was
     verified false**: the vendored `private-key` regex ends `[\s\S-]{64,}?KEY(?: BLOCK)?-----`,
     which is RE2's own DOTALL idiom, and it matched a full 202-character block at span (0, 202),
     body included. The honest justification for keeping `private_key` in tier one is REFRESH
     RESILIENCE — the same argument the other redundant patterns already carry, and the same thing
     this bullet said about it eight lines further down. Don't reinstate the capability claim.
     The decisive argument for the tier as a whole is not about any one pattern: on the real
     426 KB transcript this was measured against, the operator's own live `sk-<uuid-shaped>`
     PRIVATE-PROXY key was caught by tier one and matched by **none** of the 120 anchored gitleaks
     rules — an anchored corpus of vendor prefixes is structurally blind to a key minted by
     whoever is in front of you. Tier one running first also means a shape this project owns keeps
     its own stable label (`[REDACTED:github_token]`, not `[REDACTED:github-pat]`) across refreshes.
     **All 7 stay even though 5 are redundant TODAY** (`private_key`→`private-key`,
     `aws_access_key_id`→`aws-access-token`, `github_token`→`github-pat`,
     `google_api_key`→`gcp-api-key`, and `api_key` against the `sk-`-shaped vendor rules — for the
     vendors upstream knows, which is the gap the private-proxy key above proves), because tier two
     is REGENERATED from a moving upstream and a
     refresh that renamed or narrowed one of those would reduce coverage with no signal anywhere. The
     redundancy is made DELIBERATE by `test_redact_golden.py`'s `_OVERLAP` table, which asserts both
     halves — so a refresh that drops a rule surfaces as "this shape is now TIER-ONE-ONLY" rather
     than as nothing. `google_api_key` is only PARTIALLY redundant even now: gitleaks' `gcp-api-key`
     requires a terminator from a fixed set where tier one ends on `\b`, so `AIza<35>]` is
     tier-one-only. Never "clean up" one of the four without moving its row.
   - **TIER THREE IS ADDITIVE ONLY, and that is a DELIBERATE DIVERGENCE from the convention it
     copies — say so wherever it is described, or someone will "fix" it back.** The feature follows
     toolscout's `TS_TOOLSPACE` shape (an env var naming a JSON file, a checked-in
     `redactions.example.json` you `cp` and edit, a README example pointing at the shipped file so it
     works out of the box). But `TS_TOOLSPACE` REPLACES toolscout's built-in catalog — safe there,
     where the worst case is FEWER TOOLS. Here the worst case is a LEAKED CREDENTIAL, so
     `CD_REDACTIONS` may only ADD: no `disable` key (the key set is CLOSED, so an unknown key is a
     hard refusal and one cannot be smuggled in), no label shadowing, no ordering knob, and tier
     three runs LAST. An operator can never turn tier one or tier two off. The statement is required
     in `.env.example`, `README.md` and `redact.py`.
   - **A tier-three rule's `sample` is MANDATORY and EXECUTED at load, and ReDoS is refused at LOAD
     rather than survived at run time.** Both are load-time refusals (`SystemExit` naming the file,
     the rule's position and its label) because both failures are otherwise SILENT. (a) A regex that
     compiles but never matches gives false confidence — the same class as the Airtable POSIX trap —
     so the rule's own regex must redact the rule's own `sample`, or the load fails. (b) Operator
     regexes run on Python's BACKTRACKING `re` over ~500 KB and `re` has no timeout; adding the
     `regex` module would put a dependency on the CORE path, which is out. So every rule is
     CALIBRATED after compiling, against an ascending probe ladder (2→32 characters) on **TWO
     GATES, and neither is redundant** — an adversarial review produced a live evasion for each:
     **(1) GROWTH RATIO**, refusing the first probe that costs ≥ `_GROWTH_FLOOR_SECONDS` (5 ms) AND
     ≥ 3x its own predecessor two characters shorter. Exponential backtracking measures a consistent
     ~4.0x per two characters, so this fires long before any probe is expensive, and it is also what
     BOUNDS the load cost — which is why the ladder starts at 2 and not 12. The old absolute-only
     grid HUNG on `(a?a?a?a?a?a?a?a?a?a?)+$` (16.8 ms at 4 characters, 3.3 s at 6, never returned);
     it is now refused in ~40 ms. **(2) The ABSOLUTE 20 ms per-probe BUDGET**, for POLYNOMIAL
     blow-up that grows too gently for the ratio: `a*a*a*a*a*a*b` stays under 2x per step and still
     needs 22.8 ms at 28 characters. The floor is the safety argument for the ratio — the slowest of
     the 127 built-in patterns needs ~1.6 µs for the grid's worst probe, a ~3000x margin, and a
     per-rule test pins it — and a breach on either gate is RE-TIMED with the smallest measurement
     deciding, because a refusal is permanent and a descheduled interpreter is not.
     The probe grid is derived from the pattern's OWN source two ways, both load-bearing: EVERY
     distinct literal CHARACTER (no cap — a cap of six let `(?:abcdef)?(z+z+)+Q` through, since `z`
     was the seventh) and a REACHING PREFIX walked out of the pattern's own `re` PARSE TREE, emitted
     as a prefix with filler (`CORPSECRETPREFIX-(\w+\s?)+$` — a marker plus a quantified tail is the
     MOST COMMON operator rule shape, and a probe of one repeated character fails at position 0 and
     never reaches the tail; that rule passed calibration in 0.3 ms while really costing 1172 ms on
     24 characters). **The prefix comes from the TREE, never from scraping the pattern TEXT.** A
     scraper that took literal RUNS of ≥3 characters was the first fix and it left two holes a third
     review reproduced through the real loader: `X-(\w+\s?)+$` (a 2-character marker, under the
     minimum — loaded in ~107 ms, then hung >15 s on a 46-character line) and
     `ORG-[0-9]{4}-(\w+\s?)+$` (a CHARACTER CLASS inside the marker — the scraped run was `ORG-`,
     and no filler spells `1234-`). Lowering the minimum to 1 closes only the first; the tree walk
     closes both plus backreferences and leading lookbehinds. It is
     `regex_walk.reaching_prefixes`, SHARED with `scripts/derive_liveness_samples.py` rather than
     duplicated (invariant 11).
     **Both derived families are CAPPED, and that is what bounds the load cost in AGGREGATE.**
     Gate 1 bounds what ONE probe may cost; it says nothing about how many probes there are, and the
     old scraper's prefix count was unbounded in pattern length — a 7,919-alternative pattern
     produced 1,014,880 probes and a 16.8 s load, every probe individually cheap. Seeds are bounded
     by the alphabet itself; prefixes by `redact._MAX_REACHING_PREFIXES`. Same pattern now: 4,896
     probes. Never describe gate 1 as bounding "the load cost" without that second half.
     Both directions are tested. Calibration runs BEFORE the sample check — the sample check executes
     the pattern, so a catastrophic one must already be gone. It is a heuristic, not a proof, and
     `redact.py` says so — including the gaps it does NOT close: the load cost is bounded only down to
     the smallest probe, because nothing can time a search without running it; and the filler after a
     reaching prefix is a fixed set, so a marker followed by a tail keyed on some other character
     (`X-(z+z+)+Q`) is reached but not fed.
   - **A tier-three rule's `sample` must be SYNTHETIC, and the docs say so.** It is the one field the
     schema requires to be credential-SHAPED, and a load refusal ECHOES it to stderr — scrollback, a
     CI log, a crash report. `.env.example` and `README.md` both say to invent a token of the right
     shape; `_excerpt` caps the echo at 64 characters as a mitigation, not a licence.
   - **`replace_group` is a closed vocabulary: `null`, or the name of a group the rule's OWN regex
     declares.** It replaces that group's span only, keeping the rest of the match — the generic form
     of what `secret_assignment` does by hand, and the one built-in a plain `{label, regex}` schema
     cannot express (which is precisely why the schema needs the field). Data, never code: no
     callable, no expression, no substitution template. `redactions.example.json` must stay a REAL
     working file — the 7 built-ins expressed in this schema plus one illustrative org-specific
     entry — and `tests/test_redact_operator.py` loads it through the REAL loader so it cannot rot.
   - **The port is mechanical and CHECKED IN** (`scripts/port_gitleaks.py`): POSIX classes →
     explicit ranges with an unknown class a HARD ERROR, `\z` → `\Z`, mid-pattern `(?i)` hoisted to
     a global flag (this WIDENS — the safe direction for a redactor), and a filter to ANCHORED
     ("shape A") rules only, dropping the ~101 generic keyword-near-assignment rules that would need
     gitleaks' 1,446-entry stopword allowlist. **Every ported pattern is compiled under
     `warnings.simplefilter("error")`, and that is the load-bearing line**: gitleaks'
     `airtable-personnal-access-token` (`pat[[:alnum:]]{14}\.[a-f0-9]{64}`) COMPILES in Python with
     only a `FutureWarning` and then means something else entirely, so a real Airtable PAT never
     matches — in a scanner a missed finding, in a REDACTOR a live credential flowing to a model
     with no error anywhere. `redact.py` recompiles under the same strictness at import, and refuses
     to degrade quietly if the artifact is missing or malformed. **All THREE places that compile a
     ported pattern must `re.purge()` FIRST** (`port_gitleaks.py`, `redact._rule_from_entry`,
     `redact._load_gitleaks_subset` — the last was missing it): `re.compile` caches by
     `(pattern, flags)` and a cache HIT never re-parses, so it never re-emits the warning the guard
     exists to catch. And the LOADER's own strictness must be pinned by a test that runs the LOADER —
     `test_every_ported_pattern_recompiles_with_warnings_as_errors` compiles the artifact itself and
     so pins the ARTIFACT only; deleting the loader's `simplefilter("error")` used to turn nothing red.
   - **Two deliberate divergences from upstream, both argued in `redact.py`.** (a) gitleaks' ENTROPY
     floors are loaded for provenance and NEVER enforced: a scanner's false positive costs triage
     time, a redactor's false negative ships a credential, so the cost asymmetry is inverted, and
     everything in tier two is anchored on a literal prefix that already IS the evidence. (b) The
     KEYWORD GATE is kept exactly as upstream ships it — an earlier draft ungated any rule whose
     keywords its own pattern did not imply, and on the real transcript that immediately redacted a
     git commit SHA, because `sourcegraph-access-token` carries a bare `[a-fA-F0-9]{40}` alternative
     that only its keywords make safe. The gate's known false-negative direction — a rule keyed on
     the vendor's NAME is not reached unless the transcript names the vendor — is documented, not
     fixed, and it is **EXACTLY TWO rules**: `airtable-personnal-access-token` and
     `facebook-access-token`. Never write "a few, such as airtable" again; that phrasing is how the
     second one stayed invisible. The enumeration is DERIVED, not hand-kept — a test checks each
     rule's liveness sample against its own gate and asserts the set is precisely those two.
   - **Every one of the 120 vendored rules has a pinned LIVENESS sample, because the golden corpus
     carries a matching string for only 40 of them** (54 rules have a case of SOME kind; just 17 have
     an upstream TRUE POSITIVE — say which number you mean, and never write the old "45", which
     matched none of the three). `tests/data/liveness_samples.json` (generated by the checked-in
     `scripts/derive_liveness_samples.py`, which walks each rule's own `re` parse tree) holds one
     matching string per rule; tests assert every rule still matches its own sample and is still
     redacted end to end. Without it a dead rule is INVISIBLE — an adversarial review rewrote
     `adobe-client-secret`'s regex to `ZZZ_MATCHES_NOTHING`, and separately widened its `{32}` to
     `{288}`, with the whole suite green both times. The samples are CHECKED IN, never derived inside
     the test: a sample derived from the rule's own current pattern moves WITH the rule, so the
     narrowing mutation would regenerate a 288-character sample and stay green. Regenerating on a
     corpus refresh is a `VENDOR.md` step whose DIFF is the review artifact.
   - **ALL THREE suites pop `CD_REDACTIONS` at conftest IMPORT time — root, `eval/` and `studio/`.**
     `redact` resolves the variable when the MODULE is imported, and every member imports
     `ctx_distillery` during COLLECTION, so an autouse fixture is structurally too late. A developer
     with the variable exported otherwise runs a member against their own private rules: a BROKEN
     file makes it `INTERNALERROR` at collection, a VALID one just makes it non-hermetic. Each member
     asserts `redact._TIER3 == ()` itself rather than trusting the arrangement, and `eval/`'s
     `CD_VARS` drift test scrapes `redact.py` as well as `config.py` (scraping only `config.py` is
     what hid `CD_REDACTIONS` from it).
   - **NO NETWORK, EVER, and no new runtime dependency** — stdlib `re`/`json` only. Redaction is on
     the core path (`session.run_distillation` calls it unconditionally), and TruffleHog's model
     (POST the candidate secret to the vendor to verify it) is the exact inversion of a module that
     runs over the operator's own private history. `redact_transcript` stays idempotent and
     non-raising across all three tiers (tier three's half is enforced per rule AT LOAD: a rule that
     re-matches its own `[REDACTED:<label>]` placeholder is refused).
   - **`tests/test_redact_golden.py` is what makes the vendored copy trustworthy rather than
     hopeful**: it replays gitleaks' OWN true/false-positive corpus (`tests/data/gitleaks_golden.json`,
     scraped by `scripts/extract_gitleaks_golden.py` from upstream's Go generator) against the ported
     patterns. The false positives are PARTITIONED on purpose — `fps_rejected` (must stay rejected;
     catches a port that widened a rule into meaninglessness) vs `fps_over_redacted` (accepted
     over-redaction from the dropped entropy floors and allowlists) — and a case moving between the
     two buckets is meant to go red and force a human to look. Refresh path and licence: `VENDOR.md`.
4. **The harness-adapter seam (`ingest` / `schema_for` / `list_targets`) is read-only, full
   stop.** See `ctx_distillery/adapters/base.py`. No adapter may ever expose a write/emit path
   reachable from an RLM tool — the actual "apply" step (now built: `ctx_distillery/apply.py`)
   stays a separate, human-gated action outside the RLM trajectory entirely, and gained NO adapter
   method: writing into `memory_dir` is ordinary host-side Python, the same reasoning
   `tools/memory_reader.py` gives for reading.
5. **Tools close over an immutable SNAPSHOT, never a live adapter.** `run_distillation` calls
   `adapter.ingest()` EXACTLY ONCE; that `list[ArtifactRef]` is what every tool factory that needs
   it receives — FOUR of the six (`list_memory_files`, `read_memory_file`, `draft_memory_file`,
   `draft_skill_file`); `read_transcript_chunk` closes over the transcript list instead, and
   `draft_skill_extra_file` needs neither (a supplementary file has no name-collision concept to
   check the snapshot against). Nothing in `HarnessAdapter` promises `list_targets()` is cheap or
   stable across
   calls, so a live reference would let `read_memory_file`'s allowlist shift mid-run — and it
   would create a second copy of the transcripts the driver already owns. The allowlist check is
   an EXACT `Path(path).resolve()` match against the snapshot; never make it a prefix or
   substring test (a substring test lets `/etc/passwd` through under a crafted name, and an
   unresolved prefix test lets a `..`-segment trick pass) — this defends the REQUEST side.
   **Separately**, `ClaudeCodeAdapter.list_targets()` itself must never let a symlink living
   inside `memory_dir` fold its outside target into the snapshot in the first place (an
   adversarial review reproduced exactly that escape) — it only enumerates a resolved path whose
   PARENT is still `memory_dir` itself. Exact-match-on-request and containment-at-enumeration are
   two separate checks; neither substitutes for the other. `apply.py` mirrors the second one on the
   WRITE side with the identical test (`resolved.parent == memory_dir`), before any write.
6. **Storage discovery is CONFIRMED throughout, but the EVIDENCE behind each part differs — keep the
   distinction visible.** `ClaudeCodeAdapter.for_project(project_dir)` locates the real storage, and
   the evidence behind each part is NOT equal. Say so wherever it is described, and never upgrade one
   to sound like another:
   - **CONFIRMED**: `sanitize(project_dir)` (every `/` of the absolute path → `-`, nothing else
     transformed) giving `~/.claude/projects/<sanitized>/`; transcripts as one `<session-id>.jsonl`
     per past conversation, sibling to `memory/`; global skills at `~/.claude/skills/<name>/SKILL.md`
     (each skill is a DIRECTORY, not a flat file).
   - **CONFIRMED by later observation — this was the INHERITED one, and the label is now stale
     history rather than a caveat**: the `memory/` sub-path inside the project storage directory. It
     began as this project's pre-existing assumption, carried forward honestly because no `memory/`
     directory existed on the machine the original research ran on. It exists now: **12 of 24 project
     storage directories carry one, all at exactly the expected depth (a DIRECT child of the project
     storage directory), holding 51 `.md` files and 9 `MEMORY.md` indexes** — the assumed layout,
     unchanged. Auto-discovery only ever needed the sanitization rule to be right, and that was
     always confirmed. This bullet said "INHERITED, not re-verified — no `memory/` directory existed"
     for a long time after that stopped being true; the bullet below records the same failure for the
     project-skills path. **A stale CONFIRMED/UNCONFIRMED label is the bug this invariant exists to
     prevent, and it has now caught this invariant itself twice.** Re-check the labels when you touch
     them, and prefer citing the observation to repeating the adjective.
   - **CONFIRMED by a dedicated control experiment** (it was an UNCONFIRMED hypothesis for one pass;
     this bullet used to still say so long after the experiment closed it — if you find another place
     that still calls it unverified, that place is the stale one): Claude Code DOES read a
     project-repo-relative `<project>/.claude/skills/<name>/SKILL.md`. A throwaway probe — a scratch
     directory, never a real project on this machine, seeded with
     `.claude/skills/probe-test-skill-xyz123/SKILL.md` — was inspected by a genuinely FRESH `claude -p`
     process launched from inside it, and showed all three of: the project-local skill was listed among
     that process's available skills; a sibling CONTROL directory with no `.claude/skills/` did NOT see
     it (isolating the effect to the project-relative directory rather than a global leak); and the
     skill was actually INVOKABLE, loading its real body. Anthropic's own documented scope table agrees
     (Personal `~/.claude/skills/<name>/SKILL.md` for all projects vs. Project `.claude/skills/<name>/`
     for one). Two caveats from that same experiment are load-bearing and live in "Known
     simplifications" below — a GLOBAL skill of the same name SHADOWS a project one, and a project's
     very FIRST skills directory needs a Claude Code restart before it is discovered.
   The transcript RENDERING is deliberately LOSSY and its rules are pinned by tests: filter to
   `user`/`assistant` FIRST (no other event type carries `message` at all), handle `message.content`
   as either a plain string or a list of blocks, size a `tool_result` in chars OR blocks depending on
   ITS OWN content's shape, and name an unrecognized block rather than dropping it. **`isSidechain`
   is the field that separates the two transcript STORES, and the old instruction here — "a DEFENSIVE
   NO-OP … do not re-describe it as removing subagent noise" — described it by the ONE population
   where it does nothing.** On a main-thread file it really does filter nothing (measured: `False` on
   0 of 57,928 user/assistant events across 883 session files, which is why
   `render_transcript_events`'s default is byte-identical either way). On a SUBAGENT file it filters
   **everything** (72,126 of 72,126, across 874 files) — so reading one means passing
   `include_sidechain=True` EXPLICITLY, never deleting the filter. The default stays `False` so a
   future Claude Code that inlines sidechain events into the main file cannot silently double-count
   them. Every discovery helper takes a `home=` override, and no test may read this machine's real
   `~/.claude` (non-hermetic, and it would pull real user content into a fixture).
7. **A skill's REQUIRED frontmatter is `name` + `description`, full stop — in all three places.**
   `when_to_use` and `dispatch_intent` are OPTIONAL extras: accepted and passed through verbatim when
   present, never required. Every real installed skill inspected carried them, but all of those were
   one author's single suite, and Anthropic's documented Agent-Skills convention requires neither —
   mandating them would generalize from N=1. The three encodings must move TOGETHER or they drift:
   `make_skill_validator` (`tools/drafting.py`), `_spec_for_skill`'s model-facing PROMPT TEXT (same
   module), and `ClaudeCodeAdapter.schema_for("skill")`. A skill draft is also collision-checked
   SCOPE-AWARELY: `drafting._existing_names(index, "skill", scope)` filters by scope, because the
   global and project stores are independent namespaces and the same name in the other scope is not a
   collision.
8. **`apply.py` is the ONE writer, and it is unreachable from the RLM.** It is human-called
   (`apply_plan(memory_dir, assembled_plan, approved_ids)`), takes EXPLICIT per-candidate
   approval (never "apply the whole plan"), re-scans `list_targets()` ITSELF at apply time as the
   sole collision/target authority (the run's snapshot is stale by construction), creates a
   promotion with `open(path, "x")` (O_EXCL — the atomic, TOCTOU-proof enforcement; the re-scan is
   only the friendly early message), derives the filename as `slugify(frontmatter["name"]) + ".md"`
   with a degenerate slug being a hard refusal AND the slug CAPPED at `_SLUG_MAX` (120, the same
   number every other slugger in the workspace uses — `cli._slug`, `studio`'s `app._RUN_ID_MAX`,
   `eval`'s `cli._TASK_ID_MAX`; the input is untrusted MODEL output and one path component over
   ~255 bytes is an `OSError` from the first `stat` that touches it), ARCHIVES a prune to
   `<memory_dir's parent>/_ctx_distillery_archive/` instead of deleting it, and refuses any
   candidate carrying `problems` / `draft_ok is False` / an empty promotion draft. Because it
   writes, it is the one module EXEMPT from `tests/test_no_write_capability.py`'s mutation scan —
   the exemption is guarded by `test_apply_is_unreachable_from_the_planner_path`, which asserts no
   module on the RLM path imports it. Never import `apply` from `task.py`, `session.py`, a tool, or
   `__init__.py`; never give the planner a way to reach it.
   **This is why the CLI is TWO console scripts, not one binary with three subcommands.** That
   reachability test scans EVERY `.py` under `ctx_distillery/` except `apply.py` itself, and its
   regex matches a function-local import as readily as a top-level one — so a shared `cli.py`
   offering both `distill` and `apply` cannot exist without turning it red, and `apply.py` is
   explicitly not a precedent for a second exemption. The resolution keeps both properties:
   `ctx-distillery = ctx_distillery.cli:main` (planner: `distill` / `show`, never imports `apply`)
   and `ctx-distillery-apply = ctx_distillery.apply:main` (the writer hosts its own entry point).
   Do NOT "fix" this by relaxing the regex, by adding a second exempt module, or by reaching the
   writer through `importlib` — the last is evading a tripwire by spelling. Do not add a
   `python -m ctx_distillery apply` shim either: `__main__.py` would then be the importer.
   The CLI expresses the same "explicit per-candidate approval" the API does: `--approve` takes
   indices, `--confirm` is a second deliberate act (the default is a dry run that writes nothing),
   and `tests/test_apply_cli.py::test_no_flag_ever_approves_the_whole_plan` is the tripwire against
   an `--all` creeping back in.
9. **`apply_plan`'s roots are PER KIND, and a skill's containment check is its OWN check.** A skill is
   NOT a flat `<slug>.md` in the memory store: it is `<skills_root>/<slug>/SKILL.md` — one directory
   deeper, under a root that is never `memory_dir` (`~/.claude/skills` for global,
   `<project>/.claude/skills` for project). The flat check (`resolved.parent == memory_dir`) would
   REFUSE every legitimate skill write, so do not try to bend it: `_skill_target` is a separate
   function asserting the slug carries no path separator, that `<root>/<slug>` resolves to a DIRECT
   child of the root, that `SKILL.md` there resolves inside that directory, and that `<root>/<slug>`
   does not already exist as something else. **EVERY filesystem call in it is inside a `try`, the
   last check included.** That check (`is_symlink()`/`exists()`/`is_dir()`) used to sit OUTSIDE, and
   `_ignore_error` swallows only ENOENT/ENOTDIR/EBADF/ELOOP — so an over-long slug raised a raw
   `OSError` (ENAMETOOLONG, reproduced at 300 characters) out of `apply_plan` mid-batch, breaking the
   guarantee the enclosing `try`'s own comment states: *a refusal is the right answer to an unusable
   slug — never an exception escaping `apply_plan` halfway through a run of candidates.*
   `slugify`'s cap stops that particular input arriving, and this wall stands anyway; neither
   replaces the other. Which root is chosen comes from the candidate's own
   `key_fields["scope"]` ("global"/"project"), a documented convention exactly like `prune`'s
   `target_path` — a missing or bogus scope is REFUSED, never defaulted, and a scope whose root the
   caller did not pass is refused too (the caller decides where a skill may be installed). Derive the
   roots with `adapters.claude_code.global_skills_root()` / `project_skills_root(project_dir)` — the
   same functions `for_project` uses, so the reader and the writer cannot disagree about a location.

   **A skill's SUPPLEMENTARY `references/`/`scripts/` files get their OWN containment check too,
   for the same reason `_skill_target` is separate from the flat memory check**: a possibly-nested
   relative path needs its own wall, not a bent version of one built for exactly one path
   component. `_skill_extra_target(skill_dir, relative_path)` confines a supplementary file to the
   closed `references`/`scripts` prefix set, refuses any `.`/`..`/absolute/home-relative segment,
   and checks `resolved.is_relative_to(resolved_skill_dir)` — a bool check that never raises, unlike
   `Path.relative_to()` — catching a symlinked intermediate directory the same way `_skill_target`
   already does for the skill directory and `SKILL.md` itself. `_promote_skill` runs this check for
   EVERY supplementary file BEFORE writing anything at all, including `SKILL.md` — a candidate
   doomed by one bad `relative_path` must never leave a half-written, DISCOVERABLE skill behind
   (`rlm_kit.skills.discover_skills` globs `*/SKILL.md`). Writing itself never deletes a
   supplementary file an `overwrite` draft omits — consistent with "archives, never deletes"
   holding everywhere in this module.

   **`_skill_extra_target` checks each `relative_path` in ISOLATION, which is not the whole
   story — a second adversarial pass on the SHIPPED code found that two individually-valid entries
   can still conflict with EACH OTHER.** `relative_path="scripts/utils"` (a file) and
   `relative_path="scripts/utils/helper.py"` (which needs `scripts/utils` to be a directory) both
   pass containment alone, and writing them in sequence let `SKILL.md` and the first extra land on
   disk before the second one's `mkdir` collided — reproduced end to end through the real
   `assemble()` -> `apply_plan` path, exactly the half-written-discoverable-skill outcome the
   validate-before-write pass exists to prevent. `_extra_path_conflict` closes it: checked on the
   RESOLVED targets, before anything is written, alongside every entry's own containment check.

10. **`studio/` (`ctx-distillery-studio`) is READ-ONLY of the trace file and unreachable from the
    RLM path — it is a THIRD workspace member, never a fork of the harness.** It replays a finished
    `DistillSession` run's trace/v1 JSONL file and NEVER calls `ctx_distillery.apply.apply_plan` —
    applying a plan stays a separate, human-invoked action outside any web request, exactly as
    invariant 8 already requires. There is no live-drive endpoint (no `POST /v1/distill` or
    similar), and the refusal is argued and falsifiable, not a default.
    **The full invariant lives in `studio/CLAUDE.md`** — the three surviving reasons against a live
    endpoint, the `_slug_id` cap, the `textContent`-only rendering rule, and the `trace_io`
    dict-shape guard. It was moved there because it applies to that directory and to nothing else,
    and Claude Code loads a nested `CLAUDE.md` only when reading files under it; the normative
    sentence above stays HERE because it also constrains anyone editing `apply.py`. Corrections go
    in the nested file — never re-add a second copy here.
    **This entry is a STUB, not a gap: the number 10 is cited by ~20 places in code, tests and CSS
    plus 13 in `CHANGELOG.md`, so the list still runs 1–12. Never renumber.**
11. **Trace-reading logic has ONE implementation per job, shared across all three members — never a
    per-member copy. FIVE functions are covered: `rubric.plan_from_events` (plan-from-trace
    reconstruction), `trace_io.load_trace`/`dict_events` (the non-dict shape guard),
    `trace_io.draft_cause` (a recorded drafting call's outcome, see invariant 12),
    `trace_io.transcript_facts` (a run's own transcript composition, built from
    `run_start_meta`/`transcript_composition` — the same guard `studio/`'s `mapper.py` used to keep
    to itself until `eval/`'s scorecard needed it too, exactly the "a third consumer forces the
    shared module" pattern this invariant already names elsewhere), and `render.render_plan` (the
    human/judge-legible plan rendering).** The same rule applied OUTSIDE
    the trace path once: `ctx_distillery/regex_walk.py` is the ONE `re`-parse-tree walk, shared by
    `scripts/derive_liveness_samples.py` (`sample_for`, which generates the liveness fixture) and
    `redact._reaching_prefixes` (`reaching_prefixes`, which derives a ReDoS probe's marker). The walk
    moved INTO the package rather than being copied out of `scripts/`, and the fixture regenerates
    byte-identically, which is what proves the move was behaviour-preserving. It writes nothing, so
    it is inside `tests/test_no_write_capability.py`'s scan like everything else. Same
    failure mode, found twice; the second one is `ctx_distillery/trace_io.py`, the ONE place JSONL
    bytes become events. `rlm_kit.trace.load_events` does no shape validation, so a JSON-valid
    non-dict line reaches every `.get(...)` consumer as-is; `studio/` fixed that member-locally
    first, and **`eval/` needing the identical guard a THIRD time is what forced the shared module**
    — exactly the situation that had already forced `plan_from_events` public. `rubric`, `session`,
    `eval/taskset`, `eval/cli` and `studio/app._load_trace` all read through it. `load_trace`
    re-implements the `run_id` filter instead of passing `run_id=` down to `load_events`, and that
    is LOAD-BEARING: `load_events`'s own filter is an unguarded `event.get("run_id")`, so
    delegating puts the crash UPSTREAM of the guard, where nothing in `ctx_distillery` can reach it
    (this is why hardening only the consumers would NOT have fixed `eval/cli.py`). Hardening
    `load_events` upstream in rlm-kit is a fine follow-up THERE; it is not a prerequisite here, and
    `load_trace` stays correct either way.

    The first of the two: **`rubric.plan_from_events` is the ONE public plan-from-trace
    reconstruction — `eval/` (and `studio/`) call it, neither keeps its own copy.** It used to be
    private (`rubric._plan_from_events`) with a duplicate local copy in `eval/ctx_distillery_eval/score.py`
    (kept separate only because `eval/`'s own convention is to never reach across the package
    boundary into an underscore-prefixed helper). `studio/` needing the SAME reconstruction a third
    time is what forced the actual fix: promote it to public on `rubric.py` (already public,
    top-level, and already imported-from by `eval/` for `rubric_to_meta`) and have `eval/score.py`
    import it instead of duplicating it again. Don't reintroduce a second copy anywhere — the
    `ValidationError`-degrade fix below has already needed applying to two copies once; a third copy
    means a third place a future fix can drift out of sync.

    The fourth: **`trace_io.draft_cause` is the ONE cause classifier**, and it is the only one of the
    four that was found by an argument rather than by a bug. `rl_export._draft_cause` and
    `schema._not_ok_problem` each derived a drafting call's cause from the same two payload fields,
    in two modules. They AGREED on every payload shape the suite covered — the collapse invariant 12
    describes was already fixed in both — but nothing PINNED that they must, and a sibling consumer
    of the same kit reported getting the identical classification wrong twice INDEPENDENTLY, the
    second time in a "fix" that looked complete while still counting an endpoint failure as a gate
    rejection. A partial fix that looks complete is the more dangerous state, because nothing prompts
    a second look. So the two now call one function, and `tests/test_draft_cause.py` pins BOTH
    directions: identity (same function object, and `rl_export._draft_cause` must not come back) and
    behaviour (over the five payload shapes, the problem line's wording and the metric's bucket name
    the same cause — verified by sabotaging each surface in turn and watching it go red).
    `rubric.trace_facts`'s `any_circuit_broken` is deliberately NOT folded in; see invariant 12.

    The third: **`render.render_plan` is the ONE plan rendering**, promoted from `eval/`'s `score.py`
    (where it was written for the judge prompt) when `ctx-distillery show` needed the identical text —
    a reviewer deciding what to approve should read exactly what the judge reads. `eval/score.py`
    imports it and re-exports it in `__all__`, so `from ctx_distillery_eval.score import render_plan`
    still works; `eval/tests/test_score.py` pins the IDENTITY, not just the behaviour. The promotion
    immediately paid for itself: the no-candidates branch used to `return` early and DROP the
    run-level problems line, so a run that died before SUBMIT rendered — to a reviewer and to the
    judge — as a bare "proposed no candidates" that never said why. Fixed once, in the one place.

12. **A drafting call's outcome is READ from a named `cause`, never re-reasoned per call site, and no
    surface may name the validator unless it knows the validator ran.**
    `rlm_kit.tools.model.make_model_tool` sets `ok=False` when (a) the deterministic host-side
    validator declined the text, (b) the model ENDPOINT failed after its transient retries
    (`endpoint_error` set, `raw=""` — the validator never ran), or (c) the CIRCUIT BREAKER
    short-circuited (`circuit_broken=True`, `raw=""` — the model was never even called). Collapsing
    them into one validator-flavoured label rendered a bare connection failure as ``artifact 'a1'
    failed its format check: Connection refused``: it blames the model for an infrastructure fault in
    the text a human reads before deciding what to apply, and in `rl_export` it is TRAINING SIGNAL
    that would teach a trainer to read a 502 as model dishonesty.

    **Since rlm-kit `4fcd50b2` the cause has a NAME, and this project uses it end to end.**
    `ModelToolResult` exposes `cause` (`"ok"` / `"invalid"` / `"endpoint"` / `"circuit_broken"`, the
    `CAUSE_*` constants exported from `rlm_kit.tools`) and `validator_ran` as PROPERTIES — not
    dataclass fields, so they exist only on a LIVE result object and never arrive in a trace by
    themselves. Hence two halves, and both are required:

    * **Record it at the source.** `tools/drafting.py` holds the live result, so it records `cause`
      and `validator_ran` onto every drafting `tool_call` beside `endpoint_error`/`circuit_broken`,
      and `_errors_with_infra` branches on `result.cause` rather than re-deriving. A payload that
      SAYS what happened beats every downstream reader reconstructing it.
    * **Read it, with a fallback for old traces.** `trace_io.draft_cause(payload)` PREFERS a recorded
      `cause` (ignoring any value outside the closed vocabulary) and otherwise **CALLS
      `rlm_kit.trace.payload_cause`** — it does not reimplement the chain. That matters because
      `rl_export`, `schema` and `studio/` all read historical traces, and a pre-`4fcd50b2` trace has
      no `cause` key at all.

      **The delegation was refused once, on a real defect, and the sequence is the reusable part.**
      `payload_cause` landed upstream reading `endpoint_error or error` — TRUTHINESS — while its own
      docstring called it the read-side mirror of `ModelToolResult.cause`, which has always read
      `is not None`. They disagree on the EMPTY STRING, and that is the common case: the field is
      `str(exc)`, which is `''` for `httpx.ConnectTimeout`/`ReadTimeout`/`ConnectError`,
      `TimeoutError`, `OSError` and `RemoteDisconnected` — measured, all six. So this project took
      the kit's KEY SET (the endpoint string under BOTH names, which it had been missing), kept its
      own `is not None`, and pinned the divergence with a test instead of adopting it. The defect
      was then fixed upstream (`6d010447`); a differential over all 81 cause-less payload shapes now
      shows ZERO disagreement, so the second copy was collapsed into a call.
      **Collapse into a dependency when the difference is gone and provably so — not when it is
      merely inconvenient.**

      The guard outlived the copy: `tests/test_draft_cause.py` still asserts the six empty-string
      transport failures classify as `endpoint`, now pointing THROUGH the delegation, so it is a
      tripwire on an upstream regression rather than on a local edit. A second test asserts
      `draft_cause` and `payload_cause` agree over every cause-less shape — behaviourally, because
      an identity check on the function object would pass for a wrapper that reimplemented the body
      underneath.

    `draft_cause` is the ONE implementation (invariant 11): `schema._not_ok_problem` (the
    human/judge-visible problem line) and `rl_export.run_metrics` (`draft_validator_rejects` /
    `draft_endpoint_errors` / `draft_circuit_breaks`, disjoint and summing exactly to the
    `draft_not_ok` aggregate) both call it, and neither derives anything itself. Never reintroduce a
    per-call-site derivation, and never invent a parallel cause vocabulary — the set is rlm-kit's,
    and it is CLOSED.

    **`rubric.trace_facts`'s `any_circuit_broken` stays a direct `circuit_broken` read, and folding
    it in would be wrong.** It asks a different question — "did the breaker trip anywhere in this
    run", a run-level existence check over one field — not "which one of four outcomes was this
    call". A comment at the fact says so, so a future reader does not "finish the job" by routing a
    TA fact through a precedence order it has no stake in.

    The deliberate exception stands: `AssembledCandidate.draft_ok` and `run_labels`'s
    `n_draft_not_ok` are cause-BLIND, because they answer "did this call yield usable bytes", which
    is the same answer either way — so their docstrings say THAT and never mention the validator. Use
    `rubric.py`'s TA vocabulary ("tripped the circuit breaker") wherever the breaker is described, so
    the surfaces agree. Two surfaces still say "format check" for a cause-blind `draft_ok is False`
    (`apply._blocking_problem` and the studio's `applyBlocker`) — that is currently harmless because
    `assemble` always attaches the cause-naming `problems` line and both check `problems` FIRST, so
    the string is unreachable for real assembled data; if either ever becomes reachable, it needs the
    cause too, and `AssembledCandidate` would have to carry it.

## Versioning

- Keep `pyproject.toml` `[project].version` and `ctx_distillery.__version__` in sync — pinned by
  `tests/test_public_api.py::test_version_matches_pyproject`. On a bump, fold the release's changes
  into `CHANGELOG.md` (under the new version).
- **The two workspace members carry their OWN `version`** (`eval/pyproject.toml` and
  `studio/pyproject.toml`, both `0.1.0` today) and **nothing checks them** — no test, no CI step, and
  nothing compares them to the root's. Each member DOES expose its own `__version__`
  (`eval/ctx_distillery_eval/__init__.py`, `studio/ctx_distillery_studio/__init__.py`), so a check
  is writable; none exists. They are independent numbers; don't
  assume bumping the root moved them, and don't assume they must move together.
- **0.1.0 IS NOT CUT, and cutting it is the OWNER'S call, not a tidiness move.** `CHANGELOG.md` has
  `## [Unreleased]` as its ONLY version heading. When a version is cut it is a RENAME of that heading
  plus a fresh empty `## [Unreleased]` ABOVE it — never a section added underneath a shipped one,
  which would file the project's whole history under a version that never shipped.

  **It was cut once, prematurely, and reverted; the reasoning error is worth keeping.** The argument
  ran: this file is bloated → the bloat is one uncut `[Unreleased]` → so cut it. Every step is true
  and the conclusion still did not follow, because a release heading is a claim about the SOFTWARE,
  not a filing convention for its notes. The project had not reached a working first version, so the
  heading asserted something false; a `v0.1.0` tag added to stop the doc "lying" only made the false
  claim harder to retract. **A version number is the owner's statement that something is usable —
  never infer it from the state of the changelog.**

  What DID survive, and was the actual fix: collapsing claim-and-correction pairs. While
  `[Unreleased]` is the only heading, an entry and a later one correcting it are two drafts of the
  SAME release note, and carrying both is what took this file to 27 lines per entry against the
  siblings' 8–14. State the final position once. That is also why a wrong claim in `[Unreleased]` is
  CORRECTED IN PLACE — the supersede-don't-edit rule applies to shipped sections, and there are
  none.

## Known simplifications (stated, not hidden)

- **A rendered transcript is roughly 85% USER text, and the assistant's THINKING is not available
  at all.** "Deliberately LOSSY" (invariant 6) understates what that costs on real data, and the
  ratio is worth knowing before reading any plan this project produces. Measured on one real
  180 KB transcript, by block type:

  | assistant block | raw JSON | rendered | kept |
  |---|---|---|---|
  | `text` | 25,469 | 23,863 | 93.7% |
  | `tool_use` | 66,152 | 2,080 | 3.1% — becomes `[used tool: X]`, by design |
  | `thinking` | 299,839 | **0** | **0%** |

  Net: user 153,285 chars vs assistant 27,443 — while the RAW content is the other way round
  (305,816 vs 392,030). Two separate causes, and only one of them is ours:

  * `tool_use` collapsing to a label is this renderer's deliberate choice, and it is most of what an
    agent transcript's assistant side consists of.
  * **`thinking` renders to nothing because Claude Code does not persist the thinking TEXT.** The
    block is present and carries `type` + `signature` + `thinking`, but `thinking` is the EMPTY
    STRING and the ~1,840-character `signature` is a crypto signature, not prose. Checked across 60
    session files: **2,384 thinking blocks, 0 with content.** `_render_block` returning `""` for
    them is CORRECT — do not "fix" it, and do not reach for `signature`.

  This was found by a user asking why the transcripts looked like only their own messages. The
  immediate cause of that impression was different again and worth recording so it is not
  re-diagnosed: the planner's own turn-0 code is
  `print("\n".join(t.split("\n")[0] for t in transcripts))`, which prints each transcript's FIRST
  LINE — and a conversation's first line is always `user:`.

- **`read_memory_file` reads through `ArtifactRef.path` directly**, not through a fourth adapter
  method. The ABC answers "what exists" and "give me everything", not "give me one body on
  demand"; every in-scope harness is a local filesystem, so a plain read of the enumerated,
  already-resolved path is honest. Whether a future non-filesystem harness needs a different read
  seam is deferred to when that harness is actually designed.
- **Subagent-transcript distillation is BUILT, and OPT-IN.** This bullet used to say "transcript
  discovery reads the MAIN THREAD only … a real deferred extension: the same file shape, a different
  glob. Not built." Four things in it were wrong and the corrections are the useful part. Subagent
  transcripts live at
  `~/.claude/projects/<sanitized>/<session-id>/subagents/**/agent-<agent-id>.jsonl` —
  **recursively**: directly under `subagents/`, and nested under `subagents/workflows/<run-id>/`
  (claude-agent-sdk 0.2.116, `_internal/session_import.py:89-94` and `_internal/sessions.py:1210-1238`
  — FIRST-PARTY, not inferred from this machine). So the OLD PATH omitted the `<session-id>/` level
  AND the nested case; "a different GLOB" is false twice over (it is a recursive WALK, and the
  shipped renderer returned **0 characters** on all 874 real subagent files, because `isSidechain`
  filters every event); and each file carries a sibling `<stem>.meta.json` whose only REQUIRED keys
  are **`agentType` and `spawnDepth`** — `description`, `toolUseId`, `parentAgentId`, `model` and
  `stoppedByUser` are OPTIONAL and really are absent (every nested file, 299 of 874, carries the two
  required keys and nothing else), so degradation is per-FIELD, never per-file. `journal.jsonl` also
  lives under `subagents/` and is NOT a transcript; the `agent-` filename filter is what excludes it
  (copying the SDK's store-MIRRORING helper instead of its transcript-reading one would ingest all
  nine). `subagent_files()` discovers them; `for_project(..., include_subagents=True)` and
  `ctx-distillery distill --include-subagents` ingest them, each as its OWN `transcripts[]` entry
  with a 3-line header whose line 0 is a short index line. Default OFF, deliberately: `transcripts`
  is positional, so flipping it renumbers every entry and `read_transcript_chunk(3, …)` names a
  different conversation before and after; and shipping ~1.5x more text (up to 18.5x more ENTRIES)
  to a remote model is an operator's act. **Still not built: a SUBAGENT'S OWN nested storage** —
  none was observed, and every nested file on the measured corpus is depth 1 with no `parentAgentId`.
- **The project-scoped skills location is CONFIRMED** (empirically, via a real control experiment),
  with real precedence/timing caveats to respect: a GLOBAL skill of the same name SHADOWS a project
  one (`make_skill_validator` and `apply_plan`'s `_promote_skill` both refuse a project-scope name a
  global skill already holds, hard, with no `overwrite` bypass), and a project's very FIRST
  top-level skills directory needs a Claude Code restart before it's discovered (`apply_plan`'s
  outcome says so explicitly for that case). `<project>/.claude/skills/` is where this project
  writes a project-scoped promotion, and it is now known to be picked up, subject to those two
  caveats — not merely "believed to belong there."

  **Two further loading facts from the same research, neither built on, both recorded here because
  they were about to be lost with the design document that held them.** (a) A project skill loads
  from `.claude/skills/` in the CWD *and every parent directory up to the repo root* — an upward
  walk, so a skill installed in a parent of where Claude Code was started is still in scope. This
  project always writes to `<project>/.claude/skills/` and never relies on the walk. (b) A project
  skill's `allowed-tools` frontmatter only takes effect once the workspace-trust dialog has been
  accepted. Irrelevant to DRAFTING the file — `draft_skill_file` authors a body and nothing more —
  and worth knowing for the human who APPLIES it, which is why it belongs beside `apply_plan`'s
  other caveats rather than in the drafting tool's docs.
- **A skill's `references/` and `scripts/` are BUILT, and this bullet used to say the opposite.**
  It read *"out of scope... `draft_skill_file` authors the `SKILL.md` body only, and `apply.py`
  writes only that one file"* — both halves are now false. `draft_skill_extra_file` (the sixth
  read-only tool, invariant 1) drafts one supplementary file per call, sharing the `artifact_id` an
  earlier `draft_skill_file` call minted; `schema.assemble` gathers them per `promote_to_skill`
  candidate keyed by `relative_path` (`AssembledCandidate.extra_files`); `apply._promote_skill`
  writes `SKILL.md` and then each supplementary file, behind its own containment check (invariant
  9). Two things deliberately NOT built, named rather than left silent: no cross-check that a
  drafted `SKILL.md`'s prose actually matches what was drafted via `draft_skill_extra_file` (a model
  can write "see `scripts/setup.sh`" without ever drafting that path, and nothing catches the
  mismatch), and no live registry validating that `draft_skill_extra_file`'s `artifact_id` argument
  actually corresponds to a real prior `draft_skill_file` call (a typo'd id is a silently orphaned
  `tool_call`, never written and never an error — harmless blast radius, not solved for v1).
  `render.render_plan` shows each supplementary file beside its candidate (so `ctx-distillery show`
  and the eval judge both see them, invariant 11), and `render.plan_as_dict` — plain
  `dataclasses.asdict` — carries `extra_files` into `ctx-distillery show --json` and the studio's
  `GET /v1/runs/{id}` automatically. The studio's PLAN panel does not render them VISUALLY yet
  (`app.js` has no UI for browsing multiple files per candidate) — the data is reachable over the
  API; a real browsing UI is a follow-up, not a requirement for the planner to propose them at all.
- **Skill enumeration is opt-in on the explicit constructor.** `ClaudeCodeAdapter(memory_dir)` (what
  `apply.py`'s re-scan builds) enumerates no skills at all; pass `global_skills_dir=` /
  `project_skills_dir=`, or use `for_project`, which resolves the real roots. Deliberate: a bare
  adapter silently reaching into a real `~/.claude/skills` would make the re-scan machine-dependent.
- **The CLI is deliberately small: `distill`, `show`, `export`, and `apply` (in its own binary).**
  No `--interactive` per-candidate approval walk (the `show` → `--approve` → `--confirm` loop is
  complete and scriptable without a TTY surface) and no `purge` (see the archive bullet below). Each
  is real additional scope, not a missing polish pass. **`export` has no `--out` and `rl_export.py`
  has no `main()`** — all three sibling projects' exporters end in `open(out, "w")`, and both modules
  sit inside invariant 1's mutation scan, so the bundle is printed to stdout with
  `print(json.dumps(...))` and redirected with `>`. Note the form: `json.dump(..., sys.stdout)` also
  passes the textual scan but calls `.write` at runtime, which is evading the tripwire rather than
  satisfying it. `export` REFUSES an empty glob match instead of printing a zero-run bundle.
- **`rl_export.run_labels` is STRUCTURAL, and that boundary is the whole design of it.** An earlier
  reading declined a labels surface outright, citing "there's no obvious reward signal for *was this
  the right thing to prune*". That is correct about an ORACLE and wrong about everything else — it
  cited `toolscout`'s model-decided `met` booleans, which live in `rubric_signal`, not `run_labels`;
  and `diff-sentry`'s and `toolscout`'s actual `run_labels` are purely structural and map onto
  `AssembledCandidate`'s real fields one-for-one. So `run_labels` counts only what
  `schema.assemble()` already established (`finalized`, the action histogram, `n_unbacked`,
  `n_draft_not_ok`, `plan_problems`) — every field recomputable from the same JSONL by a second
  reader. Only `cve-reverser`'s `valid`/`complete` is oracle-flavoured, and its domain has ground
  truth; ours does not. Never add a field that claims a judgement was CORRECT. `rlm_kit.dataset`'s
  `run_label_bundle` refuses a surface literally named `reward` (it raises), so the reward-free
  property is enforced at the transport, not by convention here.
- **The DRAFTER may never ride the Claude subscription, and `from_env` refuses it UNCONDITIONALLY.**
  `CD_ROOT_LM` / `CD_SUB_LM` accept the `claude-agent-sdk/<id>` sentinel (`config.SUBSCRIPTION_PREFIX`
  → `config._maybe_subscription_lm` → `rlm_kit.configure(main_lm=, sub_lm=)`, the `[subscription]`
  extra); the drafter cannot, because `config.make_chat_fn` builds an `openai.OpenAI` client directly.
  The gate is unconditional rather than defensive for two compounding reasons: BOTH drafting tools are
  ALWAYS wired in `DistillSession.__init__` (so a sentinel there fails LATE, mid-trajectory, on the
  single hard-budget attempt), and `draft_model` falls back to `sub_model` which falls back to
  `main_model` — so setting only `CD_ROOT_LM=claude-agent-sdk/…`, the most natural way to try the
  subscription path, silently hands the sentinel to the drafting endpoint as a model id. The error
  distinguishes *explicitly set* from *inherited* (and names WHICH variable it was inherited from),
  because the fix differs. `config.py` must stay dspy-free AT MODULE LEVEL — the
  `from rlm_kit import ClaudeAgentLM` lives inside the sentinel branch, and both
  `tests/test_public_api.py` and `tests/test_subscription.py` assert the module top in a FRESH
  interpreter. `studio/app.py` needs no mirrored prefix: its `/v1/config` reports no model at all.
- **`apply_plan` is still callable directly from Python**, and `ctx-distillery-apply` is a thin
  layer over it — the CLI only knows how to derive Claude Code's roots from a `--project` path. Point
  at an unusual layout by calling `apply_plan(memory_dir, plan, approved_ids, ...)` yourself.
- **No adapter for any harness other than Claude Code.**
- **`apply.py` archives, and nothing purges.** A pruned file is moved to
  `_ctx_distillery_archive/`, never deleted; deleting the archive for real is a separate, explicit
  `purge` operation that does not exist yet. That is deliberate — "still recoverable" beats
  "irreversible" even at the human-approved step.
- **`apply_plan` only knows the Claude Code layout** (it builds a `ClaudeCodeAdapter` directly, and
  its per-kind roots are Claude Code's). Generalising the apply step across harnesses waits for a
  second adapter to actually exist.
- **`ctx_distillery/rubric.py` sources 100% of its facts from `session.assemble()`'s output, and
  `eval/` (`ctx-distillery-eval`) never writes and is never imported back.** The rubric is
  deterministic, reward-free ATLAS (TF/TA/TG/PA) facts, built on `rlm_kit.rubric` — it never decides
  met/unmet, and no field anywhere functions as a score. `eval/` is a SEPARATE workspace member: a
  static, offline LLM-as-judge that reads the assembled plan + the transcript(s) it was drawn from
  as TEXT only (never executes anything, never touches `apply.py`), and `ctx_distillery` itself
  must NEVER import `ctx_distillery_eval` back (test-enforced, `eval/tests/test_boundary.py`). The
  eval CLI's transcript path(s) are MANDATORY and must be non-empty — `_read_transcripts` refuses an
  empty or whitespace-only file loudly (`SystemExit`), because a real judge would otherwise silently
  score a plan against nothing. The now-public `rubric.plan_from_events` (see invariant 11 — `eval/`
  and `studio/` both call it rather than keeping their own copy) must degrade to `None` on a
  malformed `result` payload rather than raise — `assemble()`'s own stated philosophy is "none of
  them raise," and a malformed shape must fail the same way, never crash a batch scoring run (or a
  studio replay) over one bad trace.
- **The eval judge is LIVE iff `CDEVAL_MODEL` is set, and an unscored row is NEVER a fake 0.** The
  `judge = ["openai>=1.0"]` extra used to be dead — nothing imported it — so `judge.make_eval_judge`
  now implements it on `rlm_kit.tools.make_model_tool`, with `from openai import OpenAI` LAZY inside
  the chat closure (`eval/tests/test_boundary.py` asserts in a FRESH subprocess that importing the
  eval CLI pulls neither dspy nor openai; hoisting that import turns it red, and
  `eval/tests/test_judge.py` re-asserts it against the module's own AST). `max_retries=0` on the
  client is deliberate: `make_model_tool`'s transient-retry loop owns retries, so leaving the
  client's own retries on would multiply the two and turn a hard 60s timeout into minutes. Three
  shape changes came FIRST and are the load-bearing half: `Judge` returns a
  `JudgeVerdict(ok, score, reason)` (the only way to distinguish circuit-broken / endpoint-error /
  off-schema), `EvalRow.score` is OPTIONAL beside a REQUIRED-when-unscored `unscored_reason`
  (`compute_means` drops such rows from the sum AND the denominator — counting them is arithmetically
  identical to scoring 0), and `EvalReport` carries `n`/`n_unscored`/`judge_model`/`prompt_version`.
  Build ONE judge per batch: the circuit breaker lives in the closure. `CDEVAL_*` is a SEPARATE env
  surface from the root's `CD_*` on purpose — the judge must be pointable at a different model than
  the run it scores.
- **The eval member now has BOTH `score` and `run`; the three former blockers were closed
  ADDITIVELY, and each fix's shape is the part worth keeping.** (1) `taskset.py` carries BOTH
  concepts side by side — `Task`/`collect_tasks` (from TRACES, what `score` was always built on) and
  `EvalTask`/`load_taskset`/`demo_taskset` (the siblings' checked-in list to DRIVE). Neither replaced
  the other. (2) `judge.build_prompt` grew a third positional `reference` slot and `PROMPT_VERSION`
  bumped to `atlas-ctxd-eval-v2`, which is exactly what the constant is for; the `=== REFERENCE ===`
  section renders ONLY when `reference` is non-empty — a divergence from all three siblings'
  unconditional `"(no reference provided; …)"` fallback, because theirs ALWAYS have a taskset and
  this project's primary path (`score` with no `--taskset`) does not, so a no-reference run must keep
  rendering the byte-identical v1 prompt. When the section IS rendered, `REFERENCE_TRUST_RULE` is
  appended to `UNTRUSTED_DATA_RULE` (which enumerates exactly two untrusted bodies), stating that a
  taskset reference is TRUSTED input — a human wrote it into a checked-in file — while still not a
  licence to change the scale or output format. (3) `session.run_distillation_artifacts` returns a
  `DistillArtifacts(plan, events, run_id, trace_path, transcripts, memory_index)` where
  `transcripts` are the REDACTED texts the run actually saw, and `run_distillation` became a
  one-line wrapper with its signature AND return type unchanged (zero call-site edits). Those fields
  deliberately do NOT go on `AssembledPlan`: `render.plan_as_dict` is `dataclasses.asdict`, so
  transcript bodies would land in `ctx-distillery show --json`. Three properties of `run` that are
  NOT copied from the siblings and must not be "fixed" back: no `os.remove` of a stale trace (the
  FILENAME is `<slug(task.id)>-<UTC stamp>.jsonl`, unique per invocation, while `run_id` stays `task.id`
  for the pairing), everything under `--out`, and a failing task becoming an `unscored` ROW rather
  than a `SystemExit` that aborts the batch. `demo_taskset(root)` MATERIALIZES under a
  caller-supplied root (a `~/.claude` stand-in is machine-dependent, so no sibling's static-JSON
  form is possible) — layout only, with the transcript CONTENT staying checked in as
  `eval/ctx_distillery_eval/demo/*.jsonl`. Because `eval/` now imports product code to drive it,
  `eval/tests/test_boundary.py` gained an AST assertion that no eval module imports
  `ctx_distillery.apply` — the root package's own tripwire scans `ctx_distillery/` only.
- **Three more simplifications are `studio/`-only and live in `studio/CLAUDE.md`**, moved there for
  the same reason invariant 10's body was: they apply to that directory and nothing else, so an
  always-loaded root file was the wrong place to charge every session for them. They are the
  un-vendored JetBrains Mono font stack (plus the MONO-ONLY type decision and the deliberately
  unbuilt replay transport), the drawer's UNSCRUBBED turn text (`textContent` rendering IS the
  mitigation), and `studio/DESIGN.md`'s status as a VISUAL & UX spec rather than an architecture
  doc. One of them is cross-cutting and repeated here because it binds a root module: `DESIGN.md`'s
  `blocked` frame state mirrors `apply.py::_blocking_problem` exactly — if that function's refusal
  set changes, the frame, `app.js`'s `applyBlocker()`, and §2's table move together or the console
  starts lying about what the apply step will accept.

## Harness scope

Claude Code is the only adapter being built — it's the only platform whose real persistence
format has been directly verified. Codex, Hermes, OpenClaw, and OpenCode are named future
targets, deliberately **not** designed yet: their real on-disk formats haven't been inspected,
and guessing one would be speculation dressed as design. Don't add an adapter for any of them
until someone has actually looked at that harness's real format.
