// ctx-distillery-studio — zero-build vanilla JS. No bundler, no node_modules, no template strings
// that ever become markup: every piece of untrusted server-supplied text (a candidate's `draft`,
// a planner's `reasoning`, a tool's scalar fields, …) is written via `el.textContent` ONLY. Never
// `innerHTML`, ever, anywhere in this file — a drafted memory/skill body is untrusted model output,
// not markup to render.
"use strict";

// -- tiny DOM helper: build an element and set its TEXT (never HTML) --------------------------
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// -- /v1/config + /v1/runs: page load ----------------------------------------------------------

// Where the runs come from. Held here and rendered by `loadRunsList`, folded into the run count,
// because the two facts answer ONE question — "why is my run not listed" — and answering it in two
// places on opposite sides of the page made a reader assemble it themselves.
//
// It used to be a permanent header chip. Two things were wrong with that: it spent the most
// valuable strip on the page on a fact needed about once per session, and the slot was sized for a
// MODEL NAME (the siblings' chip) while holding a PATH, so it truncated from the head and rendered
// `/Users/operator/Documents/…` — dropping the only segment anyone reads a traces path for.
let TRACES_DIR = null;

async function loadConfig() {
  try {
    const res = await fetch("/v1/config");
    const cfg = await res.json();
    TRACES_DIR = cfg.traces_dir || "";
  } catch {
    TRACES_DIR = null;
  }
}

async function loadRunsList() {
  const hint = document.getElementById("runs-hint");
  try {
    const res = await fetch("/v1/runs");
    const body = await res.json();
    const list = document.getElementById("runs");
    clear(list);
    for (const runId of body.runs || []) {
      const opt = document.createElement("option");
      opt.value = runId;
      list.appendChild(opt);
    }
    // The COUNT only. The location used to be a clause in this same sentence; it is its own block
    // below, for the reason stated there.
    const n = (body.runs || []).length;
    hint.textContent = n
      ? `${n} run${n === 1 ? "" : "s"}`
      : "no trace files — is that the right directory?";
    // The PATH is its own labelled block rather than a clause inside that sentence. `homeRelative`
    // keeps a path under $HOME short, which is the common case and worth keeping — but a traces
    // directory outside it (a temp dir, a mounted volume) runs past a hundred characters, and
    // inside a sentence in a 320px rail that is unreadable however it is folded. A wrapping block
    // shows all of it; the sentence keeps saying only the count.
    const dirEl = document.getElementById("traces-dir");
    if (dirEl) {
      if (TRACES_DIR) {
        dirEl.textContent = "";
        dirEl.appendChild(el("b", null, "reading traces from"));
        dirEl.appendChild(document.createTextNode(homeRelative(TRACES_DIR)));
        dirEl.title = TRACES_DIR;   // the unfolded path, for a copy-paste
        dirEl.hidden = false;
      } else {
        dirEl.hidden = true;
      }
    }
  } catch {
    hint.textContent = "could not reach /v1/runs";
  }
}

// `/Users/operator/Documents/x/traces` -> `~/Documents/x/traces`. The home prefix is the longest part
// of the path and the one part a reader already knows, so folding it is free width.
function homeRelative(dir) {
  const m = /^(\/(?:Users|home)\/[^/]+)(\/.*)?$/.exec(String(dir));
  return m ? "~" + (m[2] || "") : String(dir);
}

// -- the live feed: one row per mapped SSE event, newest at the bottom ------------------------

const FEED_EVENTS = [
  "distill.run.created",
  "distill.plan.step",
  "distill.sub_lm.call",
  "distill.evidence.read",
  "distill.draft.created",
  "distill.plan.done",
  "distill.run.completed",
];

function feedRow(eventName, data) {
  const row = el("div", "feed-row");
  row.classList.add("enter");
  const badge = el("span", "fr-badge fr-" + eventName.replace(/\./g, "-"), eventName);
  row.appendChild(badge);
  const body = el("div", "fr-body");

  if (eventName === "distill.plan.step") {
    // The planner's OWN reasoning turn — plausibly the richest content in the feed for a
    // judgement-only task with six tools, so it gets its own readable line, not a JSON dump.
    body.appendChild(el("div", "fr-line", `turn ${data.turn}${data.has_code ? " (wrote code)" : ""}`));
    if (data.reasoning) body.appendChild(el("div", "fr-reasoning", data.reasoning));
  } else if (eventName === "distill.sub_lm.call") {
    body.appendChild(el("div", "fr-line", "sub-LM escalation"));
    body.appendChild(el("div", "fr-reasoning", `in: ${data.input || ""}`));
    body.appendChild(el("div", "fr-reasoning", `out: ${data.processed_or_raw || ""}`));
  } else if (eventName === "distill.evidence.read") {
    body.appendChild(el("div", "fr-line", data.tool || "(evidence read)"));
    body.appendChild(el("div", "fr-fields", JSON.stringify(omit(data, ["tool"]))));
  } else if (eventName === "distill.draft.created") {
    const flag = data.ok ? "fam-ok" : "fam-bad";
    row.classList.add(flag);
    body.appendChild(el("div", "fr-line", `${data.tool} -> artifact_id=${data.artifact_id}`));
    body.appendChild(
      el(
        "div",
        "fr-fields",
        `ok=${data.ok} circuit_broken=${data.circuit_broken}` +
          (data.errors && data.errors.length ? ` errors=${JSON.stringify(data.errors)}` : "")
      )
    );
  } else if (eventName === "distill.run.created") {
    body.appendChild(
      el(
        "div",
        "fr-line",
        // The COMPOSITION, not just the count: a run that jumped from 1 transcript to 43 because
        // subagent transcripts were included is otherwise silent semantic drift. `sessions` /
        // `subagents` are null on an old or malformed trace, in which case the count stands alone.
        `transcripts=${data.transcripts}${composition(data)} ` +
          `memory_artifacts=${data.memory_artifacts} ` +
          `rubric_criteria=${(data.rubric || {}).criteria || 0}`
      )
    );
  } else {
    body.appendChild(el("div", "fr-line", eventName));
  }
  row.appendChild(body);
  return row;
}

// ` (sessions=1 subagents=42)`, or "" when the trace does not say. Never renders zeros for an
// absent value — `sessions=0 subagents=0` would be a positive claim about an old trace that
// carries no identity list at all.
function composition(data) {
  if (data.sessions === null || data.sessions === undefined) return "";
  if (data.subagents === null || data.subagents === undefined) return "";
  return ` (sessions=${data.sessions} subagents=${data.subagents})`;
}

function omit(obj, keys) {
  const out = {};
  for (const k of Object.keys(obj || {})) {
    if (!keys.includes(k)) out[k] = obj[k];
  }
  return out;
}

let activeSource = null;

function stopReplay() {
  if (activeSource) {
    activeSource.close();
    activeSource = null;
  }
}

