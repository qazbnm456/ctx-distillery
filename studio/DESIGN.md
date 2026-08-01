# ctx-distillery-studio: visual & UX spec

The web frontend's design contract. Implementation (`static/{index.html,app.js,trajectory.js,style.css}`)
follows this file. **Architecture is locked in [`README.md`](README.md)** — the six endpoints, the SSE
event mapping, the replay-only scope decision and its reopening conditions, install/run, and every
deferred item. This doc owns the *look and feel* only.

(Context worth stating once: this repo deliberately purged its project-level design blueprint. This
file is a different species — a visual spec, the one design-shaped file that IS a family convention
across `cve-reverser` / `diff-sentry` / `toolscout` — and it must never grow back into an
architecture doc.)

## 1. Theme

**A distillation review console.** An instrument for ONE decision: a human is about to prune and
promote pieces of their own Claude Code history, and this console is the last surface they read
before calling `apply_plan` themselves, by hand, outside any web request. The stakes shape the
design — pruning is irreversible in intent (`apply.py` archives rather than deletes, but the review
is where a wrong call gets caught), so the console's job is not to look confident. It is to show
**what the plan claims, what the trace actually backs it with, and which candidates the apply step
would refuse on evidence the trace already carries** (§2 is explicit that this is a subset of every
refusal, and why the rest are not knowable here).

Teal signal-light on deep slate, sharp 2px geometry, mono-forward type. Energy: focused,
instrumented. Not playful, not corporate.

Utility mode (no marketing hero): orient (header) · input (Load a run) · status (Replay feed) ·
result (PLAN stage + rubric facts).

## 2. The signature: the plan's CLAIM ≠ the drafted BYTES (made visual)

ctx-distillery's load-bearing invariant (`CLAUDE.md` **invariant 2**) is that a plan candidate
carries only `{action, artifact_id, key_fields}` — **never** the drafted content. The actual
markdown+frontmatter body is authored by `draft_memory_file` / `draft_skill_file` and recorded as a
`tool_call` event; `schema.assemble` re-sources it **on read** by matching that event's
`artifact_id`. A label can therefore never be trusted to describe its own bytes — **label ≠ bytes**
is this project's whole reason to exist, and the console must make it legible.

So the frame is keyed to the state `assemble()` DERIVED from the trace, never to the plan's own
claim about what it drafted. The three derived states use only real `AssembledCandidate` fields
(`action` · `artifact_id` · `key_fields` · `draft` · `draft_ok` · `problems`):

| derived state | frame | when |
|---|---|---|
| **blocked** | `--bad` (full frame) | a refusal decidable FROM THE TRACE — `problems` non-empty, **or** `draft_ok is False`, **or** a promotion whose assembled `draft` is empty/whitespace, **or** a `promote_to_skill` whose `key_fields['scope']` is not `"global"`/`"project"`, **or** a `prune` naming no `key_fields['target_path']` |
| **backed** | `--signal` left edge | a `promote_to_memory`/`promote_to_skill` whose `artifact_id` resolved to real drafted bytes and is not blocked |
| **inert** | `--border-strong` left edge | `keep` / `prune` — there is nothing to back, and claiming otherwise would be theatre |

**`blocked` is not a UI opinion — every condition in it is one `apply_plan` really refuses on.**
Three come from `apply.py::_blocking_problem` (the checks the writer re-runs regardless of action
kind); two more mirror the per-action-kind `key_fields` conventions `_promote_skill` and `_prune`
enforce. `app.js`'s `applyBlocker()` is that mirror.

**But red is a SUBSET of refused, not an equality — and saying otherwise would be the exact kind of
false confidence §1 says this console must not project.** `apply.py` refuses at 29 sites. The five
above are the ones a FINISHED TRACE can decide; the rest cannot be known here at all, because they
depend on the fresh `list_targets()` re-scan `apply_plan` performs at write time, against a store
that may have changed since the run: a slug that now collides, a draft whose frontmatter carries no
usable `name`, a project-scope skill a global one now shadows. A studio reading a trace file has
none of that state, and inventing it would be worse than omitting it. So: **red means the writer
will refuse this. Absence of red does not mean it will accept.** The apply step's own per-candidate
outcome is the authority, and it is the reason `apply_plan` reports one for every candidate.

The third condition is why this is a derived function and not just `problems.length`. **An empty
promotion draft carries no `problems` and can even report `draft_ok === true`** — a promotion whose
label survived while its bytes did not. Before this spec, that candidate rendered as an ordinary row
with a missing `<pre>` and NO marker: the single case a reviewer most needs to see was the one the
console said least about. A blocked row now also carries a `⚠` refusal line, LAST, so it reads as the
verdict on everything above it. **Flagged, never silently dropped** — a broken candidate stays on
screen, because a reviewer's question is "what is wrong with this plan", and a hidden row answers it
with silence.

## 3. Palette

Dark default, with a **light theme** toggled from the header (`[data-theme="light"]` overrides the
tokens; persisted in `localStorage` under `ctxd-studio-theme`, and a `prefers-color-scheme: light`
media block covers the un-toggled first visit). The values below are design intent; the **live tokens
are `style.css :root` + `:root[data-theme=light]` — source of truth.** The base slate/surface ramp is
`diff-sentry-studio`'s, carried verbatim (same visual family, deliberately); the accent is ours.

