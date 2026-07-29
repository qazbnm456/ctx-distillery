/* Behavioural contract tests for `static/app.js` (run: `node tests/app.test.js`).

   This file exists because `static-contract.test.js` reads `app.js` as TEXT and `trajectory.test.js`
   covers the drawer only, so the plan-review path — the console's actual product — had no
   behavioural coverage at all. It was a live infinite loop for one full review cycle before anyone
   noticed, on a run whose evidence happens to be written as prose.

   Same shape as `trajectory.test.js`: a hand-rolled stub DOM, plain CommonJS, no npm. `app.js` is a
   top-level script rather than a factory, so it is loaded through `new Function` with a trailing
   `return` that hands back the internals under test. */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

// --- minimal stub DOM ---------------------------------------------------------------------------

function stubEl(tag) {
  return {
    tagName: tag || "div", className: "", textContent: "", title: "", hidden: false, type: "",
    value: "", children: [], handlers: {},
    style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); },
      toggle(c, on) { if (on) this._set.add(c); else this._set.delete(c); },
      contains(c) { return this._set.has(c); },
    },
    appendChild(child) { this.children.push(child); return child; },
    insertBefore(child) { this.children.unshift(child); return child; },
    removeChild(child) { this.children = this.children.filter((c) => c !== child); return child; },
    get firstChild() { return this.children[0] || null; },
    addEventListener(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); },
    click() { (this.handlers.click || []).forEach((fn) => fn()); },
    setAttribute() {}, removeAttribute() {}, querySelectorAll() { return []; },
    scrollIntoView() {},
  };
}

function load() {
  const nodes = {};
  global.document = {
    createElement: stubEl,
    createTextNode: (text) => ({ tagName: "#text", textContent: String(text), children: [] }),
    getElementById: (id) => nodes[id] || (nodes[id] = stubEl("div")),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    documentElement: { setAttribute() {}, getAttribute: () => null },
  };
  global.localStorage = { getItem: () => null, setItem() {} };
  global.window = {
    Trajectory: () => ({ open() {}, reset() {}, showHandle() {} }),
    matchMedia: () => ({ matches: false }),
  };
  global.navigator = {};
  global.fetch = async () => ({ ok: true, json: async () => ({}) });
  global.EventSource = function () { return { addEventListener() {}, close() {} }; };
  const src = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
  const api = new Function(
    src + "\nreturn { PLAN, renderZones, renderCandidateList, transcriptsCitedBy, " +
    "appendProseWithLinks, citingTranscript, renderRubric, syncMeta };"
  )();
  return { api, byId: (id) => document.getElementById(id) };
}

// Every text node and label under a rendered tree, so a test can assert what reached the page.
function allText(node) {
  let out = node.textContent ? [node.textContent] : [];
  (node.children || []).forEach((c) => { out = out.concat(allText(c)); });
  return out;
}
function buttonsIn(node) {
  let out = (node.handlers && (node.handlers.click || []).length) ? [node] : [];
  (node.children || []).forEach((c) => { out = out.concat(buttonsIn(c)); });
  return out;
}

// --- harness ------------------------------------------------------------------------------------

let failed = 0;
function test(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (e) { failed++; console.error("  FAIL " + name + "\n       " + e.message); }
}

// --- fixtures: the two evidence SHAPES two real runs produced ------------------------------------

// Run A wrote its evidence as integer lists under the keys this console knows.
const LIST_SHAPED = {
  action: "promote_to_memory", artifact_id: "a1", draft: "---\nname: x\n---\nbody", draft_ok: true,
  problems: [], key_fields: { transcripts: [0, 2], corroborated_by: [1], reason: "durable fact" },
};

// Run B invented a key (`sources`) and wrote the same information as PROSE. This is the shape that
// exposed the loop, and the shape a free-form `key_fields` can produce at any time.
const PROSE_SHAPED = {
  action: "promote_to_skill", artifact_id: "a2", draft: "---\nname: y\n---\nbody", draft_ok: true,
  problems: [],
  key_fields: {
    scope: "project",
    sources: "transcripts[0],[2] (siblings under session 30f8147f, not independent of each other)",
    reason: "measured 0.80 (transcript[1]) supersedes the earlier figure",
  },
};

function seed(api, candidates) {
  api.PLAN.candidates = candidates;
  api.PLAN.transcriptIndex = [
    { kind: "session", id: "30f8147f-aaaa", session: "30f8147f-aaaa", parent: "session:30f8147f" },
    { kind: "subagent", id: "a00d251c-bbbb", session: "30f8147f-aaaa", parent: "session:30f8147f" },
    { kind: "subagent", id: "a022be95-cccc", session: "30f8147f-aaaa", parent: "session:30f8147f" },
  ];
  api.PLAN.filter = new Set();
  api.PLAN.picked = new Set();
  api.PLAN.selected = 0;
}

// --- tests ---------------------------------------------------------------------------------------

console.log("app.js contract");