function startReplay(runId) {
  stopReplay();
  // The one place the live run id is set. `trajectory`'s `getRunId` reads THIS, at click time —
  // this page can load a second run without a reload, and a snapshot would pin the drawer to the
  // first one.
  currentRunId = runId;
  trajectory.reset();
  trajectory.showHandle();
  const feed = document.getElementById("feed");
  clear(feed);
  // "replaying…", not "streaming…" — the rows come from a FINISHED trace (there is no live-drive
  // endpoint), and `?delay=` only PACES the replay to feel live. Saying "streaming" claimed a
  // capability the backend does not have.
  document.getElementById("feed-status").textContent = "replaying…";
  const planEmpty = document.getElementById("plan-empty");
  planEmpty.textContent = "Loading…";
  // `hidden = false` is REQUIRED, not redundant — found by review. `loadPlan` sets `hidden = true`
  // on a successful render, so on every load AFTER the first this note stayed hidden and the middle
  // stage sat blank for the whole replay instead of saying "Loading…". It only ever looked right
  // because a fresh page starts with the note visible.
  planEmpty.hidden = false;
  PLAN.candidates = [];
  PLAN.selected = 0;
  PLAN.view = "entry";
  // Ticks are per RUN: carrying them across a load would offer to apply indices that now name
  // different candidates entirely.
  PLAN.picked = new Set();
  PLAN.filter = new Set();
  PLAN.runId = runId;
  PLAN.project = null;
  renderCandidateList();
  renderStage();
  clear(document.getElementById("rubric-list"));
  document.getElementById("rubric-empty").textContent = "—";
  // The column empties here too, so it has to be re-asked here too. Without it, loading a SECOND
  // run left the three-track grid in place around an empty aside until telemetry arrived.
  syncMeta();

  const source = new EventSource(`/v1/runs/${encodeURIComponent(runId)}/events`);
  activeSource = source;

  for (const name of FEED_EVENTS) {
    source.addEventListener(name, (evt) => {
      let data = {};
      try {
        data = JSON.parse(evt.data);
      } catch {
        data = {};
      }
      feed.appendChild(feedRow(name, data));
      feed.scrollTop = feed.scrollHeight;
      if (name === "distill.run.completed") {
        document.getElementById("feed-status").textContent = "done";
        stopReplay();
        loadPlan(runId);
      }
    });
  }
  source.onerror = () => {
    document.getElementById("feed-status").textContent = "connection closed";
    stopReplay();
  };
}

// -- plan review: a rail LIST of candidates, a middle STAGE showing the selected one ----------

// Mirrors `ctx_distillery.rubric._CATEGORY_LENS` — a client-side DISPLAY grouping only (the
// endpoint returns a flat `rubric_facts` dict per its own settled contract; grouping it for
// readability here adds no new server dependency).
const CATEGORY_LENS = {
  TF: ["n_candidates", "n_non_keep", "plan_problems"],
  TA: ["min_read_step", "min_draft_step", "any_circuit_broken"],
  TG: ["n_backed_promotions", "prune_targets_named"],
  PA: ["n_candidate_problems", "n_bad_skill_scope"],
};

// The two actions that carry drafted bytes — mirrors `ctx_distillery.schema.PROMOTION_ACTIONS`.
const PROMOTION_ACTIONS = ["promote_to_memory", "promote_to_skill"];

// Mirrors `ctx_distillery.apply._blocking_problem` — the three conditions `apply_plan` refuses on
// regardless of action kind. Returns the reason, or null. Keyed ONLY on state `assemble()` already
// computed from the trace, never on the plan's own claim about what it drafted (CLAUDE.md
// invariant 2). The third condition is why this is a function and not just `problems.length`: an
// empty promotion draft carries no `problems` and may even report `draft_ok === true`, so without
// it the one candidate a reviewer most needs to see would render as an ordinary row.
function applyBlocker(candidate) {
  if (candidate.problems && candidate.problems.length) {
    return "carries problems — apply_plan refuses this candidate";
  }
  if (candidate.draft_ok === false) {
    return "the drafting call failed its deterministic format check — apply_plan refuses it";
  }
  if (PROMOTION_ACTIONS.includes(candidate.action) && !String(candidate.draft || "").trim()) {
    return "no drafted text was assembled for this promotion (nothing to write)";
  }
  // The two ADDITIONAL refusals that are derivable from the trace alone — added after a review
  // found the console framed both of these teal ("backed") while `apply_plan` would refuse them.
  // Everything above mirrors `apply.py::_blocking_problem`; these two mirror the per-action-kind
  // key_fields conventions (`_promote_skill`'s scope gate, `_prune`'s target gate). They belong
  // here and the REST of apply.py's refusals do not, because these are the only ones decidable
  // from what a finished trace carries — see DESIGN.md §2 for the ones that are apply-time-only.
  const keyFields = candidate.key_fields || {};
  if (candidate.action === "promote_to_skill") {
    const scope = keyFields.scope;
    if (scope !== "global" && scope !== "project") {
      return "key_fields['scope'] must be \"global\" or \"project\" — apply_plan refuses it";
    }
  }
  if (candidate.action === "prune" && !String(keyFields.target_path || "").trim()) {
    return "a prune must name key_fields['target_path'] — apply_plan refuses it";
  }
  return null;
}

// The derived frame state, §2 of DESIGN.md: "blocked" (apply_plan would refuse), "backed" (a
// promotion whose artifact_id resolved to real drafted bytes), else "inert" (keep/prune — there is
// nothing to back).
function candidateState(candidate) {
  if (applyBlocker(candidate)) return "blocked";
  if (PROMOTION_ACTIONS.includes(candidate.action)) return "backed";
  return "inert";
}

// -- the rail LIST and the middle STAGE: one candidate at a time -------------------------------
//
// This replaced a middle column that rendered all ten candidates with every drafted body expanded
// inline. Measured on a real run: 32,091 characters of draft across 10 rows, each capped at 260px
// with its OWN scrollbar, inside a panel that also scrolled — so reading candidate 5 meant scrolling
// the panel to it, scrolling inside its box, and being thrown to candidate 6 when the wheel escaped
// that box. The sibling studios all use rail-list -> stage-detail; this is that, built from
// `textContent` nodes because invariant 10 forbids the `innerHTML` they assemble their stages with.

// `selected` is what the STAGE shows; `picked` is what the apply command will carry. Two different
// questions about one row — "let me read this" and "I want this applied" — and conflating them is
// how a reviewer applies something they only meant to open.
const PLAN = { candidates: [], selected: 0, view: "entry", picked: new Set(), runId: null,
               project: null, transcriptIndex: [], filter: new Set() };

function candidateLabel(candidate) {
  const k = candidate.key_fields || {};
  const named = k.target_path || k.name || candidate.artifact_id || "";
  return String(named).split("/").pop() || "";
}