```
--bg:#0a0e13  --surface-1:#121a24  --surface-2:#1a2431  --surface-3:#232f3e
--border:#313e4d  --border-strong:#45566a
--text:#e8eef4  --text-dim:#a2b4c4  --text-faint:#6a7c8d
--signal:#22d3c2  --signal-dim:#3fb3a8  --signal-glow:rgba(34,211,194,0.24)   /* THE brand accent */
--ok:#3fb950  --bad:#f85149  --warn:#d29922
```

Rules. `--signal` (teal) is for interactive + "backed" things: the Load button, input focus, the
promotion action pills, the rubric category labels, the **backed** frame edge. `--bad` is the refusal
color and is confined to failure — the **blocked** frame, `chip-bad` / `problem-line`, the feed's
`fam-bad` row edge, `run-problems`, and
the `⚠` refusal marker. `--warn` (amber) marks a `prune` action pill, because prune is the one action
that removes something. `--ok` marks a successful drafting call (`chip-ok`, and a `distill.draft.created`
feed row's left edge). Do not cross-use: **the frame carries derived state and nothing else.** Nested
surfaces step (`bg` → `surface-1` → `surface-2` → `surface-3`), each with a 1px `--border`.

The §5.7 drawer reuses the same three, unchanged in meaning: `--signal` for the `◫` handle mark, the
`Trajectory` tag, and a `.related` cross-highlight; `--bad` for a failed timeline entry's left edge
and a sub-LM `error` line; `--warn` for an `unrecognized` tool's edge, because an unknown tool in a
CLOSED tool set is a "look at this", not a failure. It introduces **no new token**.

*Not copied, and the reasons are real:* there is **no** multi-stop metal "alloy" gradient here.
diff-sentry's verdict alloy and toolscout's grounding alloy encode a 4–5 way verdict axis; ours is a
three-state derived fact, and a gradient would be decoration dressed as information.

## 4. Typography

**Mono, everywhere — `--mono` on `body`.** That is a divergence from the siblings' mono-frame /
sans-prose split, and it is deliberate rather than unfinished: this console's "prose" is a drafted
**memory/skill file** — markdown with YAML frontmatter, whose indentation, `---` fences and key
alignment are *structure a reviewer is checking*, not paragraphs they are reading. Setting it in a
proportional face would misrepresent the artifact.

`--mono` is `"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` — it
**PREFERS** JetBrains Mono (matching the sibling studios' visual family when the visitor already has
it) and falls back to the platform stack. **No font binary is vendored** (`static/vendor/` does not
exist), a known simplification recorded in `CLAUDE.md`: checking a woff2 pair into a brand-new
package to match a sibling asset-for-asset is bloat, not parity. Consequence to respect: the page
must never block on a font, and the fallback must look intentional at every size used here.

Hierarchy comes from SIZE + WEIGHT + COLOR instead of family: `1.25rem/700` wordmark ·
`.82rem` primary lines · `.76rem` reasoning and problems · `.7–.74rem` uppercase labels, chips and
fields, in `--text-faint`.

## 5. Components

### 5.1 Header
`▣ ctx-distillery studio` wordmark (`▣` in `--signal`, "studio" in `--text-faint`). Sticky, 56px,
one hairline bottom border. Right side: **the theme toggle (`☾`) and nothing else.**

*The `TRACES` chip was removed, and the reasoning generalises.* It was a DIAGNOSTIC — it answers
"why is my run not listed", a question asked about once per session — occupying the most valuable
strip on the page permanently. Worse, the slot was copied from the siblings, where it holds a MODEL
NAME: short, and identified by its head. This one held a PATH, so `max-width:22ch` truncated it to
`/Users/operator/Documents/…`, dropping the only segment anyone reads a traces path for. **A borrowed
component carries the content-shape assumptions of the thing it was borrowed for.** It now lives in
§5.2, at the point of use.

### 5.2 Load box (left rail, top)
`▾ LOAD A RUN`: a `datalist`-backed input over `GET /v1/runs` plus a `Load ▶` primary button. Enter
submits. Below it a one-line hint carrying the RUN COUNT alone, and below that the traces directory
as its own labelled, WRAPPING block (`.tracesdir`, hidden until known).

*Two decisions, and the second overrules the first.* The location reads best folded into the count
as one sentence, and `homeRelative()` keeps it short by folding `$HOME` to `~` (which also stops a
screenshot leaking the operator's username). But a traces directory OUTSIDE `$HOME` — a temp dir, a
mounted volume — runs past a hundred characters, and inside a sentence in a 320px rail that is
unreadable however it is folded. So the sentence keeps the count and the path gets a block that can
wrap; the unfolded value stays on `title` for a copy-paste.

*The two loaders are SEQUENCED* (`loadConfig().then(loadRunsList)`), not fired together. The block is
rendered from `TRACES_DIR`, which the other fetch sets, and nothing re-renders it — two unawaited
requests racing over one piece of state, where losing is silent and permanent. Both are tiny and
local, so config usually wins and the bug would only appear somewhere else.

### 5.3 Replay feed (left rail, fills remaining height)
`▾ REPLAY FEED` + a status word (`replaying…` → `done`, or `connection closed`). A scroll container,
newest row at the BOTTOM, appended per SSE event, scrolled to the bottom on each append. Each row: a
mono `.fr-badge` pill carrying the event name, then a body — `.fr-line` (primary), `.fr-reasoning`
(pre-wrapped, `--text-dim`), `.fr-fields` (`--text-faint`). A 2px left edge tints per family: default
`--border`; `--ok` / `--bad` on a `distill.draft.created` row per its `ok`.

The seven mapped families (`mapper.to_event` is the source of truth):
- `distill.run.created` — `transcripts` · `memory_artifacts` · `rubric_criteria`. When the run
  recorded a transcript index, `transcripts` carries its composition too — `12 (sessions=3
  subagents=9)` — so a reviewer can see at a glance whether subagent transcripts were in scope.
- `distill.plan.step` — **the planner's own reasoning turn**: `turn N (wrote code)` + the reasoning
  text. For a judgement-only task with six tools this is plausibly the richest content in the trace.
- `distill.sub_lm.call` — a recursive sub-LM escalation, `in:` / `out:`
- `distill.evidence.read` — one of the three read-only lookups + its short scalar fields
- `distill.draft.created` — `<tool> -> artifact_id=…` + `ok` / `circuit_broken` / `errors`. **No draft
  body here** — the bytes belong beside their plan entry (§5.4), not in a scrolling log.
- `distill.plan.done` · `distill.run.completed` — terminal; `completed` flips the status to `done`,
  closes the `EventSource`, and triggers the PLAN fetch.

**It is a REPLAY, and the label says so.** The panel used to read "Live feed" with a `streaming…`
status, which promised a capability the backend does not have; renamed, and the rename is recorded in
`README.md`. `?delay=` only PACES a finished trace to feel live. **Carry the real ordering caveat:**
`main_step` events flush POST-HOC with trailing `step_id`s, so a replay sorted by `_step_key` streams
the run's ACTIONS BEFORE the reasoning turns that produced them. Do not design a causal reading of
this feed (no connector lines, no "because of the turn above"); it is a log, not a narrative.

*Not copied:* no "the live feed streams as it happens" claim, and no per-family inline-SVG icon chips
(the badge carries the event name as text — there is no inline SVG anywhere in `index.html`).

### 5.4 The result: candidate LIST (rail) + PLAN stage + meta modules
Page-level **3 columns**: `320px` rail | `minmax(0,1fr)` stage | `300px` meta, `height:
calc(100vh - 56px)` so the page itself never scrolls.

**One scroll track per panel, never two.** A `.panel` is a flex BOX; the scroll belongs to exactly
one designated child (`.feed`, `.cand-list`, `.stage-body`, and `.meta-col` is its own track). This
is the discipline every sibling studio uses and the one this file had dropped: `.panel {
overflow-y:auto }` *plus* an inner `overflow-y:auto` gives two nested scrollers per column, and the
wheel hands off mid-gesture — the reader is thrown past what they were reading. Every track carries
the top/bottom `mask-image` fade, which is what distinguishes a list that ENDS from one that
CONTINUES. Pinned by `static-contract.test.js`.

**Rail, middle — `▾ CANDIDATES — TICK WHAT TO APPLY (n)`.** The title names the panel's JOB, not its
contents: this console exists to get a reviewer to a correct `ctx-distillery-apply --approve …`, and
"Candidates (10)" left them to guess the verb.

One `.cand-item` per `AssembledCandidate`, carrying **two controls because there are two verbs**: a
checkbox that puts it in the apply command, and the rest of the row, which opens it on the stage.
One control doing both is how a reviewer applies something they only meant to read. The row shows
action · the artifact's short name · one glyph for the only thing a list is scanned for (`⚠` refused,
`◆` backed). The left stripe repeats the §2 frame state **from the same derived value the stage
uses**, so rail and stage cannot disagree. `↑`/`↓` step through the VISIBLE rows (see the filter
below); the selection scrolls into view.

*The zero-based INDEX is not shown, and that is the point.* It is what `--approve` consumes, so
renumbering it from 1 would break the mapping — but nobody needs to read it either, because the
command below is assembled from the ticks. Printing it only invited "why does this start at 0" for
a number nobody types.

**The apply command** (`.apply-cmd`) assembles under the list from the ticks, with a copy button:

    ctx-distillery-apply <traces>/<run-id>.jsonl --project . --approve 0,5

**Without `--confirm`, deliberately.** That flag is what writes, and a copyable one-liner that writes
on first paste would make this console the thing that applied a plan — which invariant 8 says it must
never be. The note says it is a dry run, and names the project directory to run it from (the
BASENAME, via `iterations._project_label`, whose docstring carries the reason the full path is never
surfaced): `--project .` pasted in the wrong directory writes into the wrong project.

**`keep` is not tickable, and neither is a blocked candidate.** `apply_plan` returns `STATUS_NOOP`
for a keep — "there is nothing to apply" — so a tick for it invents an action the writer does not
have, and the word already means "leave this alone". Both cases DISABLE the box rather than hiding
it, with the reason on the row: a row that silently cannot be chosen reads as an oversight. The
predicate is `notApplicable()`, keyed on what `apply_plan` would actually DO, which is the only
honest basis for offering the control.

**The transcript filter.** Clicking a transcript in the stage's EVIDENCE zone narrows the list to
candidates citing it; several can be active at once and they combine with **AND**, stated in the chip
(`citing BOTH …` / `citing ALL 3 …`). AND rather than OR because on real data each transcript is
already cited by most of the plan (9, 7 and 4 of 10 candidates on one run), so OR broadens to
everything and discriminates nothing — while AND answers the question the planner's own instructions
care about: which candidates draw on more than one conversation.

*A filter is a way of LOOKING, not a way of choosing.* Ticks and the open candidate survive it, or
the apply command would change as the reviewer changed where they were looking. That leaves one
hazard, so the chip says it outright: `— 2 ticked candidates hidden`, in `--warn`.

*Why a list and not the old inline stage.* The middle column used to render every candidate with its
drafted body expanded. Measured on a real run: **32,091 characters of draft across 10 candidates**,
each `<pre>` capped at 260px with its OWN scrollbar, inside a panel that also scrolled — so reading
candidate 5 meant scrolling the panel to it, scrolling inside its box, and being thrown to candidate
6 the moment the wheel escaped. List here, one detail there: the siblings' rail → stage relationship.

**Middle stage — the selected candidate, one at a time.** Head: `▾ [N] <action>` plus an
`Entry | Draft` switch (hidden when there is no draft). *Draft* view: the drafted bytes in a `<pre>`,
`white-space:pre-wrap`, `overflow-wrap:anywhere`, filling the stage's own scroll track (**no second
scroller** — see above).

