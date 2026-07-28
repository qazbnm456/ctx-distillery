# ctx-distillery-studio: visual & UX spec

The web frontend's design contract. Implementation (`static/{index.html,app.js,style.css}`) follows
this file. **Architecture is locked in [`README.md`](README.md)** — the five endpoints, the SSE event
mapping, the replay-only scope decision and its reopening conditions, install/run, and every deferred
item. This doc owns the *look and feel* only.

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
one hairline bottom border. Right side: **one chip only** — `TRACES` + the directory from
`GET /v1/config`, truncated with an ellipsis at `22ch` — then the theme toggle (`☾`).

*Not copied:* the siblings' three model-role chips (`planner / analyst / classifier`). This project's
model is an INJECTED `chat_fn`, not an env-var-selected role trio, and `/v1/config` returns
`{"traces_dir": ...}` and nothing else. The traces directory is what genuinely varies by deployment
and what a "why is my run not in the list" user needs to see, so it takes the slot. **Never render a
model name here** — the server does not know one, and inventing the chip would be fabricating a field
the response lacks.

### 5.2 Load box (left rail, top)
`▾ LOAD A RUN`. One row: a mono text input bound to a `<datalist>` filled from `GET /v1/runs`
(placeholder `pick or type a run id…`, `spellcheck="false"`), and a **Load ▶** button — the only
`--signal`-filled control on the page. Enter in the input activates it. Below, a `.hint` line
reporting `N run(s) available`, or `no trace files found — check /v1/config's traces_dir`, or
`could not reach /v1/runs`. The hint is diagnostic on purpose: the two ways this page looks broken
are a wrong `CTXD_TRACES_DIR` and a dead server, and it distinguishes them.

*Not copied:* there is **no primary action button** in the sibling sense (Classify / Solve), no
`POST /v1/*`, no run-id preview, and no `run-core.js`. There is no live-drive endpoint at all — see
`README.md`'s "Scope: replay-only, v1" for the three reasons and the reopening conditions. **Load is
the primary action, and it is a read.**

### 5.3 Replay feed (left rail, fills remaining height)
`▾ REPLAY FEED` + a status word (`replaying…` → `done`, or `connection closed`). A scroll container,
newest row at the BOTTOM, appended per SSE event, scrolled to the bottom on each append. Each row: a
mono `.fr-badge` pill carrying the event name, then a body — `.fr-line` (primary), `.fr-reasoning`
(pre-wrapped, `--text-dim`), `.fr-fields` (`--text-faint`). A 2px left edge tints per family: default
`--border`; `--ok` / `--bad` on a `distill.draft.created` row per its `ok`.

The seven mapped families (`mapper.to_event` is the source of truth):
- `distill.run.created` — `transcripts` · `memory_artifacts` · `rubric_criteria`
- `distill.plan.step` — **the planner's own reasoning turn**: `turn N (wrote code)` + the reasoning
  text. For a judgement-only task with five tools this is plausibly the richest content in the trace.
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

### 5.4 The result: PLAN stage + right modules
Page-level **3 columns**: `320px` rail | `minmax(0,1fr)` stage | `300px` modules, `height:
calc(100vh - 56px)` so the page itself never scrolls — each panel is its own scroll track.

**Middle stage — `▾ PLAN` (the money shot: candidate + drafted text, side by side).** One
`.candidate-row` per `AssembledCandidate`, framed per §2, in plan order. Each row, top to bottom:
1. a head: `#N` index · the **action pill** (`--signal` for the two promotions, `--warn` for `prune`,
   `--text-dim` for `keep`) · the `artifact_id` when there is one · a `draft ok` / `draft failed`
   chip when `draft_ok` is not null;
2. `key_fields` as one compact JSON line (`word-break` on, because `target_path` is attacker-length
   by nature — it is a path from a file the model read);
3. **the draft** — a `<pre>` on `--surface-1`, `white-space:pre-wrap`, capped at `260px` with its own
   scroll. Written with **`el.textContent` ONLY, never `innerHTML`**: a drafted memory/skill body is
   untrusted model output, not markup to render. This rule is absolute and non-negotiable across the
   whole file;
4. any per-candidate `problems`, one `--bad` line each;
5. the `⚠` refusal marker when the candidate is **blocked** (§2).

Run-level `problems` render in a `--bad`-bordered `.run-problems` block after the last candidate —
including the one that matters most, `"no plan was produced by this run"` from `assemble(events,
None)`. Empty states: `Load a run to see its assembled plan.` → `Loading…` → either the rows or
`this run's plan proposed no candidates.`

**Right module — `▾ RUBRIC FACTS (ATLAS)`.** The ten deterministic facts from
`rubric.trace_facts`, grouped client-side under the four ATLAS categories (`CATEGORY_LENS` mirrors
`rubric._CATEGORY_LENS`; the endpoint returns them flat and the grouping is DISPLAY only, adding no
server dependency): **TF** `n_candidates` · `n_non_keep` · `plan_problems`; **TA** `min_read_step` ·
`min_draft_step` · `any_circuit_broken`; **TG** `n_backed_promotions` · `prune_targets_named`; **PA**
`n_candidate_problems` · `n_bad_skill_scope`. Category label in `--signal`, then `key` (dim, left) /
`value` (bright, right) rows.