function renderCandidateList() {
  const list = document.getElementById("cand-list");
  const count = document.getElementById("cand-count");
  clear(list);
  const shown = visibleCandidates();
  count.textContent = PLAN.candidates.length
    ? shown ? `(${shown.size} of ${PLAN.candidates.length})` : `(${PLAN.candidates.length})`
    : "";
  renderFilterChip(shown);
  PLAN.candidates.forEach((candidate, index) => {
    if (shown && !shown.has(index)) return;
    const state = candidateState(candidate);
    const row = el("div", "cand-item state-" + state);
    if (index === PLAN.selected) row.classList.add("on");

    // The TICK decides what gets applied. A blocked candidate's box is disabled rather than hidden:
    // `apply_plan` would refuse it, and a row that silently cannot be chosen reads as an oversight.
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "ci-pick";
    box.checked = PLAN.picked.has(index);
    const why = notApplicable(candidate);
    box.disabled = why !== null;
    box.title = why || "include in the apply command";
    box.setAttribute("aria-label", "apply candidate " + index);
    box.addEventListener("change", () => {
      if (box.checked) PLAN.picked.add(index);
      else PLAN.picked.delete(index);
      renderApplyCommand();
    });
    row.appendChild(box);

    // The REST of the row opens it on the stage. Separate control, separate verb.
    const open = el("button", "ci-open");
    open.type = "button";
    open.setAttribute("aria-pressed", String(index === PLAN.selected));
    open.appendChild(el("span", "ci-action", candidateTitle(candidate)));
    // One glyph carries the only thing a reviewer scans a list for: would `apply_plan` take it.
    if (state === "blocked") open.appendChild(el("span", "ci-mark bad", "\u26a0"));
    else if (state === "backed") open.appendChild(el("span", "ci-mark ok", "\u25c6"));
    open.addEventListener("click", () => selectCandidate(index));
    row.appendChild(open);

    list.appendChild(row);
  });
  renderApplyCommand();
}

// `key_fields` is FREE-FORM: the planner invents the keys, and this run's real ones are
// `target_path`, `reason`, `transcripts`, `corroborated_by`, `overlaps_with`, `scope`. Rendering
// them as one uniform key/value list treated a paragraph of prose, a machine-identifying absolute
// path and a list of bare integers as the same kind of thing. They answer different questions, so
// the stage groups them by the question and renders each in its own shape. Anything unrecognised
// still renders, under `OTHER` — a free-form field must never silently vanish.
const FIELD_ZONE = {
  scope: "proposes",
  target_path: "applies",
  transcripts: "evidence",
  corroborated_by: "evidence",
  overlaps_with: "evidence",
  reason: "why",
};

// `[1, 2]` -> `session b2d5ba2e · session cd68fdc4`. The indices are positions in `transcripts`, and
// until `transcript_index` existed nothing anywhere could map one back to a file — a reviewer
// reading `transcripts: [1]` had no way to learn what transcript 1 WAS. Falls back to the raw
// numbers when the trace predates the field, which is honest: better a bare index than a guess.
function describeTranscripts(value) {
  if (!Array.isArray(value)) return null;
  const index = PLAN.transcriptIndex || [];
  if (!index.length) return value.map((n) => `transcript ${n}`);
  return value.map((n) => {
    const entry = index[n];
    if (!entry || typeof entry !== "object") return `transcript ${n}`;
    const id = String(entry.id || "").slice(0, 8);
    if (entry.kind === "subagent") {
      const owner = String(entry.session || "").slice(0, 8);
      return `subagent ${id}${owner ? ` of ${owner}` : ""}`;
    }
    return `session ${id}`;
  });
}

// A memory/skill path rendered for a human: the file name, with the full path one hover away. The
// verbatim value is `/Users/<name>/.claude/projects/-Users-<name>-Documents-<proj>/memory/x.md`,
// which is mostly a home directory, mostly repeated on every row, and identifies the machine.
function shortTarget(value) {
  const s = String(value || "");
  const parts = s.split("/").filter(Boolean);
  if (parts.length <= 2) return s;
  return parts.slice(-2).join("/");
}

// A QUALIFIED run of transcript references inside prose, e.g. `transcripts[3],[11],[20],[23]-[26]`.
//
// `key_fields` is free-form and the planner does not use one shape: one run wrote
// `transcripts: [0, 1, 2]` (a real list), another wrote `sources: "transcripts[2],[12] (siblings
// under session 30f8147f, not independent of each other)"` — prose. Measured across the second
// run's 18 transcript-mentioning strings: `transcript[N]` 12 times, `transcripts[N]` 8, and bare
// `[N]` 17 as continuations, plus one RANGE (`[23]-[26]`).
//
// A bare `[N]` is NEVER matched on its own. It is ambiguous — a footnote, an array index, a
// citation marker — and a wrong link is worse than no link: it invites a reviewer to filter by
// evidence that was never claimed. So a run must OPEN with the word, and only then do following
// `,[N]` / `-[N]` continuations join it. A range is not expanded either: `[23]-[26]` links 23 and
// 26, the two indices actually written, and does not invent 24 and 25.
//
// Read with `String.matchAll`, never with a shared `/g` regex driven by `exec`. A `/g` regex carries
// MUTABLE `lastIndex`, and these two are used re-entrantly: `appendProseWithLinks` iterates the runs
// and, per reference, calls `transcriptControl` -> `citingTranscript` -> `transcriptsCitedBy`, which
// scans every other candidate's prose with the SAME objects. The inner scan reset `lastIndex` to 0,
// the outer loop restarted from the beginning, and the render never terminated: measured as a
// 4 GB heap exhaustion, not a slow page. `matchAll` clones the regex per call, so no state is
// shared. The bug was invisible on the first run because its evidence is real integer lists and its
// prose carries no `transcript[N]`, so the outer loop never had a body to re-enter from.
const TX_RUN = /transcripts?\s*\[\s*\d+\s*\](?:\s*[,-]\s*\[\s*\d+\s*\])*/gi;
const TX_ONE = /\[\s*(\d+)\s*\]/g;

// Renders a prose value into `parent`, with qualified transcript references as filter controls and
// everything else as plain text nodes. Never `innerHTML`: this is model-authored prose.
function appendProseWithLinks(parent, value) {
  const text = String(value);
  let at = 0;
  let matched = false;
  for (const run of text.matchAll(TX_RUN)) {
    matched = true;
    if (run.index > at) parent.appendChild(document.createTextNode(text.slice(at, run.index)));
    let inner = 0;
    for (const one of run[0].matchAll(TX_ONE)) {
      if (one.index > inner) {
        parent.appendChild(document.createTextNode(run[0].slice(inner, one.index)));
      }
      parent.appendChild(transcriptControl(Number(one[1]), one[0]));
      inner = one.index + one[0].length;
    }
    if (inner < run[0].length) parent.appendChild(document.createTextNode(run[0].slice(inner)));
    at = run.index + run[0].length;
  }
  if (at < text.length) parent.appendChild(document.createTextNode(text.slice(at)));
  return matched;
}

// One transcript reference as a control, or as plain text when the index does not exist. The number
// is MODEL-SUPPLIED, so it is range-checked before it can become something clickable: a
// hallucinated `transcripts[99]` must read as the prose it is, not as a filter that silently
// matches nothing.
function transcriptControl(n, label) {
  const index = PLAN.transcriptIndex || [];
  if (!Number.isInteger(n) || n < 0 || n >= index.length) {
    return document.createTextNode(label);
  }
  const active = PLAN.filter.has(n);
  const link = el("button", "tx-link" + (active ? " on" : ""), label);
  link.type = "button";
  link.setAttribute("aria-pressed", String(active));
  const named = (describeTranscripts([n]) || [])[0] || `transcript ${n}`;
  const cited = citingTranscript(n).length;
  link.title = active
    ? `Filtering by ${named} — click to clear`
    : `${named} — show the ${cited} candidate${cited === 1 ? "" : "s"} citing it`;
  link.addEventListener("click", () => setTranscriptFilter(n));
  return link;
}