*Entry* view is **four ZONES, in the order a reviewer asks the questions** — PROPOSES, EVIDENCE, WHY,
IF APPLIED — with anything unrecognised under OTHER. It was a flat list of `key_fields` rows, which
is the plan's STORAGE shape rather than anyone's reading order, and `key_fields` is **free-form: the
planner invents the keys.** Two real runs proved how far apart they can be: one wrote
`transcripts: [0, 1, 2]`, `reason`, `target_path`; the other invented `sources`, `topic`,
`procedure`, `related_open_item`. So the zones group by the QUESTION a field answers, each field
renders in the shape it deserves, and OTHER exists because a free-form field must never vanish.

* **PROPOSES** is synthesised, not copied: the plan carries an action and an artifact id, never a
  sentence saying what it wants, and stitching those together is this console's job.
* **EVIDENCE** resolves transcript indices to identities via `transcript_index` — `[1]` becomes
  `session b2d5ba2e`, or `subagent a00d251c of 30f8147f`. Until that field existed nothing anywhere
  could map an index back to a file, so `transcripts: [1, 2]` explained nothing at all. Each is a
  CONTROL (see the filter above); the SET one is a filled pill, because the same name appears on
  several candidates and a weight change alone is not legible down a column.
* **WHY** gets prose treatment, because `reason` IS a paragraph. Squeezing it into the value column
  of a key/value row made the most-read field the least readable.
