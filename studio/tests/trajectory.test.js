/* Contract tests for the Trajectory drawer FACTORY (run: `node tests/trajectory.test.js`).

   `trajectory.js` is a DOM factory, not a pure core, so — like the sibling studios' version of this
   file — we stub a minimal DOM and assert the DEPENDENCY-INJECTION contract rather than pixels:
   building it runs every create/wiring path, a missing injected dep is refused loudly, the facade
   shape is stable, and the live `getRunId` GETTER is actually re-consulted instead of snapshotted.

   Adapted, not ported, in two places:

   * The siblings inject a `$` and their test memoizes it so the assertions observe the very element
     the factory is mutating. `app.js` has no `$` — it calls `document.getElementById` directly, and
     the drawer follows the house style — so the memoization moved into the stub `document` instead
     of into the deps roster. Same property, one fewer invented helper.
   * They also stub `ReplayCore` and assert a `refreshTransport` facade entry. Neither exists here:
     there is no transport, and the twelve lines of stop-walking the drawer does use are inlined.

   Plain CommonJS, `require("assert")`, the same tiny harness — no npm, no package.json, no
   node_modules. The runner is async only because `open()` is (it fetches `/iterations`). */
"use strict";
const assert = require("assert");

// --- minimal stub DOM -------------------------------------------------------------------------

function stubEl(tag) {
  return {
    tagName: tag || "div", className: "", textContent: "", hidden: false, open: false, type: "",
    scrollTop: 0, offsetHeight: 0, children: [], handlers: {},
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) { this.children = this.children.filter((c) => c !== child); return child; },
    get firstChild() { return this.children[0] || null; },
    addEventListener(name, fn) { (this.handlers[name] = this.handlers[name] || []).push(fn); },
    click() { (this.handlers.click || []).forEach((fn) => fn()); },
    setAttribute() {}, removeAttribute() {}, value: "",
    // A REAL class set, not four no-ops. The no-op version could not tell a test that search had
    // marked a turn `match` or dimmed it, so the feature would have shipped observable only by eye.
    // Same rule as `style` below: a double that silently accepts every call cannot fail, which is
    // the one thing a double must be able to do.
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); },
      toggle(c, on) { if (on === undefined) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); }
                      else if (on) { this._set.add(c); } else { this._set.delete(c); } },
      contains(c) { return this._set.has(c); },
    },
    // A real element always has `style`, and `style` always has `setProperty`; the stub had
    // neither, so the first production code to size a node inline (the timeline's proportional
    // segment width) and the first to set a custom property (its family hue) each failed here, in
    // five tests that are not about styling at all. A stub that omits a universal DOM member does
    // not test less — it fails WRONGLY, and a wrongly-failing test invites removing the feature
    // rather than completing the double. `props` is readable so a test can assert on width/hue.
    style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
  };
}

// MEMOIZING: the same id always returns the same node, so a test can read back what the factory
// wrote. `nodes` is swapped per `make()` so each factory gets a fresh page.
let nodes = {};
global.window = {};
global.document = {
  getElementById(id) { return nodes[id] || (nodes[id] = stubEl("div")); },
  addEventListener() {},
};

require("../static/trajectory.js"); // sets window.Trajectory