// Every candidate that names this transcript index, in either evidence field. `transcripts` is
// "drawn from" and `corroborated_by` is "confirmed by"; for the question this filter answers —
// "what else came out of this conversation" — both count.
function citingTranscript(n) {
  const out = [];
  PLAN.candidates.forEach((candidate, index) => {
    if (transcriptsCitedBy(candidate).has(n)) out.push(index);
  });
  return out;
}

// Which transcripts a candidate names, from BOTH shapes the planner uses: real integer lists
// (`transcripts` / `corroborated_by`) and qualified references inside any prose value. Without the
// second, a run that wrote its evidence as prose — which one of the two real runs did, under a key
// it invented (`sources`) — had no citations at all, so the filter matched nothing and the links
// pointed at empty sets.
function transcriptsCitedBy(candidate) {
  const found = new Set();
  for (const [key, value] of Object.entries(candidate.key_fields || {})) {
    if (Array.isArray(value) && (key === "transcripts" || key === "corroborated_by")) {
      for (const n of value) if (Number.isInteger(n)) found.add(n);
      continue;
    }
    if (typeof value !== "string") continue;
    for (const run of value.matchAll(TX_RUN)) {
      for (const one of run[0].matchAll(TX_ONE)) found.add(Number(one[1]));
    }
  }
  return found;
}

// Several transcripts can be filtered at once, and the combination is AND: "candidates citing ALL
// of these", not "any of these".
//
// That is a choice, and on this data it is the only useful one. Each transcript is already cited by
// most of the plan (9, 7 and 4 of 10 candidates on the run this was built against), so OR broadens
// to nearly everything and discriminates nothing. AND answers the question the planner's own
// instructions care about — "when multiple transcripts independently confirm the same thing, say so"
// — by showing exactly the candidates that DO draw on more than one conversation. The chip says
// which operator is in force, because a silently-chosen one is how a reader ends up trusting a set
// they misread.
function setTranscriptFilter(n) {
  if (PLAN.filter.has(n)) PLAN.filter.delete(n);
  else PLAN.filter.add(n);
  renderCandidateList();
  renderStage();
}

// Why this candidate cannot be ticked, or null when it can. Mirrors what `apply_plan` would
// actually DO with it, which is the only honest basis for offering the control at all:
//
//   * `keep` is a NO-OP there — `apply.py` returns `STATUS_NOOP` with "keep is a no-op, there is
//     nothing to apply". Offering a tick for it invents an action the writer does not have, and a
//     ticked `keep` would put an index into `--approve` that does nothing. The word already means
//     "leave this alone"; a control that appears to enact it is a contradiction on its face.
//   * a BLOCKED candidate would be refused, so the box is disabled rather than hidden — a row that
//     silently cannot be chosen reads as an oversight, and the reason belongs on the row.
function notApplicable(candidate) {
  const blocker = applyBlocker(candidate);
  if (blocker) return "apply_plan would refuse this: " + blocker;
  if (candidate.action === "keep") {
    return "keep is a no-op — there is nothing to apply";
  }
  return null;
}

// `promote_to_memory` -> `promote memory`, and the artifact's own name after it. The action is the
// thing a reviewer sorts by; the underscored enum value is how the plan spells it, not how anyone
// reads it. The zero-based INDEX is deliberately not shown: it is the key `--approve` consumes, the
// command below carries it, and printing it in the row only invited the question "why does this
// start at 0" for a number nobody has to type.
function candidateTitle(candidate) {
  const action = String(candidate.action || "").replace(/_to_/g, " ").replace(/_/g, " ");
  const name = candidateLabel(candidate);
  return name ? action + "  " + name : action;
}

// The command the ticks build. This is the console's whole purpose: `ctx-distillery show` prints a
// plan, and the next thing a reviewer does is retype indices into `ctx-distillery-apply`. Retyping
// indices from a scrolled list is exactly where an off-by-one applies the wrong candidate.
function renderApplyCommand() {
  const box = document.getElementById("apply-cmd");
  const line = document.getElementById("ac-line");
  const countEl = document.getElementById("ac-count");
  const note = document.getElementById("ac-note");
  if (!box) return;
  const picked = [...PLAN.picked].sort((a, b) => a - b);
  if (!PLAN.candidates.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  countEl.textContent = picked.length
    ? `${picked.length} of ${PLAN.candidates.length} ticked`
    : "nothing ticked yet";
  if (!picked.length) {
    line.textContent = "";
    note.textContent = "Tick a candidate to build its apply command.";
    return;
  }
  const trace = PLAN.runId ? `${TRACES_DIR ? TRACES_DIR + "/" : ""}${PLAN.runId}.jsonl` : "<trace>";
  line.textContent =
    `ctx-distillery-apply ${trace} --project . --approve ${picked.join(",")}`;
  // WITHOUT `--confirm`, deliberately. That flag is what writes, and a copyable one-liner that
  // writes on first paste would make this console the thing that applied a plan — which invariant 8
  // says it must never be. The dry run is the safe first act, and the note says what follows.
  note.textContent =
    "Run it from the " + (PLAN.project ? `\`${PLAN.project}\` ` : "") +
    "project directory. As written this is a DRY RUN: add --confirm once its output looks right.";
}

// The indices the list may show: every candidate citing ALL of the filtered transcripts, or null
// when nothing is filtered (which is not the same as "all of them" — null means do not filter).
function visibleCandidates() {
  if (!PLAN.filter.size) return null;
  let acc = null;
  for (const n of PLAN.filter) {
    const set = new Set(citingTranscript(n));
    acc = acc === null ? set : new Set([...acc].filter((i) => set.has(i)));
  }
  return acc;
}

function renderFilterChip(shown) {
  const chip = document.getElementById("cand-filter");
  const label = document.getElementById("cand-filter-label");
  if (!chip) return;
  if (!PLAN.filter.size) {
    chip.hidden = true;
    return;
  }
  chip.hidden = false;
  const picked = [...PLAN.filter];
  const named = describeTranscripts(picked) || [];
  // "citing BOTH" / "citing ALL n" — never a bare list, because a list of two names beside a
  // shortened result set reads equally well as either operator, and the two give very different
  // sets here.
  const how = picked.length === 1 ? "citing" : picked.length === 2 ? "citing BOTH" : `citing ALL ${picked.length}`;
  // Ticks SURVIVE a filter, so a hidden tick would otherwise be a candidate silently queued for
  // apply that the reviewer can no longer see. Say the number rather than dropping or hiding it.
  const hiddenPicks = [...PLAN.picked].filter((i) => !shown.has(i)).length;
  label.textContent = `${how} ${named.join(" + ")}` +
    (shown.size ? "" : " — no candidate cites all of these") +
    (hiddenPicks ? ` — ${hiddenPicks} ticked candidate${hiddenPicks === 1 ? "" : "s"} hidden` : "");
  label.classList.toggle("warn", hiddenPicks > 0 || shown.size === 0);
}

// `step` is +1/-1 from a keyboard arrow; a click passes the index directly. Stepping walks the
// VISIBLE candidates, not all of them: with a filter on, clamping to the full list walked onto rows
// that are not on screen, so the arrows appeared to do nothing and then jumped.
function stepCandidate(delta) {
  const shown = visibleCandidates();
  const order = shown
    ? PLAN.candidates.map((_, i) => i).filter((i) => shown.has(i))
    : PLAN.candidates.map((_, i) => i);
  if (!order.length) return;
  const at = order.indexOf(PLAN.selected);
  const next = at === -1 ? 0 : Math.max(0, Math.min(at + delta, order.length - 1));
  selectCandidate(order[next]);
}

function selectCandidate(index) {
  if (!PLAN.candidates.length) return;
  PLAN.selected = Math.max(0, Math.min(index, PLAN.candidates.length - 1));
  renderCandidateList();
  renderStage();
  const on = document.querySelector(".cand-item.on");
  if (on) on.scrollIntoView({ block: "nearest" });
}

function renderStage() {
  const body = document.getElementById("stage-body");
  const label = document.getElementById("stage-label");
  const stageSwitch = document.getElementById("stage-switch");
  clear(body);
  const candidate = PLAN.candidates[PLAN.selected];
  if (!candidate) {
    stageSwitch.hidden = true;
    label.textContent = "\u25be Plan";
    return;
  }
  const hasDraft = !!String(candidate.draft || "").trim();
  stageSwitch.hidden = !hasDraft;
  if (!hasDraft) PLAN.view = "entry";
  label.textContent = `\u25be [${PLAN.selected}] ${candidate.action}`;
  for (const btn of stageSwitch.querySelectorAll("button")) {
    btn.classList.toggle("on", btn.dataset.view === PLAN.view);
  }

  const card = el("div", "candidate-row state-" + candidateState(candidate));
  if (PLAN.view === "draft" && hasDraft) {
    const pre = document.createElement("pre");
    pre.className = "candidate-draft";
    pre.textContent = candidate.draft; // NEVER innerHTML — untrusted drafted model output.
    card.appendChild(pre);
  } else {
    const head = el("div", "candidate-head");
    head.appendChild(el("span", "candidate-index", `#${PLAN.selected}`));
    head.appendChild(el("span", "candidate-action action-" + candidate.action, candidate.action));
    if (candidate.artifact_id) head.appendChild(el("span", "candidate-artifact", candidate.artifact_id));
    if (candidate.draft_ok !== null && candidate.draft_ok !== undefined) {
      head.appendChild(el("span", candidate.draft_ok ? "chip-ok" : "chip-bad",
        candidate.draft_ok ? "draft ok" : "draft failed"));
    }
    card.appendChild(head);
    renderZones(card, candidate);
    if (candidate.problems && candidate.problems.length) {
      const problems = el("div", "candidate-problems");
      for (const pr of candidate.problems) problems.appendChild(el("div", "problem-line", pr));
      card.appendChild(problems);
    }
    // The refusal marker goes LAST so it reads as the verdict on everything above it. It is the only
    // surface that makes the empty-promotion-draft case visible at all.
    const blocker = applyBlocker(candidate);
    if (blocker) card.appendChild(el("div", "candidate-blocked", "\u26a0 " + blocker));
  }
  body.appendChild(card);
  body.appendChild(el("div", "stage-nav-hint", "\u2191 \u2193 step through candidates"));
}

function bindCandidateFilter() {
  const btn = document.getElementById("cand-filter-clear");
  if (btn) {
    btn.addEventListener("click", () => {
      PLAN.filter.clear();
      renderCandidateList();
      renderStage();
    });
  }
}

function bindApplyCommand() {
  const btn = document.getElementById("ac-copy");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const text = document.getElementById("ac-line").textContent || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "copied";
    } catch {
      // A clipboard write can be refused (insecure origin, denied permission). Say so rather than
      // reporting success — the text is on screen and selectable either way.
      btn.textContent = "select it";
    }
    setTimeout(() => { btn.textContent = "copy"; }, 1600);
  });
}