* **IF APPLIED** shows `target_path` as its file NAME, full path on `title`: the verbatim value is
  mostly a home directory, repeated on every row, and identifies the machine.
* Then any per-candidate `problems`, then the `⚠` refusal marker when the candidate is **blocked**
  (§2), LAST, so it reads as the verdict on everything above it.

**Transcript references inside PROSE are linkified too, under a deliberately narrow rule.** The
second run wrote its evidence as `sources: "transcripts[2],[12] (siblings under session 30f8147f,
not independent of each other)"`. A run must OPEN with the word `transcript(s)[N]`; only then do
`,[N]` / `-[N]` continuations join it. A bare `[N]` is never matched — it could be a footnote or an
array index, and **a wrong link is worse than no link: it invites filtering by evidence that was
never claimed.** A range links only the indices actually written (`[23]-[26]` gives 23 and 26, not
24 and 25), and the number is RANGE-CHECKED before it becomes clickable, because it is model-supplied
and a hallucinated `transcripts[99]` must read as the prose it is.

*Use `String.matchAll`, never a shared `/g` regex with `exec`.* The render is re-entrant — linkifying
one candidate's prose asks which OTHER candidates cite the same transcript, scanning their prose with
the same objects — so a mutable `lastIndex` made the outer loop restart forever: a 4 GB heap
exhaustion, not a slow page. Pinned by `tests/app.test.js`.

Written with **`el.textContent` ONLY, never `innerHTML`**: a drafted memory/skill body is untrusted
model output, not markup to render. Absolute across every file under `static/`. It is also why this
studio cannot copy the siblings' stage code, which assembles its markup with template strings.

Run-level `problems` pin under the candidate LIST, not inside the stage — they belong to the run,
not to whichever candidate happens to be selected. Empty states: `Load a run, then pick a
candidate.` → `Loading…` → either the stage or `this run's plan proposed no candidates.`

**Right column — RUN TELEMETRY, then one module per ATLAS category.** `.meta-col` of `.module`
cards, each with the siblings' 3px `--signal → transparent` `.module-cap`, an `<h4>` title, and a
right-side **headline**. **The column collapses entirely** (`.layout.no-meta`) until `#rubric-list`
has children, so an unloaded stage takes the width the metadata does not yet need; the toggle is
`syncMeta()`, called from every place the column's contents change and nowhere else.