// The two real helpers from app.js, transcribed — the deps roster is exactly {el, clear, getRunId,
// onError}, and these two are the honest stand-ins for what app.js actually passes.
function el(tag, className, text) {
  const node = stubEl(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function make(over) {
  nodes = {};
  const deps = Object.assign({ el, clear, getRunId: () => "run-1", onError() {} }, over || {});
  return { t: window.Trajectory(deps), byId: (id) => document.getElementById(id) };
}

// Every descendant's textContent, so a test can assert what actually reached the page.
function allText(node) {
  let out = node.textContent ? [node.textContent] : [];
  (node.children || []).forEach((c) => { out = out.concat(allText(c)); });
  return out;
}

// --- fixtures: the two MEASURED shapes `iterations.py` is pinned against ------------------------

// per_turn_timing FALSE — the offline harness's 0.0019s span, i.e. EVERY trace this workspace's own
// test suite can produce. `turn_index` is absent from every timeline entry, which is precisely the
// case a turn-grouped-only drawer would render blank.
const OFFLINE = {
  started_at: 1785226770.373, total_s: 0.0026, per_turn_timing: false,
  timing_note: "Per-turn timing isn't available for this trace (turns weren't live-stamped, or the " +
    "run was too short to span); the timeline carries the live tool/sub-LM calls — where time went.",
  initial: {
    project: "secret-client-project", transcripts: 1, memory_artifacts: 0,
    models: { planner: "gpt_oss_120B", drafter: "gpt_oss_120B" }, interpreter: "pyodide",
    rubric_criteria: 4, max_iterations: 8, max_llm_calls: 6,
  },
  iterations: [
    { turn: 0, index: 0, reasoning: "look around", code: "list_memory_files()", output: "[]" },
    { turn: 1, index: 1, reasoning: "submit", code: "SUBMIT(plan)", output: "FINAL" },
  ],
  timeline: [
    { entry_kind: "tool", tool: "list_memory_files", ok: true, duration_s: 0.0009, label: "list memory",
      count: 2, kinds: ["memory", "skill"], seq: 0, rel_s: 0.0009 },
    { entry_kind: "tool", tool: "read_memory_file", ok: true, duration_s: 0.0003, label: "read memory",
      name: "stale.md", kind: "memory", chars: 812, truncated: false, seq: 1, rel_s: 0.0012 },
  ],
};

// The one thing `iterations.py` deliberately does NOT scrub, and the reason this drawer must render
// with textContent: a turn's `output` is the REPL's echo of whatever the planner printed.
const HOSTILE_OUTPUT =
  "Memory draft result: {'draft': '---\\nname: x\\n---\\n<img src=x onerror=alert(1)> " + "A".repeat(300) + "'}";

function liveTrace() {
  return {
    started_at: 1785226770.373, total_s: 23.451, per_turn_timing: true,
    timing_note: "Per-turn timing is live — captured as each turn was parsed; the tool timeline " +
      "shows where time went within the turns.",
    initial: OFFLINE.initial,
    iterations: [
      { turn: 0, index: 0, reasoning: "print the transcripts first", code: "pprint(transcripts)",
        output: HOSTILE_OUTPUT, rel_s: 2.99, duration_s: 5.317 },
      { turn: 1, index: 1, reasoning: "draft the rules", code: "draft_memory_file(...)", output: "ok",
        rel_s: 8.307, duration_s: 15.109 },
    ],
    timeline: [
      { entry_kind: "tool", tool: "draft_memory_file", ok: true, duration_s: 10.574,
        label: "draft memory", artifact_id: "2f6d40f59078", kind: "memory", draft_chars: 180,
        errors: [], endpoint_error: null, circuit_broken: false, topic: "Release freeze rule",
        memory_type: "project", seq: 0, rel_s: 10.574, turn_index: 1 },
    ],
  };
}

function stubFetch(payload, over) {
  const calls = [];
  global.fetch = async (url) => {
    calls.push(url);
    return Object.assign({ ok: true, status: 200, json: async () => payload }, over || {});
  };
  return calls;
}

// --- harness ------------------------------------------------------------------------------------

let failed = 0;
async function test(name, fn) {
  try { await fn(); console.log("  ok   " + name); }
  catch (e) { failed++; console.error("  FAIL " + name + "\n       " + e.message); }
}

(async function main() {

  await test("build + wiring run without a ReferenceError (the missing-injected-dep guard)", () => {
    const { t } = make();
    assert.ok(t, "factory returned nothing");
  });

  await test("facade shape is exactly { open, reset, showHandle }", () => {
    const { t } = make();
    assert.deepStrictEqual(Object.keys(t).sort(), ["open", "reset", "showHandle"]);
    ["open", "reset", "showHandle"].forEach((k) => assert.strictEqual(typeof t[k], "function", k));
  });

  await test("a missing injected dep throws at CONSTRUCTION, not silently at first click", () => {
    // The siblings get this for free (they call an injected `$` while building their element map).
    // This drawer reads the DOM the way app.js does, so nothing would touch a dep until the handle
    // was clicked — the check has to be explicit, and this is what pins it.
    for (const name of ["el", "clear", "getRunId", "onError"]) {
      assert.throws(() => make({ [name]: undefined }),
        new RegExp("missing injected dependency `" + name + "`"), "missing `" + name + "` must throw");
    }
  });

  await test("showHandle consults the getRunId GETTER, and re-consults it (never a snapshot)", () => {
    let runId = null;
    const { t, byId } = make({ getRunId: () => runId });
    const handle = byId("traj-handle");
    handle.hidden = true;
    t.showHandle();
    assert.strictEqual(handle.hidden, true, "handle must stay hidden while getRunId() is empty");
    runId = "run-2";                      // the page loads a run AFTER the factory was built
    t.showHandle();
    assert.strictEqual(handle.hidden, false, "handle must appear once getRunId() returns an id");
  });

  await test("reset hides the handle and runs its stop/close paths without throwing", () => {
    const { t, byId } = make();
    const handle = byId("traj-handle");
    handle.hidden = false;
    t.reset();
    assert.strictEqual(handle.hidden, true, "reset should hide the handle");
  });

  await test("open() fetches /iterations for the run id current at CLICK time", async () => {
    let runId = "run-1";
    const { t } = make({ getRunId: () => runId });
    const calls = stubFetch(OFFLINE);
    runId = "run-9";                      // changed after construction — the getter must see it
    await t.open();
    assert.deepStrictEqual(calls, ["/v1/runs/run-9/iterations"]);
  });

  await test("open() with no run id does not fetch at all", async () => {
    const { t } = make({ getRunId: () => null });
    const calls = stubFetch(OFFLINE);
    await t.open();
    assert.deepStrictEqual(calls, []);
  });

  await test("the timeline renders in FULL when per_turn_timing is false (no turn_index anywhere)", async () => {
    // THE regression this drawer's design exists to avoid: `turn_index` is absent from every entry
    // on every trace the offline harness produces, so a pane fed by turn grouping would be empty.
    const { t, byId } = make();
    stubFetch(OFFLINE);
    await t.open();
    assert.ok(OFFLINE.timeline.every((e) => e.turn_index === undefined), "fixture must have no turn_index");
    assert.strictEqual(byId("traj-timeline").children.length, 2, "both calls must render");
    const labels = byId("traj-timeline").children.map((n) => allText(n)[0]);
    assert.deepStrictEqual(labels, ["list memory", "read memory"]);
    // Init + one nav entry per turn.
    assert.strictEqual(byId("traj-steps").children.length, 3);
    assert.deepStrictEqual(byId("traj-steps").children.map((n) => allText(n)[0]),
      ["Init", "Turn 0", "Turn 1"]);
  });

  await test("the timing note is rendered VERBATIM, not paraphrased", async () => {
    const { t, byId } = make();
    stubFetch(OFFLINE);
    await t.open();
    const note = byId("traj-note");
    assert.strictEqual(note.hidden, false);
    assert.ok(allText(note).includes(OFFLINE.timing_note), "the server's own sentence must appear as-is");
    assert.match(note.className, /\binfo\b/, "per_turn_timing=false picks the neutral tag");
  });

  await test("a turn's reasoning is prose and its code/output land in the REPL block as TEXT", async () => {
    // The whole point of the drawer, and the whole reason it may not use innerHTML: this string is
    // the REPL's verbatim echo of a drafted body, tag and all.
    const { t, byId } = make();
    stubFetch(liveTrace());
    await t.open();
    byId("traj-steps").children[1].click();     // select Turn 0
    const text = allText(byId("traj-detail"));
    assert.ok(text.includes("print the transcripts first"), "the turn's reasoning must render");
    assert.ok(text.includes("pprint(transcripts)"), "the turn's code must render");
    assert.ok(text.includes(HOSTILE_OUTPUT), "the turn's output must render verbatim, as text");
    assert.ok(text.some((s) => s.indexOf("rendered as text, never markup") >= 0),
      "the REPL block must carry the honest caption about what this text can contain");
  });

  await test("selecting a timeline entry shows its fields and the gap-attribution caveat", async () => {
    const { t, byId } = make();
    stubFetch(liveTrace());
    await t.open();
    // Select by what the row IS, not by position and not by "has a handler": the timeline
    // interleaves "T<n> ▸" turn markers between turn groups, a positional index silently picks one,
    // and they are clickable too (they open their turn), so a handler test picks them up as well.
    const segs = byId("traj-timeline").children.filter((c) => /\bseg\b/.test(c.className));
    assert.ok(segs.length, "the timeline must render at least one clickable entry");
    segs[0].click();
    const text = allText(byId("traj-detail"));
    assert.ok(text.includes("draft memory"), "the entry's label must head the pane");
    assert.ok(text.includes("2f6d40f59078"), "the allowlisted fields must render");
    assert.ok(text.some((s) => s.indexOf("not instrumented tool time") >= 0),
      "duration_s must never be presented as an instrumented tool latency");
  });

  await test("the timeline encodes DURATION AS WIDTH, floored, with a family hue per tool", async () => {
    // The point of the strip: on the run this was rebuilt against, ONE of nine calls took 84.3% of
    // the wall clock while the rest took 2-8s, which a column of equal-height rows cannot express.
    // The ratio here (2s vs 38s) is deliberately lopsided enough that the SHORT call's natural
    // width (2/40 * 720 = 36px) falls under the 108px floor, so one assertion covers the
    // proportion and the other covers the floor that keeps a fast call readable. An earlier draft
    // of this test used 8s vs 32s and asserted the floor on a segment that computes to 144px — the
    // test caught its own arithmetic. Without it, `width is time` is a claim in a comment.
    const trace = liveTrace();
    trace.timeline = [
      { entry_kind: "tool", tool: "read_transcript_chunk", ok: true, duration_s: 2, label: "read",
        seq: 0, rel_s: 2, turn_index: 0, errors: [], endpoint_error: null, circuit_broken: false },
      { entry_kind: "tool", tool: "draft_memory_file", ok: true, duration_s: 38, label: "draft",
        seq: 1, rel_s: 40, turn_index: 0, errors: [], endpoint_error: null, circuit_broken: false },
    ];
    const { t, byId } = make({});
    stubFetch(trace);
    await t.open();
    const segs = byId("traj-timeline").children.filter((c) => /\bseg\b/.test(c.className));
    assert.strictEqual(segs.length, 2, "both calls must render");
    const px = (n) => Number(String(n.style.width).replace("px", ""));
    assert.ok(px(segs[1]) > px(segs[0]), "the longer call must be the wider segment");
    assert.strictEqual(px(segs[1]), Math.round((38 / 40) * 720), "width is its share of the run");
    assert.strictEqual(px(segs[0]), 108, "a short call floors at 108px rather than vanishing");
    assert.strictEqual(segs[0].style.props["--fam"], "var(--fam-read)", "a read call is read-hued");
    assert.strictEqual(segs[1].style.props["--fam"], "var(--fam-draft)", "a draft call is draft-hued");
    assert.strictEqual(byId("traj-axis-end").textContent, "40.0s", "the axis must end at the total");
  });

  await test("a turn marker is a working BUTTON, not a divider that looks like one", async () => {
    // Shipped once as a plain `div`: it sat in the strip looking exactly like the clickable
    // segments beside it and did nothing when pressed. A control that LOOKS interactive and is not
    // is worse than no control, so the marker's handler is pinned, and pinned to open the same turn
    // its label names — a marker that opened the wrong turn would still pass a "has a handler" test.
    const trace = liveTrace();
    trace.timeline = [
      { entry_kind: "tool", tool: "read_memory_file", ok: true, duration_s: 1, label: "read",
        seq: 0, rel_s: 1, turn_index: 0, errors: [], endpoint_error: null, circuit_broken: false },
      { entry_kind: "tool", tool: "draft_memory_file", ok: true, duration_s: 1, label: "draft",
        seq: 1, rel_s: 2, turn_index: 1, errors: [], endpoint_error: null, circuit_broken: false },
    ];
    const { t, byId } = make({});
    stubFetch(trace);
    await t.open();
    const marks = byId("traj-timeline").children.filter((c) => /turn-mark/.test(c.className));
    assert.strictEqual(marks.length, 2, "one marker per turn boundary");
    assert.ok((marks[1].handlers.click || []).length, "a marker must be clickable");
    marks[1].click();
    // Turn 1's own reasoning, i.e. the marker opened the turn it names rather than any turn.
    assert.ok(allText(byId("traj-detail")).some((s) => s.indexOf("draft the rules") >= 0),
      "clicking T1 must open turn 1");
  });

  await test("search matches a turn's OUTPUT, dims the misses, and counts the hits", async () => {
    // Searching titles only would be near-useless here: a turn's `output` is the REPL echo and ran
    // 16,038 characters on the run this was built against, which is exactly where the thing you are
    // looking for lives. So the haystack is reasoning + code + output, and this pins that by putting
    // the needle ONLY in `output` — a title/reasoning-only implementation goes red.
    const trace = liveTrace();
    trace.iterations = [
      { turn: 0, index: 0, reasoning: "look around", code: "list_memory_files()", output: "[]" },
      { turn: 1, index: 1, reasoning: "keep going", code: "pprint(x)", output: "NEEDLE_IN_OUTPUT" },
    ];
    const { t, byId } = make({});
    stubFetch(trace);
    await t.open();
    const steps = byId("traj-steps").children.filter((c) => /tstep/.test(c.className));
    assert.ok(steps.length >= 2, "the turns must render as steps");

    byId("traj-search").value = "needle_in_output";
    (byId("traj-search").handlers.input || []).forEach((fn) => fn());
    const matched = steps.filter((s) => s.classList.contains("match"));
    assert.strictEqual(matched.length, 1, "exactly the turn whose OUTPUT carries it matches");
    assert.ok(steps.some((s) => s.classList.contains("dim")), "a miss must dim, not vanish");
    assert.strictEqual(byId("traj-search-count").textContent, "1 match", "the hit count is shown");

    // Clearing restores every step: a search must not leave the list permanently filtered.
    byId("traj-search").value = "";
    (byId("traj-search").handlers.input || []).forEach((fn) => fn());
    assert.ok(!steps.some((s) => s.classList.contains("dim")), "clearing the query undims everything");
    assert.strictEqual(byId("traj-search-count").textContent, "", "and drops the count");
  });

  await test("a failed /iterations fetch reports through onError and leaves the drawer closed", async () => {
    const seen = [];
    const { t, byId } = make({ onError: (m) => seen.push(m) });
    stubFetch(null, { ok: false, status: 404 });
    const drawer = byId("traj-drawer");
    drawer.hidden = true;
    await t.open();
    assert.strictEqual(seen.length, 1, "onError must be called exactly once");
    assert.match(seen[0], /HTTP 404/, "the status must survive verbatim (404 and 502 differ)");
    assert.strictEqual(drawer.hidden, true, "an empty drawer must not open");
  });

  await test("a thrown fetch is reported too, not swallowed", async () => {
    const seen = [];
    const { t } = make({ onError: (m) => seen.push(m) });
    global.fetch = async () => { throw new Error("network down"); };
    await t.open();
    assert.strictEqual(seen.length, 1);
    assert.match(seen[0], /network down/);
  });

  await test("an EMPTY envelope renders without throwing (a malformed trace must never crash it)", async () => {
    const { t, byId } = make();
    stubFetch({ started_at: null, total_s: null, timing_note: "", per_turn_timing: false,
                initial: {}, iterations: [], timeline: [] });
    await t.open();
    assert.strictEqual(byId("traj-steps").children.length, 1, "Init is always a stop");
    assert.strictEqual(byId("traj-timeline").children.length, 1, "the empty-state line, not a crash");
    assert.ok(allText(byId("traj-timeline")).includes("no tool or sub-LM calls recorded"));
  });

  console.log(failed ? "\n" + failed + " test(s) FAILED" : "\nall passing");
  process.exit(failed ? 1 : 0);
})();