// The Entry view, as the four questions a reviewer actually asks in the order they ask them:
// what is being proposed, on what evidence, why, and what happens if it is applied. The previous
// form was a flat list of `key_fields` rows, which is the plan's storage shape rather than anyone's
// reading order, and which left `transcripts: [1, 2]` sitting there explaining nothing.
function renderZones(card, candidate) {
  const fields = candidate.key_fields || {};
  const zones = { proposes: [], evidence: [], why: [], applies: [], other: [] };
  for (const [key, value] of Object.entries(fields)) {
    zones[FIELD_ZONE[key] || "other"].push([key, value]);
  }

  // PROPOSES is synthesised, not copied: the plan does not carry a sentence saying what it wants,
  // only an action and an artifact id, and stitching those together is this console's job.
  const proposes = [];
  if (PROMOTION_ACTIONS.includes(candidate.action)) {
    const kind = candidate.action === "promote_to_skill" ? "skill" : "memory file";
    const scope = fields.scope ? ` (${fields.scope} scope)` : "";
    proposes.push(["creates", `a new ${kind}${scope}`]);
    const chars = String(candidate.draft || "").length;
    if (chars) proposes.push(["drafted", `${chars.toLocaleString()} characters — see the Draft tab`]);
  } else if (candidate.action === "prune") {
    proposes.push(["removes", "an existing memory file (archived, never deleted)"]);
  } else if (candidate.action === "keep") {
    proposes.push(["keeps", "an existing file unchanged — nothing to apply"]);
  }

  zoneBlock(card, "proposes", proposes);

  // EVIDENCE is built by hand rather than through `zoneBlock`, because its values are CONTROLS: a
  // transcript name filters the candidate list to everything else that cites the same conversation,
  // which is the question a reviewer asks next ("is this plan actually corroborated, or is it one
  // session repeated?"). On the run this was designed against, one transcript is cited by 8 of 10
  // candidates and another by 3 — a lopsidedness the flat list could not show at all.
  if (zones.evidence.length) {
    const zone = el("div", "zone");
    zone.appendChild(el("div", "zone-label", "evidence"));
    for (const [key, value] of zones.evidence) {
      const line = el("div", "zone-row");
      const isTranscripts = key === "transcripts" || key === "corroborated_by";
      line.appendChild(el("span", "zr-key",
        key === "transcripts" ? "drawn from" : key === "corroborated_by" ? "confirmed by"
          : key.replace(/_/g, " ")));
      const cell = el("span", "zr-value");
      if (isTranscripts && Array.isArray(value)) {
        const names = describeTranscripts(value) || [];
        // One control builder for both shapes: a toggle must SHOW which way it is set (without
        // this the control filtered the list and then looked exactly as it had, so the second
        // click read as a blind guess), and having two builders is two places for that to lapse.
        value.forEach((n, i) => cell.appendChild(transcriptControl(n, names[i] || `transcript ${n}`)));
      } else {
        cell.textContent = String(value);
      }
      line.appendChild(cell);
      zone.appendChild(line);
    }
    card.appendChild(zone);
  }

  zoneBlock(card, "why", zones.why.map(([, v]) => [null, String(v)]), true);

  const applies = zones.applies.map(([key, value]) => {
    if (key !== "target_path") return [key.replace(/_/g, " "), String(value)];
    return ["target", shortTarget(value), String(value)];
  });
  zoneBlock(card, "if applied", applies);

  // OTHER carries the keys this console does not know, which is where a free-form planner puts its
  // evidence when it invents a key name. So it gets link rendering too — the zone a field lands in
  // must not decide whether its transcript references are usable.
  if (zones.other.length) {
    const zone = el("div", "zone");
    zone.appendChild(el("div", "zone-label", "other"));
    for (const [key, value] of zones.other) {
      const line = el("div", "zone-row");
      line.appendChild(el("span", "zr-key", key.replace(/_/g, " ")));
      const cell = el("span", "zr-value");
      if (typeof value === "string") appendProseWithLinks(cell, value);
      else cell.textContent = JSON.stringify(value);
      line.appendChild(cell);
      zone.appendChild(line);
    }
    card.appendChild(zone);
  }
}