*Telemetry sits FIRST*: what the run COST and what it did, which previously lived only inside the
Trajectory drawer — a reviewer had to open a bottom sheet to learn that a 10-candidate plan took five
minutes. An elapsed HEADLINE at display size (the only figure answering "was this cheap or
expensive"), then four counts. Every field degrades to an em dash rather than to zero: this endpoint
answers "what does the trace say", and a trace that says nothing must not render as a run that did
nothing. Each cell carries a styled `.stat-pop` on hover OR keyboard focus — a native `title` arrives
after a second, unstyled and truncated, which for a field whose whole problem is that a bare count
says nothing is too slow to be the fix. `title` stays as the touch and copy-text fallback.

*`.module` is NOT a clipping context.* It carried `overflow:hidden` so the cap's square top corners
were clipped by the module's radius; the cap now carries that radius itself, same result. The clip
was cutting the telemetry popover away entirely. An earlier note credited that `overflow` with
keeping modules from compressing inside the fixed-height column — it does not: `flex-shrink:0` on
`.meta-col .module` is the whole guard, and every module renders into that column.

Below telemetry, one module per category. Body: the criterion's own `description` behind a
`▸ what this asks` disclosure, then one `.kv` row per fact. Two always-open paragraphs above every
card made the column mostly prose, and a reader scanning four cards for four numbers read past all
of it every time; with the row labels saying what they count, the text is reference material.

**Fact rows are LABELLED, not de-underscored.** `key.replace(/_/g, " ")` gives "n non keep", which is
a variable name with the underscores taken out. The siblings use plain nouns for their stat cells
("turns", "servers", "tool calls"), and the harder half here is that stripping `n_` is not enough:
`non_keep` counts "candidates proposing a CHANGE", which its own key never says. The raw key goes on
the row's `title`, for a reader cross-referencing the trace.

**No `.stat` cells in the rubric modules, and that is not an inconsistency.** Telemetry is four
counts and a duration: all magnitudes, all comparable, which is exactly what a cell grid is for. The
rubric facts are heterogeneous, and two of them are ORDINALS — `min_draft_step: 1` is "the first
draft happened at step 1", not a quantity, so a big bold number in a comparison grid states something
false about it, and splitting it from `min_read_step` destroys the only thing TA asks: which came
first. **The same component is right in one panel and wrong in the other, decided by the data's type
rather than by visual consistency.**

*The description is SERVED, never copied.* `GET /v1/runs/{id}` returns `rubric_criteria`, recovered
from the run's own `run_start` meta by `rubric.rubric_from_meta`. Four descriptions transcribed into
`app.js` would drift the moment a criterion is reworded — silently, because nothing compares the
two — and reading it per run means an OLD trace explains itself with the rubric it actually ran
under, not today's.

*Facts with no category land in `outside the four criteria`.* The grouping is a whitelist, so
without a catch-all a fact the server ADDS is silently invisible — which really happened:
`n_transcripts` / `n_transcripts_read` were computed, served, and never rendered. The group is named
after what it IS rather than after today's two members.

**Label them FACTS, never scores, and never state more than the fact measures.** `trace_facts`
decides no met/unmet and no field anywhere is a reward, so a headline may say `tools: read first`
(an observation about ordering) and may never say `good`, a total, a percentage, a bar, or a ✓/✗.
Two headlines had to be corrected against this, and the failure mode is the same both times —
**rendering a count of TOOL CALLS as a conclusion about the planner's behaviour**:

* TA read `draft → read`. Both numbers are step ids of *tool calls*, and the planner also receives
  `transcripts` and `memory_index` as **REPL variables** (they are on `DistillSession`'s signature,
  and `_INSTRUCTIONS` tells it to print and slice them). A real run read all three transcripts in
  full at turns 0–2 by printing the variable and escalating the text to the sub-LM, then drafted at
  turn 3 and called `list_memory_files` at step 9 — tool-wise `draft` first, in substance evidence
  first. The headline now says `tools: draft first`, and a caveat block says the rest.
* The unclaimed group read `0 / 3 read`. The FRACTION was the lie: it frames read-tool calls as a
  proportion of transcripts, asserting the rest went unread, which the trace cannot support. The
  numbers stay as plain rows; the arithmetic is gone.

A caveat block (`.module-caveat`, set apart by a left rule so a reader can tell console text from
rubric text) carries these, never edited into the server's own description.

The PLAN surface answers "what does this run propose, and what backs it". It cannot answer "how did
the planner get there" — that is **§5.7**, the Trajectory drawer, and it is a separate surface on
purpose: a reviewer deciding whether to call `apply_plan` reads the plan, and only sometimes needs
the trajectory behind it.

### 5.5 States (every state explicit)
| derived state | frame | body |
|---|---|---|
| **backed** promotion | `--signal` left edge | action pill · `artifact_id` · `draft ok` chip · the drafted bytes in the `<pre>` |
| **inert** `keep` | `--border-strong` left edge | action pill + `key_fields`; no draft, no chip — nothing to back |
| **inert** `prune` | `--border-strong` left edge, `--warn` pill | `key_fields` carrying `target_path` (the apply step refuses a prune without one) |
| **blocked** (any kind) | full `--bad` frame | everything above, PLUS the `problems` lines and the `⚠` refusal marker |
| run-level problems | `--bad` block below the list | `assemble`'s run-level `problems`, verbatim |

### 5.6 Empty / running / error
- **Empty** (first load): the stage reads `Load a run to see its assembled plan.`; the rubric column
  reads `—`; the feed is blank with an empty status. The header chip still fills from `/v1/config`,
  so the page is diagnostic before it is useful.
- **Running** (a replay in flight): status `replaying…`, rows appending bottom-up; the stage holds
  `Loading…`; the rubric column holds `—`. The PLAN is fetched only on `distill.run.completed`, so
  the stage does not flicker through partial states.
- **Error**, and these must stay DISTINGUISHABLE — the failures have different fixes:
  - config/discovery unreachable → `(unavailable)` in the chip, `could not reach /v1/runs` in the hint;
  - missing trace → `GET /v1/runs/{id}` **404** → `could not load run X (HTTP 404)`;
  - unreadable/corrupt trace → **502, never 500** (`_load_trace` wraps `OSError`/`ValueError`) →
    `could not load run X (HTTP 502)`. A malformed trace is an EXTERNAL failure, and the studio must
    not present it as its own crash;
  - a JSON-valid but non-dict JSONL line → filtered by `trace_io.load_trace` before anything
    downstream sees it, so it degrades to a shorter plan, never an exception;
  - a truncated trace (a hard-killed run whose recorder never reached `__exit__`) → the server
    synthesizes a terminal `distill.run.completed`, so the feed reaches `done` instead of hanging;
  - the SSE connection dropping → status `connection closed`, and the source is closed rather than
    left retrying.
  **Never blank, and never a bare "error".**

### 5.7 Trajectory drawer
A bottom sheet over `GET /v1/runs/{run_id}/iterations`, opened from a fixed `◫ Trajectory` handle at
the bottom right. The handle appears the moment a run is loaded and hides again on `reset`; a
backdrop click, the `✕`, or `Esc` closes it. `static/trajectory.js` exposes a `window.Trajectory(deps)`
factory loaded **before** `app.js`, with a deliberately short injected-deps roster —
`{ el, clear, getRunId, onError }`. `el`/`clear` are the only helpers `app.js` has (there is no `esc`
here, because nothing is escaped — nothing becomes markup); `getRunId` is a **getter, never a
construction-time value**, because this page can load a second run without a reload; `onError` renders
into the Replay feed, since a failed fetch happens *before* the drawer opens and an empty drawer would
be worse than one that does not open.

**Centred on the TURN, because that is the information nothing else has.** `mapper.to_event` gives the
feed `has_code: bool` and drops `output` entirely, so a turn's actual code and its actual REPL output
exist nowhere else in this studio. Three panes:

**WIDTH IS TIME.** Above the two working columns, spanning the drawer, is a horizontal strip:
an axis (`start` … total elapsed, `tabular-nums`) over a `74px` row of segments, each a tab whose
width is its share of the run's wall clock, floored at `108px`, scrolling horizontally rather than
squeezing (`flex-shrink:0` is load-bearing — without it flexbox re-equalises the widths and the
encoding silently disappears). A segment's top edge carries its **tool family** hue
(`--fam-read` / `--fam-draft` / `--fam-list`, overridden by `--bad` / `--warn` when the call failed or
was unrecognised — the outcome outranks the taxonomy), which is the only information that survives a
segment squeezed to the floor. Between turn groups sits a clickable `T<n> ▸` marker that opens that
turn.

*This replaced a 260px side column of equal-height rows,* which is the one place a duration can only
ever be text. On the run it was rebuilt against, **one of nine calls took 242.6s of 313.4s (84.3%)**
while the rest took 2–8s each: a fact a column of identical rows cannot show and a proportional strip
cannot hide. The denominator is the SUM, not the slowest call — the strip is a ruler laid along the
whole run, not a set of per-row bars.

Below it, two columns:

- **left — turn nav.** A **search box** over each turn's `reasoning + code + output` (`N matches`
  beside it): a hit gains an amber edge, a miss DIMS to 0.4 rather than being filtered out, because
  which turn matched is only meaningful against the run's sequence and a filtered list silently
  renumbers what the reader is looking at. It earns its place here more than in the siblings — one
  turn's REPL echo ran **16,038 characters** on the run this was built against. `←`/`→` step through
  the stops, and are released while the search box has focus (there they are the caret keys).
  Then `Init`, then one entry per turn (`Turn N` + the first line of its reasoning, plus its
  `duration_s` when the trace has one).
