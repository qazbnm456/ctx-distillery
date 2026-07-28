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

// The last two segments of a path, with a leading ellipsis when anything was dropped. Two rather
// than one because the last segment alone is almost always `traces` — the same word for every
// project, i.e. no information at all.
function tailPath(value) {
  const parts = String(value).split("/").filter(Boolean);
  if (parts.length <= 2) return String(value);
  return "…/" + parts.slice(-2).join("/");
}

async function loadConfig() {
  try {
    const res = await fetch("/v1/config");
    const cfg = await res.json();
    // The TAIL, not the head. This is a path, and its identifying part is the end
    // (`ctx-distillery/traces`); a head-truncated `/Users/operator/Documents/…` answers nothing. The
    // full value goes on the `title`, so nothing is lost — it is one hover away instead of absent.
    const dir = cfg.traces_dir || "";
    const chip = document.getElementById("traces-dir-chip");
    if (chip) chip.title = dir || "traces directory is unset";
    document.getElementById("traces-dir-value").textContent = dir ? tailPath(dir) : "(unset)";
  } catch {
    document.getElementById("traces-dir-value").textContent = "(unavailable)";
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
    hint.textContent = (body.runs || []).length
      ? `${body.runs.length} run(s) available`
      : "no trace files found — check /v1/config's traces_dir";
  } catch {
    hint.textContent = "could not reach /v1/runs";
  }
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
    // judgement-only task with five tools, so it gets its own readable line, not a JSON dump.
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
  renderCandidateList();
  renderStage();
  clear(document.getElementById("rubric-list"));
  document.getElementById("rubric-empty").textContent = "—";

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

const PLAN = { candidates: [], selected: 0, view: "entry" };

function candidateLabel(candidate) {
  const k = candidate.key_fields || {};
  const named = k.target_path || k.name || candidate.artifact_id || "";
  return String(named).split("/").pop() || "";
}

function renderCandidateList() {
  const list = document.getElementById("cand-list");
  const count = document.getElementById("cand-count");
  clear(list);
  count.textContent = PLAN.candidates.length ? `(${PLAN.candidates.length})` : "";
  PLAN.candidates.forEach((candidate, index) => {
    const state = candidateState(candidate);
    const item = el("button", "cand-item state-" + state);
    item.type = "button";
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(index === PLAN.selected));
    if (index === PLAN.selected) item.classList.add("on");
    item.appendChild(el("span", "ci-index", String(index)));
    item.appendChild(el("span", "ci-action", (candidate.action + " " + candidateLabel(candidate)).trim()));
    // One glyph carries the only thing a reviewer scans a list for: would `apply_plan` take it.
    if (state === "blocked") item.appendChild(el("span", "ci-mark bad", "\u26a0"));
    else if (state === "backed") item.appendChild(el("span", "ci-mark ok", "\u25c6"));
    item.addEventListener("click", () => selectCandidate(index));
    list.appendChild(item);
  });
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
    // One line per key field, not one JSON blob: `reason` is a paragraph of model prose and was the
    // single longest thing in the old row, unreadable inside `JSON.stringify` of the whole object.
    if (candidate.key_fields && Object.keys(candidate.key_fields).length) {
      for (const [k, v] of Object.entries(candidate.key_fields)) {
        const line = el("div", "candidate-key-fields");
        line.appendChild(el("span", "kf-key", k));
        line.appendChild(el("span", "kf-value", typeof v === "string" ? v : JSON.stringify(v)));
        card.appendChild(line);
      }
    }
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

function bindStageControls() {
  for (const btn of document.querySelectorAll("#stage-switch button")) {
    btn.addEventListener("click", () => { PLAN.view = btn.dataset.view; renderStage(); });
  }
  document.addEventListener("keydown", (ev) => {
    if (ev.target && /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName)) return;
    if (ev.key === "ArrowDown") { ev.preventDefault(); selectCandidate(PLAN.selected + 1); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); selectCandidate(PLAN.selected - 1); }
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

function kvRow(key, value) {
  const row = el("div", "kv");
  row.appendChild(el("span", null, key.replace(/_/g, " ")));
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
  // What this category ASKS, before the numbers that answer it. Without it the column is a list of
  // bare identifiers — `min_draft_step: 1` means nothing to a reviewer who did not write the rubric,
  // and "what am I looking at" is the first question this panel drew.
  if (CRITERION_DESC[category]) body.appendChild(el("p", "module-desc", CRITERION_DESC[category]));
  if (CRITERION_TOOL_NOTE[category]) {
    body.appendChild(el("p", "module-caveat", CRITERION_TOOL_NOTE[category]));
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
  return renderRubricBody(facts);
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
loadConfig();
loadRunsList();