function zoneBlock(card, title, rows, prose) {
  if (!rows.length) return;
  const zone = el("div", "zone");
  zone.appendChild(el("div", "zone-label", title));
  for (const row of rows) {
    if (prose) {
      const para = el("p", "zone-prose");
      appendProseWithLinks(para, row[1]);
      zone.appendChild(para);
      continue;
    }
    const line = el("div", "zone-row");
    if (row[2]) line.title = row[2]; // the verbatim value, when the shown one was shortened
    line.appendChild(el("span", "zr-key", row[0] || ""));
    line.appendChild(el("span", "zr-value", row[1]));
    zone.appendChild(line);
  }
  card.appendChild(zone);
}

function bindStageControls() {
  for (const btn of document.querySelectorAll("#stage-switch button")) {
    btn.addEventListener("click", () => { PLAN.view = btn.dataset.view; renderStage(); });
  }
  document.addEventListener("keydown", (ev) => {
    if (ev.target && /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName)) return;
    if (ev.key === "ArrowDown") { ev.preventDefault(); stepCandidate(1); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); stepCandidate(-1); }
  });
}

async function loadPlan(runId) {
  const emptyNote = document.getElementById("plan-empty");
  const candEmpty = document.getElementById("cand-empty");
  PLAN.candidates = [];
  PLAN.selected = 0;
  PLAN.view = "entry";
  renderCandidateList();
  renderStage();
  try {
    const res = await fetch(`/v1/runs/${encodeURIComponent(runId)}`);
    if (!res.ok) {
      emptyNote.textContent = `could not load run ${runId} (HTTP ${res.status})`;
      emptyNote.hidden = false;
      return;
    }
    const body = await res.json();
    const plan = body.plan || { candidates: [], problems: [] };
    if (!plan.candidates || plan.candidates.length === 0) {
      emptyNote.textContent = "this run's plan proposed no candidates.";
      candEmpty.textContent = "no candidates.";
      emptyNote.hidden = false;
      candEmpty.hidden = false;
    } else {
      emptyNote.hidden = true;
      candEmpty.hidden = true;
      PLAN.candidates = plan.candidates;
      PLAN.project = body.project || null;
      PLAN.transcriptIndex = Array.isArray(body.transcript_index) ? body.transcript_index : [];
      renderCandidateList();
      renderStage();
    }
    // Run-level problems belong to the RUN, not to whichever candidate is selected, so they pin
    // under the list rather than riding inside a stage card that changes out from under them.
    if (plan.problems && plan.problems.length) {
      const runProblems = el("div", "run-problems");
      for (const pr of plan.problems) runProblems.appendChild(el("div", "problem-line", pr));
      document.getElementById("cand-list").appendChild(runProblems);
    }
    renderRubric(body.rubric_facts || {}, body.rubric_criteria || []);
    loadTelemetry(runId);
  } catch (err) {
    emptyNote.textContent = "failed to load the plan: " + err;
    emptyNote.hidden = false;
  }
}

// The four ATLAS categories, mirroring `ctx_distillery.rubric._CATEGORY_LENS` — a DISPLAY grouping
// only (the endpoint returns a flat `rubric_facts` dict per its own settled contract).
//
// Anything `trace_facts` returns that no category claims lands in UNCLAIMED rather than being
// dropped. That is not a catch-all for tidiness: `n_transcripts` / `n_transcripts_read` are
// deliberately outside the lens server-side (they describe the run's INPUT, not evidence for any of
// the four criteria as worded) and `rubric.py` says outright that `trace_facts` surfaces them "which
// is what studio's rubric_facts renders". This panel rendered ONLY the lensed keys, so both facts
// were computed, served, and invisible. A fact the server adds must appear here by default.
const CATEGORY_TITLES = {
  TF: "TF · task fidelity",
  TA: "TA · task adherence",
  TG: "TG · task grounding",
  PA: "PA · plan accuracy",
  // NOT "coverage". Named after what it IS — the facts no criterion claims — because the group is
  // a CATCH-ALL, and its membership is whatever `trace_facts` returns that `CATEGORY_LENS` does not
  // list. Calling it "coverage" described today's two members and would have mislabelled the next
  // fact the server adds.
  UNCLAIMED: "outside the four criteria",
};

// Each module's HEADLINE, and the rule that governs it: it states a FACT, never a verdict. This
// rubric is deterministic and reward-free — it decides nothing met/unmet and no field anywhere is a
// score (CLAUDE.md) — so "read -> draft" is allowed (an observation about ordering) and "good" or a
// pass/fail emblem is not. Returns [text, tone] or null; `tone` colours the border only.
function moduleHeadline(category, facts) {
  if (category === "TA") {
    const read = facts.min_read_step;
    const draft = facts.min_draft_step;
    if (!Number.isInteger(draft)) return ["no draft tools", ""];
    // The fact is the RELATION, which is why these two never render as separate stat cards. But the
    // wording says TOOLS, deliberately and at every turn: both numbers are step ids of TOOL CALLS,
    // and the planner also receives `transcripts` and `memory_index` as REPL VARIABLES (they are on
    // `DistillSession`'s signature, and `_INSTRUCTIONS` tells it to print and slice them). A real
    // run here read all three transcripts in full at turns 0-2 — by printing the variable and
    // escalating the text to the sub-LM — then drafted at turn 3 and called `list_memory_files` at
    // step 9. Tool-wise that is `draft` before `read`; in substance the evidence came first. A
    // headline of "draft -> read" states the second thing and is false. Say what is measured.
    if (!Number.isInteger(read)) return ["draft tools only", "warn"];
    if (draft < read) return ["tools: draft first", "warn"];
    return ["tools: read first", "ok"];
  }
  if (category === "TF" && Number.isInteger(facts.n_candidates)) {
    return [facts.n_candidates + " candidates", facts.n_candidates ? "" : "warn"];
  }
  if (category === "TG" && Number.isInteger(facts.n_backed_promotions)) {
    return [facts.n_backed_promotions + " backed", ""];
  }
  if (category === "PA") {
    const n = (Number.isInteger(facts.n_candidate_problems) ? facts.n_candidate_problems : 0) +
      (Number.isInteger(facts.n_bad_skill_scope) ? facts.n_bad_skill_scope : 0);
    return [n ? n + " flagged" : "none flagged", n ? "bad" : "ok"];
  }
  // No headline for UNCLAIMED, deliberately. It carried `<read> / <n> read`, and the FRACTION was
  // the lie: it frames read-tool calls as a proportion of transcripts, which asserts that the rest
  // went unread. The trace cannot support that — the planner reads through the `transcripts` REPL
  // variable and the tool is optional, so a run that read all three in full reports 0 of 3. The
  // numbers stay as plain rows (a server fact must not silently vanish from this panel again);
  // what goes is the arithmetic that turned two true facts into a false claim.
  return null;
}

