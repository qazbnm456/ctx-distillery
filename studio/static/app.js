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

async function loadConfig() {
  try {
    const res = await fetch("/v1/config");
    const cfg = await res.json();
    document.getElementById("traces-dir-value").textContent = cfg.traces_dir || "(unset)";
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
        `transcripts=${data.transcripts} memory_artifacts=${data.memory_artifacts} ` +
          `rubric_criteria=${(data.rubric || {}).criteria || 0}`
      )
    );
  } else {
    body.appendChild(el("div", "fr-line", eventName));
  }
  row.appendChild(body);
  return row;
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
  clear(document.getElementById("plan-list"));
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

// -- the PLAN panel: the money shot — each candidate's draft next to its plan entry ------------

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

function renderCandidate(candidate, index) {
  const row = el("div", "candidate-row");
  const state = candidateState(candidate);
  row.classList.add("state-" + state);

  const head = el("div", "candidate-head");
  head.appendChild(el("span", "candidate-index", `#${index}`));
  head.appendChild(el("span", "candidate-action action-" + candidate.action, candidate.action));
  if (candidate.artifact_id) head.appendChild(el("span", "candidate-artifact", candidate.artifact_id));
  if (candidate.draft_ok !== null && candidate.draft_ok !== undefined) {
    head.appendChild(el("span", candidate.draft_ok ? "chip-ok" : "chip-bad", candidate.draft_ok ? "draft ok" : "draft failed"));
  }
  row.appendChild(head);

  if (candidate.key_fields && Object.keys(candidate.key_fields).length) {
    row.appendChild(el("div", "candidate-key-fields", JSON.stringify(candidate.key_fields)));
  }

  if (candidate.draft) {
    const pre = document.createElement("pre");
    pre.className = "candidate-draft";
    pre.textContent = candidate.draft; // NEVER innerHTML — untrusted drafted model output.
    row.appendChild(pre);
  }

  if (candidate.problems && candidate.problems.length) {
    const problems = el("div", "candidate-problems");
    for (const p of candidate.problems) problems.appendChild(el("div", "problem-line", p));
    row.appendChild(problems);
  }

  // The refusal marker goes LAST so it reads as the verdict on everything above it. It is the only
  // surface that makes the empty-promotion-draft case visible at all.
  const blocker = applyBlocker(candidate);
  if (blocker) row.appendChild(el("div", "candidate-blocked", "⚠ " + blocker));
  return row;
}

async function loadPlan(runId) {
  const emptyNote = document.getElementById("plan-empty");
  const list = document.getElementById("plan-list");
  clear(list);
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
      emptyNote.hidden = false;
    } else {
      emptyNote.hidden = true;
      plan.candidates.forEach((c, i) => list.appendChild(renderCandidate(c, i)));
    }
    if (plan.problems && plan.problems.length) {
      const runProblems = el("div", "run-problems");
      for (const p of plan.problems) runProblems.appendChild(el("div", "problem-line", p));
      list.appendChild(runProblems);
    }
    renderRubric(body.rubric_facts || {});
  } catch (err) {
    emptyNote.textContent = "failed to load the plan: " + err;
    emptyNote.hidden = false;
  }
}

function renderRubric(facts) {
  const rubricList = document.getElementById("rubric-list");
  const rubricEmpty = document.getElementById("rubric-empty");
  clear(rubricList);
  if (!facts || Object.keys(facts).length === 0) {
    rubricEmpty.textContent = "no rubric facts for this run.";
    return;
  }
  rubricEmpty.textContent = "";
  for (const [category, keys] of Object.entries(CATEGORY_LENS)) {
    const block = el("div", "rubric-category");
    block.appendChild(el("div", "rubric-category-label", category));
    for (const key of keys) {
      if (!(key in facts)) continue;
      const line = el("div", "rubric-fact");
      line.appendChild(el("span", "rubric-fact-key", key));
      line.appendChild(el("span", "rubric-fact-value", JSON.stringify(facts[key])));
      block.appendChild(line);
    }
    rubricList.appendChild(block);
  }
}

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
loadConfig();
loadRunsList();