test("prose evidence renders WITHOUT hanging (the shared-regex loop)", () => {
  // THE REGRESSION. `TX_RUN` / `TX_ONE` are module-level `/g` regexes, and the render is re-entrant:
  // `appendProseWithLinks` iterates the runs in ONE candidate's prose and, per reference, calls
  // `citingTranscript` -> `transcriptsCitedBy`, which scans EVERY OTHER candidate's prose with the
  // same objects. Driven by `exec`, the inner scan reset `lastIndex` and the outer loop restarted
  // forever: measured as a 4 GB heap exhaustion, not a slow page, on the first real run whose
  // evidence was prose. Two prose candidates are the minimum that re-enters, so one is not enough.
  const { api } = load();
  seed(api, [PROSE_SHAPED, PROSE_SHAPED, LIST_SHAPED]);
  const card = stubEl("div");
  api.renderZones(card, PROSE_SHAPED);   // hangs here if the state is shared again
  // The prose is split across text nodes and controls, so assert on the PARTS: the surrounding
  // words survive as text, and each written index became its own control.
  const text = allText(card).join("");
  assert.ok(text.includes("siblings under session"), "the prose around the reference must survive");
  assert.ok(text.includes("transcripts"), "and the qualifying word itself");
  const labels = buttonsIn(card).map((b) => b.textContent).filter((s) => /^\[\d+\]$/.test(s));
  assert.deepStrictEqual(labels.sort(), ["[0]", "[1]", "[2]"], "every written index is a control");
});

test("a qualified transcript reference in prose becomes a control", () => {
  const { api } = load();
  seed(api, [PROSE_SHAPED]);
  const cell = stubEl("span");
  const matched = api.appendProseWithLinks(cell, "see transcripts[0],[2] for this");
  assert.ok(matched, "the run must be recognised");
  assert.strictEqual(buttonsIn(cell).length, 2, "one control per written index");
});

test("a BARE bracket is never a control, and an out-of-range index is not either", () => {
  // A wrong link is worse than no link: it invites filtering by evidence that was never claimed.
  // `[3]` unqualified could be a footnote or an array index; `transcripts[9]` names a transcript
  // this run does not have, and a model-supplied number must be range-checked before it can be
  // clicked.
  const { api } = load();
  seed(api, [PROSE_SHAPED]);
  for (const prose of ["see item [3] and array[7] below", "transcripts[9] does not exist"]) {
    const cell = stubEl("span");
    api.appendProseWithLinks(cell, prose);
    assert.strictEqual(buttonsIn(cell).length, 0, prose);
  }
});

test("a range links only the indices actually written", () => {
  const { api } = load();
  seed(api, [PROSE_SHAPED]);
  const cell = stubEl("span");
  api.appendProseWithLinks(cell, "transcripts[0]-[2] were read");
  const labels = buttonsIn(cell).map((b) => b.textContent);
  assert.deepStrictEqual(labels, ["[0]", "[2]"], "1 is implied by the range, not written, so not linked");
});

test("citations are read from BOTH shapes, so the filter matches either", () => {
  // The filter and the links must agree about what a candidate cites. They were keyed on the list
  // shape alone, so a prose-shaped run filtered to nothing while still rendering links.
  const { api } = load();
  seed(api, [LIST_SHAPED, PROSE_SHAPED]);
  assert.deepStrictEqual([...api.transcriptsCitedBy(LIST_SHAPED)].sort(), [0, 1, 2]);
  assert.deepStrictEqual([...api.transcriptsCitedBy(PROSE_SHAPED)].sort(), [0, 1, 2]);
  assert.deepStrictEqual(api.citingTranscript(0), [0, 1], "both candidates cite transcript 0");
});

test("a keep candidate cannot be ticked for apply", () => {
  // `apply_plan` returns STATUS_NOOP for `keep` — "there is nothing to apply". A tick for it would
  // invent an action the writer does not have.
  const { api, byId } = load();
  const keep = { action: "keep", artifact_id: null, draft: "", draft_ok: null, problems: [],
                 key_fields: { target_path: "/memory/x.md", reason: "unchanged" } };
  seed(api, [keep, LIST_SHAPED]);
  api.renderCandidateList();
  const boxes = byId("cand-list").children.map((row) => row.children[0]);
  assert.strictEqual(boxes[0].disabled, true, "keep is not tickable");
  assert.match(boxes[0].title, /no-op/, "and the row says why");
  assert.strictEqual(boxes[1].disabled, false, "a promotion still is");
});

test("the meta column collapses when empty and expands when the RUBRIC fills it", () => {
  // Both halves were broken, and both were invisible on the happy path.
  //
  // `syncMeta()` sat after a `return` in `renderRubric` — unreachable. The column still expanded,
  // but only because `loadTelemetry` calls `syncMeta` as well, so the layout was right exactly when
  // the `/iterations` fetch happened to succeed. That fetch's `catch` is a deliberate silent early
  // return (telemetry is enrichment), so a rubric with no telemetry rendered into a collapsed
  // column. Asserting through `renderRubric` ALONE is what pins it: a test that also ran telemetry
  // would have passed against the bug.
  const { api, byId } = load();
  const layout = byId("layout");
  layout.classList.add("no-meta");

  api.renderRubric(
    { n_candidates: 2, n_non_keep: 1, plan_problems: [] },
    [{ name: "x", category: "TF", description: "did the planner exercise judgement" }]
  );
  assert.ok(!layout.classList.contains("no-meta"), "a rendered rubric is a real column");
  assert.ok(byId("rubric-list").children.length, "and it really did render something");

  // Emptying it must ask again — loading a SECOND run clears this list, and the three-track grid
  // used to stay in place around an empty aside until telemetry arrived.
  byId("rubric-list").children = [];
  api.syncMeta();
  assert.ok(layout.classList.contains("no-meta"), "an empty column is not a column");
});

process.exit(failed ? 1 : 0);