// Fact key -> what the number ANSWERS. `key.replace(/_/g, " ")` is how this read before, and
// "n non keep" is not a label: it is a variable name with the underscores taken out. The siblings
// all use plain nouns for their stat cells ("turns", "servers", "tool calls", "escalations"), and
// the harder half here is that stripping `n_` is not enough — `non_keep` needs a phrase, because
// what it counts is "candidates proposing a CHANGE", which its own key never says.
//
// The raw key is not lost: it goes on the row's `title`, for a reader cross-referencing the trace
// or `rubric.trace_facts`. Any key not listed falls back to the de-underscored form, so a fact the
// server adds still renders, just plainly.
const FACT_LABEL = {
  n_candidates: "candidates in the plan",
  n_non_keep: "propose a change",
  plan_problems: "run-level problems",
  min_read_step: "first read tool, at step",
  min_draft_step: "first draft tool, at step",
  any_circuit_broken: "breaker tripped",
  n_backed_promotions: "promotions with drafted bytes",
  prune_targets_named: "prunes naming a target",
  n_candidate_problems: "candidates carrying problems",
  n_bad_skill_scope: "skills with an invalid scope",
  n_transcripts: "transcripts ingested",
  n_transcripts_read: "opened with the read tool",
};

function kvRow(key, value) {
  const row = el("div", "kv");
  row.title = key; // the raw fact key, for cross-referencing the trace
  row.appendChild(el("span", null, FACT_LABEL[key] || key.replace(/_/g, " ")));
  let text = "";
  let tone = "";
  if (Array.isArray(value)) {
    text = value.length ? String(value.length) : "none";
    tone = value.length ? " bad" : " zero";
  } else if (typeof value === "boolean") {
    text = value ? "yes" : "no";
    tone = value ? " bad" : " zero";
  } else if (value === null || value === undefined) {
    text = "\u2014";
    tone = " zero";
  } else {
    text = String(value);
    if (value === 0) tone = " zero";
  }
  row.appendChild(el("code", tone.trim() || null, text));
  return row;
}

// category -> the criterion's own `description`, straight from the run's `run_start` meta via
// `GET /v1/runs/{id}`'s `rubric_criteria`. NOT a copy of the four descriptions kept here: a copy
// drifts the moment a criterion is reworded, silently, because nothing compares the two. Reading it
// per run also means an OLD trace explains itself with the rubric it actually ran under.
let CRITERION_DESC = {};
// A second note under the server's own description, for the ONE place where reading the fact at
// face value gets the run wrong. Kept separate from CRITERION_DESC so the server's text is never
// edited client-side — this is an addition beside it, not a rewrite of it.
let CRITERION_TOOL_NOTE = {};

function renderModule(category, title, keys, facts) {
  const module = el("div", "module");
  module.appendChild(el("div", "module-cap"));
  const head = el("div", "module-head");
  head.appendChild(el("h4", null, title));
  const headline = moduleHeadline(category, facts);
  if (headline) head.appendChild(el("span", ("headline " + headline[1]).trim(), headline[0]));
  module.appendChild(head);
  const body = el("div", "module-body");
  // The criterion's own text, BEHIND A DISCLOSURE. Two full paragraphs above every card made the
  // column mostly prose, and a reader scanning four cards for four numbers had to read past all of
  // it every time. With the row labels saying what they count, the prose is reference material:
  // available on the first read, out of the way on the twentieth. `<details>` rather than a tooltip
  // because the text runs to several lines and a tooltip cannot be re-read or selected.
  const desc = CRITERION_DESC[category];
  const caveat = CRITERION_TOOL_NOTE[category];
  if (desc || caveat) {
    const box = document.createElement("details");
    box.className = "module-why";
    const summary = document.createElement("summary");
    summary.textContent = "what this asks";
    box.appendChild(summary);
    if (desc) box.appendChild(el("p", "module-desc", desc));
    if (caveat) box.appendChild(el("p", "module-caveat", caveat));
    body.appendChild(box);
  }
  for (const key of keys) body.appendChild(kvRow(key, facts[key]));
  // The problem TEXT, not just its count — a run-level problem is the thing a reviewer most needs
  // to read, and `plan_problems: 2` on its own tells them only that they must go looking.
  if (Array.isArray(facts.plan_problems) && facts.plan_problems.length && category === "TF") {
    for (const line of facts.plan_problems) body.appendChild(el("p", "module-note", line));
  }
  module.appendChild(body);
  return module;
}

// -- Run telemetry: the meta column's top module -------------------------------------------------
//
// What the run COST and what it did, which lived only inside the Trajectory drawer: a reviewer had
// to open a bottom sheet to learn that a 10-candidate plan took five minutes. Adapted from
// a sibling studio's telemetry module, whose two good ideas are the ELAPSED HEADLINE (one number
// large enough to read without looking for it) and PER-FIELD HELP (a bare count is not
// self-explanatory).
//
// Sourced from `/v1/runs/{id}/iterations`, the same envelope the drawer reads. Every field degrades
// to an em dash rather than to zero: this endpoint answers "what does the trace say", and a trace
// that says nothing must not be rendered as a run that did nothing.
const TELEMETRY_HELP = {
  elapsed: "Wall clock from the run's first event to its last, as recorded in the trace.",
  turns: "Planner turns: one REPL iteration each (reason, run code, read the output).",
  tools: "Read-only and drafting tool calls the planner made. Sub-LM escalations count too.",
  drafts: "Calls to draft_memory_file / draft_skill_file / draft_skill_extra_file: where a "
    + "promotion's bytes (and a skill's supplementary files) come from.",
  reads: "Calls to the evidence tools. Zero is normal — `transcripts` is also a REPL variable, so "
    + "a planner can read every one of them without a tool call.",
};

function fmtSecs(s) {
  if (typeof s !== "number" || !isFinite(s)) return "\u2014";
  if (s >= 60) return `${Math.floor(s / 60)}m${String(Math.round(s % 60)).padStart(2, "0")}s`;
  return `${s.toFixed(1)}s`;
}

/* The meta column is a real column only once it holds something. Toggled here rather than in the
   renderer that fills it, so "is there metadata" is asked in ONE place. */
function syncMeta() {
  const layout = document.getElementById("layout");
  const list = document.getElementById("rubric-list");
  if (!layout || !list) return;
  layout.classList.toggle("no-meta", !list.children.length);
}