- **right — detail.** *Init*: the run's inputs as `kv` rows — project **basename** (never the path),
  transcript/artifact counts, `planner`/`drafter`, the pinned `interpreter` (always `pyodide`, which
  is the point: invariant 1's sandbox pin, visible per run), rubric criteria, and the two budgets.
  *Turn*: its `reasoning` as prose, then a collapsible **REPL** block with `code` and `output` in
  their own wells. *Timeline entry*: its label, its `ok`, and the fields that tool's own allowlist
  branch in `iterations._tool_entry` contributed (iterated, not switched per tool — the server owns
  that roster, and a second copy here would drift). A `sub_call` renders `model` + input/output wells.

**A turn marker is a BUTTON.** It shipped once as a plain `div`: it sat in the strip looking exactly
like the clickable segments beside it and did nothing when pressed. A control that LOOKS interactive
and is not is worse than no control — and the test for it asserts that it opens the turn it NAMES,
because a marker wired to the wrong turn would still pass a "has a handler" check.

**The timeline is FLAT and UNCONDITIONAL; `turn_index` is only an enrichment.** That is forced by two
measured numbers, not a hedge: a real live run's `main_step` span is 20.4s → `per_turn_timing = true`,
while the offline scripted harness spans 0.0019s → `false`, and `turn_index` back-mapping runs *only*
when it is true. So on every trace this workspace's tests can produce — and on any genuinely fast run
— no timeline entry carries a `turn_index` at all. A drawer that reached tool calls only *through*
turn grouping would render an empty pane in exactly those cases (an audit caught precisely that in an
earlier design). Nothing about whether an entry renders reads `turn_index`; only the cross-highlight
does (`.related` on the linked turn / the linked entries), and it simply switches off when the link
is not real.

**Timing is stated, never dressed up.** `timing_note` renders **verbatim** — `per_turn_timing` picks
the tag (`● per-turn timing` / `ⓘ timing`) and nothing else, because that sentence is the honest
description of what this trace's timing does and does not mean. A turn with no `rel_s`/`duration_s`
says so ("no per-turn timing on this trace… the tool timeline is unaffected") instead of showing a
zero. And every timeline entry carries the caveat in words: **`duration_s` is the gap since the
previous recorded event — planner-think + tool-exec.** There is no per-call instrumentation anywhere
in this project, so calling it a tool latency would be a fabricated measurement.

**The `textContent` rule is the drawer's actual mitigation, not hygiene.** `iterations.py`'s leak
tests prove `timeline` and `initial` carry no paths, no drafted bodies and no evidence. They do **not**
cover a turn's `reasoning`/`code`/`output`, and they cannot: on the real live run those numbers came
from, **4 of 6 drafted bodies and all 6 evidence blobs appear in `iterations[*].code` /
`iterations[*].output`**, because that text is the REPL's own echo — the planner printed a drafting
call's return value and typed the evidence in as a literal. Since showing turns is the entire reason
this drawer exists, the answer is **rendering, not filtering**: every string reaches the page through
`el(tag, className, text)`, which sets `textContent`. The REPL block says so on screen too ("verbatim
REPL echo — may repeat a drafted body or the evidence behind it; rendered as text, never markup").
Never read the drawer's safety off those leak tests.

*Rebuilt, not ported.* Every sibling's `trajectory.js` assembles its panes by assigning `innerHTML`,
seven sites each. That is forbidden here absolutely (§7's first Don't), so this file was rebuilt
against `el()`/`clear()`, and `tests/static-contract.test.js` now scans **every `static/*.js`** for
the sinks — widened in the same pass, because a scan that read only `app.js` would have waved the one
new file through.

*Not built, each for a stated reason:* no `replay-core.js`, no ▶/⏸/speed transport, no progress bar,
no expand-to-full, no `run-core.js`, and nothing implying a live run. (In-drawer search WAS on this
list and is now built — see the turn nav above. It came off the list on evidence, not taste: a single
turn's REPL echo measured 16,038 characters, so "which turn touched X" is otherwise a manual read of
every turn.) A
transport's payoff scales with tool-call count and this project's runs make a handful of calls;
`app.js` has never even used the server's existing `?delay=` pacing. The `←`/`→` stop-walk survives as
~12 inlined lines (`buildStops` / stop index / step target) — vendoring `replay-core.js` to use a
quarter of it would mean carrying a replay engine for a keyboard shortcut. No icon set either: pane
labels are text, matching §5.3's badge decision.

## 6. Depth / motion
2px geometry (`--radius:2px` on buttons, inputs, panels — chips are pills at `999px`; the candidate frame's left edge is
3px so state reads at a glance). Depth via surface steps + 1px hairlines only — no glassmorphism, no
drop-shadow stacks, no marketing gradient anywhere. The header is `color-mix`-translucent over `--bg`.
Motion is almost absent by design: one 160ms `rowin` fade-and-rise on a feed row entering, a
focus ring (`box-shadow: 0 0 0 2px var(--signal-glow)`) on inputs, a hover ring on the Load button,
and — the one larger movement on the page — the §5.7 drawer's 260ms `translateY` slide with its
backdrop fade. That one is a *sheet arriving*, not a review surface animating under a reader, which
is the line: **no spinner, no pulse, no alloy sweep, and nothing that moves while a human is deciding
whether to delete their own history.** A `prefers-reduced-motion: reduce` block turns all of it off,
because a stated OS preference outranks a house style.

**Responsive.** The three tracks total ~1000px, so below `1040px` they stack into one column and
`.layout` releases its `calc(100vh - 56px)` pin — with one column, three independent scroll tracks
inside a fixed viewport height would each be a few rows tall, so the PAGE scrolls instead and the
feed is capped at `50vh` so it cannot push the plan off-screen. The §5.7 drawer's own three tracks
(220 | 1fr | 260) hit the same wall and stack in the same breakpoint: the sheet grows to `86vh`, the
nav and timeline are capped at `24vh` each so the detail pane — the reason the drawer exists — is
never squeezed off the bottom. Three rules make the horizontal axis safe at any width and all are
pinned by `tests/static-contract.test.js`: every model-supplied field (`key_fields`, a rubric value,
a feed row's scalars) carries `word-break`; the draft `<pre>` carries `overflow-wrap:anywhere` on top
of `pre-wrap`, because `pre-wrap` breaks at whitespace only and a drafted body's token lengths are
untrusted; and `.traj-well` carries the same pair, because a turn's REPL echo is the same class of
untrusted text.

## 7. Do / Don't
**Do:** key the frame on state `assemble()` DERIVED from the trace (§2), never on the plan's own
claim · keep `applyBlocker()` a faithful mirror of the refusals a trace can decide, so "red" means
"the writer will refuse this" — and never let it imply the converse ·
flag a broken candidate, never drop it · render every draft **and every turn's REPL text** through
`el.textContent` · keep the Rubric module labelled FACTS · distinguish 404 from 502 from a dropped
stream · say "replay" when it is a replay · render `timing_note` verbatim · say in words that a
timeline `duration_s` is a gap (planner-think + tool-exec) · `word-break` on every field that can
carry a model-supplied path or token.

**Don't:** no `innerHTML`, anywhere, in any `static/*.js`, for any reason · no model-role chips in
the header (`/v1/config` has no model to report; §5.7's Init pane reads a past run's own
`run_start.meta` instead, as `kv` rows) · no vendored font, and never block on one · no inline SVG
icon set · no primary POST button, no run-id preview, no `live`/`subscription` studio extra (there is
no live path to gate) · in the Trajectory drawer specifically, no `replay-core.js`, no ▶/⏸/speed
transport, no progress bar, no expand-to-full, no `run-core.js` and nothing implying a live run ·
don't make the tool timeline depend on `turn_index`, which is absent whenever `per_turn_timing` is
false · don't FILTER a turn's REPL text in place of rendering it as text, and don't cite Pass 3's
leak tests as evidence that it is clean — they cover `timeline` and `initial` only · don't claim the
feed is live · don't draw causality between feed rows (`main_step` flushes post-hoc — the ordering is
not causal) · don't invent response fields a run lacks (hide, don't fake) · don't turn `rubric_facts`
into a score, a bar, or a grade · don't key anything on `candidate.artifact_id` alone, which is the
plan's CLAIM and the exact thing this design exists to distrust.

## 8. Acceptance (in a browser)
1. First screen is unmistakably this product: `▣ ctx-distillery studio` in mono, a Load box naming a
   real traces directory, and a COLLAPSED meta column (two tracks, not three) — no hero, no
   marketing, and no header chip.
2. Loading a run id from the `<datalist>` fills the **Replay feed** bottom-up, the status goes
   `replaying…` → `done`, the PLAN renders only after `done`, and the `◫ Trajectory` handle appears
   at the bottom right (it was hidden before any run was loaded).
3. **The drawer (§5.7).** Opening it shows `Init` + one nav entry per turn; selecting a turn renders
   that turn's reasoning as prose and its `code`/`output` in the REPL block; `←`/`→` walk Init ↔
   turns and `Esc` closes. On a trace whose note reads *"Per-turn timing isn't available…"* — i.e.
   any offline-harness trace — **the tool timeline is still fully populated**, one entry per
   `tool_call`/`sub_call`, and each says `gap`, not "took". On a live-timed trace, selecting a turn
   also outlines its linked timeline entries.
4. A `promote_to_memory` candidate whose drafting call succeeded shows a `--signal` stripe in the
   rail LIST and a `◆`; selecting it heads the stage `▾ [N] promote_to_memory`, the `Entry` view
   renders the four ZONES with EVIDENCE naming real sessions (or `subagent … of …`), and the `Draft`
   switch shows its **verbatim drafted markdown+frontmatter**. Exactly ONE scrollbar is reachable in
   that column.
4b. **Ticking builds a command.** Tick two promotions and the block under the list reads
   `ctx-distillery-apply … --approve <i>,<j>` with a working `copy`, no `--confirm`, and a note
   naming the project directory. A `keep` row's box is DISABLED and says why; so is a blocked one.
4c. **A transcript is a control.** Clicking one in EVIDENCE fills it as a pill and shortens the list
   to citing candidates; clicking a second says `citing BOTH …`; `✕` clears. If a ticked candidate
   is hidden by the filter, the chip says so in `--warn`. `↑`/`↓` walk only the visible rows.
4d. **Two runs, two shapes.** Load a run whose evidence is integer lists and one whose evidence is
   prose (`transcripts[2],[12] (…)`): both render links, neither hangs, and a bare `[3]` elsewhere in
   the same prose is NOT a link.
5. A candidate the apply step would refuse shows the full `--bad` frame, its `problems` lines and the
   `⚠` marker, and stays ON SCREEN. Check **both** shapes: (a) an `artifact_id` matching no drafting
   call, and (b) a promotion whose assembled draft is EMPTY though it carries no `problems` and may
   report `draft_ok = true` — the case §2 exists for.
6. A draft containing `<img src=x onerror=alert(1)>` and a 5000-character unbroken token renders as
   literal text and wraps — no script runs, and the page does not scroll sideways. **Same check
   inside the drawer**: a turn whose `output` echoes that draft renders it as text in `.traj-well`.
7. Requesting a run id with no trace file shows `HTTP 404`; a corrupted trace file shows `HTTP 502`,
   never a 500 and never a blank stage. Clicking `◫ Trajectory` for a run whose `/iterations` fetch
   fails writes one red `trajectory` row into the feed and does **not** open an empty drawer.
8. The theme toggle flips light/dark and survives a reload; at 375px nothing overflows horizontally
   — below `1040px` the three tracks STACK into one column, the viewport-height pin is released so
   the page scrolls instead of the panels being crushed, and the drawer's panes stack with the detail
   pane still reachable. The tool timeline keeps scrolling HORIZONTALLY at every width: its segment
   widths are the time encoding, so they are never allowed to reflow to fit.