**Label them FACTS, never scores.** `trace_facts` decides no met/unmet and no field anywhere is a
reward. A `min_read_step` / `min_draft_step` pair is a raw observation; whether "evidence came before
drafting" is left to whoever reads it. Never render a total, a percentage, a bar, or a ✓/✗ over these.

*Not copied:* a `§5.7 Trajectory drawer` and `GET /v1/runs/{id}/iterations`. There is no
`iterations.py`, no `trajectory.js`, and no such route in this studio — describing one would be
fabrication. It is legitimate **deferred** scope, and the fix belongs in `app.py` (a new endpoint
serving per-turn init/reasoning/REPL) plus a `static/trajectory.js`, in that order.

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

## 6. Depth / motion
2px geometry (`--radius:2px` on buttons, inputs, panels — chips are pills at `999px`; the candidate frame's left edge is
3px so state reads at a glance). Depth via surface steps + 1px hairlines only — no glassmorphism, no
drop-shadow stacks, no marketing gradient anywhere. The header is `color-mix`-translucent over `--bg`.
Motion is almost absent by design: one 160ms `rowin` fade-and-rise on a feed row entering, a
focus ring (`box-shadow: 0 0 0 2px var(--signal-glow)`) on inputs, a hover ring on the Load button.
**No spinner, no pulse, no alloy sweep** — a review console that animates while a human is deciding
whether to delete their own history is working against the user.

**Responsive.** The three tracks total ~1000px, so below `1040px` they stack into one column and
`.layout` releases its `calc(100vh - 56px)` pin — with one column, three independent scroll tracks
inside a fixed viewport height would each be a few rows tall, so the PAGE scrolls instead and the
feed is capped at `50vh` so it cannot push the plan off-screen. Two rules make the horizontal axis
safe at any width and both are pinned by `tests/static-contract.test.js`: every model-supplied field
(`key_fields`, a rubric value, a feed row's scalars) carries `word-break`, and the draft `<pre>`
carries `overflow-wrap:anywhere` on top of `pre-wrap`, because `pre-wrap` breaks at whitespace only
and a drafted body's token lengths are untrusted.

## 7. Do / Don't
**Do:** key the frame on state `assemble()` DERIVED from the trace (§2), never on the plan's own
claim · keep `applyBlocker()` a faithful mirror of the refusals a trace can decide, so "red" means
"the writer will refuse this" — and never let it imply the converse ·
flag a broken candidate, never drop it · render every draft through `el.textContent` · keep the
Rubric module labelled FACTS · distinguish 404 from 502 from a dropped stream · say "replay" when it
is a replay · `word-break` on every field that can carry a model-supplied path or token.

**Don't:** no `innerHTML`, anywhere, for any reason · no model-role chips (`/v1/config` has no model
to report) · no vendored font, and never block on one · no primary POST button, no run-id preview, no
`live`/`subscription` studio extra (there is no live path to gate) · don't describe a Trajectory
drawer that does not exist · don't claim the feed is live · don't draw causality between feed rows
(`main_step` flushes post-hoc — the ordering is not causal) · don't invent response fields a run
lacks (hide, don't fake) · don't turn `rubric_facts` into a score, a bar, or a grade · don't key
anything on `candidate.artifact_id` alone, which is the plan's CLAIM and the exact thing this design
exists to distrust.

## 8. Acceptance (in a browser)
1. First screen is unmistakably this product: `▣ ctx-distillery studio` in mono, a single `TRACES`
   chip showing a real directory, and a Load box — no hero, no marketing.
2. Loading a run id from the `<datalist>` fills the **Replay feed** bottom-up, the status goes
   `replaying…` → `done`, and the PLAN renders only after `done`.
3. A `promote_to_memory` candidate whose drafting call succeeded shows a `--signal` left edge, a
   `draft ok` chip, and its **verbatim drafted markdown+frontmatter** in the `<pre>` beside its
   `action`/`key_fields` — the money shot, side by side.
4. A candidate whose `artifact_id` matches no drafting call shows the full `--bad` frame, its
   `problems` lines, and the `⚠` refusal marker — and it is still ON SCREEN, not dropped.
5. A promotion with an EMPTY assembled draft is ALSO red and marked, even though it carries no
   `problems` and may report `draft_ok = true`. (This is the case §2 exists for; check it explicitly.)
6. A draft containing `<img src=x onerror=alert(1)>` and a 5000-character unbroken token renders as
   literal text and wraps — no script runs, and the page does not scroll sideways.
7. Requesting a run id with no trace file shows `HTTP 404`; a corrupted trace file shows `HTTP 502`,
   never a 500 and never a blank stage.
8. The theme toggle flips light/dark and survives a reload; at 375px nothing overflows horizontally
   — below `1040px` the three tracks STACK into one column, the viewport-height pin is released so
   the page scrolls instead of the panels being crushed, and the `<pre>` still scrolls inside its own
   260px box.