function telemetryModule(data) {
  const timeline = Array.isArray(data.timeline) ? data.timeline : [];
  const turns = Array.isArray(data.iterations) ? data.iterations.length : null;
  const tools = timeline.length;
  const drafts = timeline.filter((e) => String(e.tool || "").indexOf("draft") === 0).length;
  const reads = timeline.filter((e) => /^(read|list)/.test(String(e.tool || ""))).length;

  const module = el("div", "module");
  module.appendChild(el("div", "module-cap"));
  const head = el("div", "module-head");
  head.appendChild(el("h4", null, "run telemetry"));
  module.appendChild(head);
  const body = el("div", "module-body");

  const elapsed = el("div", "t-elapsed", fmtSecs(data.total_s));
  elapsed.title = TELEMETRY_HELP.elapsed;
  body.appendChild(elapsed);
  // The trace's OWN caveat about its timing, verbatim — `iterations.py` writes it, and paraphrasing
  // it here would be a second copy of a judgement the server already made.
  if (data.timing_note) body.appendChild(el("p", "t-note", data.timing_note));

  const grid = el("div", "stat-grid");
  for (const [key, label, value] of [
    ["turns", "turns", turns],
    ["tools", "tool calls", tools],
    ["drafts", "drafting calls", drafts],
    ["reads", "read-tool calls", reads],
  ]) {
    const cell = el("div", "stat" + (value === 0 ? " zero" : ""));
    const help = TELEMETRY_HELP[key];
    cell.title = help;                    // fallback: touch, and copying the text
    cell.setAttribute("tabindex", "0");   // hover OR keyboard focus opens the popover
    cell.appendChild(el("div", "sv", value === null || value === undefined ? "\u2014" : String(value)));
    cell.appendChild(el("div", "sl", label));
    // Styled, instant, and it wraps — a native tooltip arrives after a second and truncates, which
    // for a field whose whole problem is that a bare count says nothing is too slow to be the fix.
    const pop = el("div", "stat-pop");
    pop.appendChild(el("b", null, label));
    pop.appendChild(document.createTextNode(help));
    cell.appendChild(pop);
    grid.appendChild(cell);
  }
  body.appendChild(grid);
  module.appendChild(body);
  return module;
}

async function loadTelemetry(runId) {
  const list = document.getElementById("rubric-list");
  try {
    const res = await fetch(`/v1/runs/${encodeURIComponent(runId)}/iterations`);
    if (!res.ok) return;
    const data = await res.json();
    // PREPENDED: the run's cost frames the facts below it, and a reader arriving at this column
    // wants "what happened" before "how it scored".
    list.insertBefore(telemetryModule(data), list.firstChild);
    syncMeta();
  } catch {
    // A telemetry fetch is enrichment. The rubric modules are already rendered and stay usable.
  }
}

function renderRubric(facts, criteria) {
  CRITERION_DESC = {};
  CRITERION_TOOL_NOTE = {};
  for (const c of criteria || []) {
    if (c && c.category && c.description) CRITERION_DESC[c.category] = c.description;
  }
  // The one category with no criterion behind it, by design: `n_transcripts` / `n_transcripts_read`
  // describe the run's INPUT and are deliberately outside `rubric._CATEGORY_LENS` (they are not
  // evidence for or against any of the four criteria as worded). So its note is written here — the
  // no-drift rule above binds the FOUR criteria, which are the server's to define.
  CRITERION_DESC.UNCLAIMED =
    "Facts the four ATLAS criteria do not claim. They are shown so a fact the server adds cannot " +
    "vanish from this panel, not because they score anything.";
  CRITERION_TOOL_NOTE.UNCLAIMED =
    "n_transcripts_read counts calls to the read_transcript_chunk TOOL — not transcripts read. " +
    "The planner also gets `transcripts` as a REPL variable, so a run that read all of them in " +
    "full can report 0. What was actually opened is visible in the trajectory drawer's turns.";
  CRITERION_TOOL_NOTE.TA =
    "Both steps are TOOL-CALL ids. Evidence reached this planner mainly through the `transcripts` " +
    "and `memory_index` REPL variables, which these facts cannot see, so a late first read step " +
    "does not by itself mean the planner drafted blind — check the turns.";
  renderRubricBody(facts);
  // AFTER the body, not after a `return`. This sat below one and was unreachable: the column only
  // ever un-hid because `loadTelemetry` calls `syncMeta` too, so the layout was correct exactly
  // when the `/iterations` fetch happened to succeed — and that fetch's own `catch` is a silent
  // early return, by design, because telemetry is enrichment. A rubric with no telemetry rendered
  // into a column the layout still had collapsed.
  syncMeta();
}

function renderRubricBody(facts) {
  const rubricList = document.getElementById("rubric-list");
  const rubricEmpty = document.getElementById("rubric-empty");
  clear(rubricList);
  if (!facts || Object.keys(facts).length === 0) {
    rubricEmpty.textContent = "no rubric facts for this run.";
    return;
  }
  rubricEmpty.textContent = "";
  const claimed = new Set();
  for (const keys of Object.values(CATEGORY_LENS)) for (const k of keys) claimed.add(k);
  const groups = Object.entries(CATEGORY_LENS).map(([cat, keys]) => [cat, keys.filter((k) => k in facts)]);
  const uncategorised = Object.keys(facts).filter((k) => !claimed.has(k));
  if (uncategorised.length) groups.push(["UNCLAIMED", uncategorised]);
  for (const [cat, keys] of groups) {
    if (!keys.length) continue;
    rubricList.appendChild(renderModule(cat, CATEGORY_TITLES[cat] || cat, keys, facts));
  }
}

// -- the Trajectory drawer (static/trajectory.js) ---------------------------------------------

// The run currently loaded into the page. `startReplay` owns it; the drawer READS it through the
// `getRunId` getter below and never holds a copy.
let currentRunId = null;

// The drawer's error sink. It renders into the Replay feed rather than owning a status line of its
// own: a failed `/iterations` fetch happens BEFORE the drawer opens (a drawer that opened empty
// would be worse than one that does not open), so the message has to land somewhere already on
// screen. Reuses the feed's existing row classes — `fam-bad` is the refusal edge, per DESIGN.md §3.
function trajectoryError(message) {
  const feed = document.getElementById("feed");
  const row = el("div", "feed-row fam-bad");
  row.appendChild(el("span", "fr-badge", "trajectory"));
  const body = el("div", "fr-body");
  body.appendChild(el("div", "fr-line", message));
  row.appendChild(body);
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;
}

// The injected-deps roster is deliberately short and honest: `el`/`clear` are the only helpers this
// file HAS (there is no `esc` — nothing is escaped because nothing becomes markup — no `$`, no
// `ICONS`, no `tint`, no `fmtBytes`), plus the live-run getter and an error sink.
const trajectory = window.Trajectory({
  el: el,
  clear: clear,
  getRunId: () => currentRunId,
  onError: trajectoryError,
});

// -- theme toggle (persisted, mirrors the sibling studios' convention) ------------------------

function initTheme() {
  const stored = localStorage.getItem("ctxd-studio-theme");
  if (stored) document.documentElement.setAttribute("data-theme", stored);
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", current);
    localStorage.setItem("ctxd-studio-theme", current);
  });
}

// -- wire-up -------------------------------------------------------------------------------------

document.getElementById("load").addEventListener("click", () => {
  const runId = document.getElementById("load-id").value.trim();
  if (runId) startReplay(runId);
});
document.getElementById("load-id").addEventListener("keydown", (evt) => {
  if (evt.key === "Enter") document.getElementById("load").click();
});

initTheme();
bindStageControls();
bindApplyCommand();
bindCandidateFilter();
// SEQUENCED, not fired together. `loadRunsList` renders the traces-directory block from
// `TRACES_DIR`, which `loadConfig` sets — two unawaited fetches racing over one piece of state, and
// the loser is silent: nothing re-renders that block, so a `/v1/runs` that resolved first left the
// path hidden for the whole session. Both are tiny and local, so config usually wins and the bug
// only shows up somewhere else. Order it instead of relying on that.
loadConfig().then(loadRunsList);
