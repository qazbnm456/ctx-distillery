/* Static CSS contracts the zero-build frontend relies on (run: `node tests/static-contract.test.js`).
   Plain CommonJS, no npm, no package.json, no node_modules — the same runner shape the sibling
   studios use. Each rule below is easy to regress silently because nothing else in the suite opens a
   browser, so they are pinned TEXTUALLY against the real `static/style.css`.

   ADAPTED, not copied. Of toolscout's five assertions only three have an analogue here: the
   `[hidden]` guard, the `.layout` viewport-height pin, and `word-break` on model-supplied fields.
   The other two assert selectors this studio does not have — there is no inline SVG anywhere in
   `index.html` (so no `.empty-stage .es-glyph svg` sizing rule) and no `.meta-col`/`.prose`/`.tchip`
   (our analogue is the generic `.panel { overflow-y:auto }`). Asserting them would pin a UI that
   does not exist. The remaining assertions are OURS and have no sibling precedent: the draft
   `<pre>`'s `overflow-wrap`, the §2 derived-state frame classes, the responsive stack, the
   Trajectory drawer's `.traj-well` (a turn's REPL echo is the same class of untrusted text as a
   draft), and the no-markup scan — which reads EVERY `static/*.js`, plus a guard asserting the scan
   really found the files it exists to police.

   See `../DESIGN.md` for what each rule is defending visually. */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const STATIC_DIR = path.join(__dirname, "..", "static");
const css = fs.readFileSync(path.join(STATIC_DIR, "style.css"), "utf8");
// EVERY script under static/, not just app.js. The scan used to read app.js alone, which meant a
// second frontend file — `trajectory.js`, the drawer whose whole content is untrusted REPL echo —
// would have sailed straight past the no-markup assertion below. A scan that cannot see the file
// most likely to violate the rule is worse than no scan, because it reports "ok".
const JS_FILES = fs.readdirSync(STATIC_DIR).filter((f) => f.endsWith(".js")).sort();
const JS_SOURCES = JS_FILES.map((f) => [f, fs.readFileSync(path.join(STATIC_DIR, f), "utf8")]);

let failed = 0;
function test(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (e) { failed++; console.error("  FAIL " + name + "\n       " + e.message); }
}

// -- 1. carried over from the siblings, verified to exist here ---------------------------------

test("[hidden] guard exists and is !important", () => {
  // DEFENSIVE, and stated as such: the one `.hidden` toggle on this page is `#plan-empty`, which
  // sets no `display` of its own today, so the UA sheet would currently suffice. The guard pins the
  // invariant BEFORE someone gives a hidden-toggled element a `display:*` and every toggle silently
  // no-ops (the exact shipped bug this assertion exists for in the sibling studios).
  assert.match(css, /\[hidden\]\s*\{\s*display\s*:\s*none\s*!important\s*;?\s*\}/);
});

test("the grid owns the viewport height so the page itself never scrolls", () => {
  // Without this, each panel's own `overflow-y:auto` is meaningless — the panels grow instead and
  // the PLAN stage walks off the bottom of a long run.
  assert.match(css, /\.layout\s*\{[^}]*height\s*:\s*calc\(100vh - 56px\)/);
});

test("every model-supplied field wraps attacker-length tokens", () => {
  // These three render UNTRUSTED model output inline: a feed row's scalar fields, a candidate's
  // `key_fields` (which carries `target_path`, a path from a file the model read), and a rubric
  // value. One unbroken token would otherwise give the whole page a horizontal scrollbar.
  for (const sel of [".fr-fields", ".candidate-key-fields", ".rubric-fact-value"]) {
    const rule = css.match(new RegExp("(^|\\n)\\" + sel + "\\s*\\{([^}]*)\\}"));
    assert.ok(rule, sel + " rule is missing");
    assert.match(rule[2], /word-break|overflow-wrap/, sel + " must wrap long tokens");
  }
});

// -- 2. ours: no sibling has these ---------------------------------------------------------------

test("the draft <pre> breaks unbroken tokens, not just at whitespace", () => {
  // `white-space:pre-wrap` wraps at WHITESPACE only. A drafted memory/skill body is untrusted model
  // output, so a single 5000-char base64 blob is in scope — `overflow-wrap` is what keeps it inside
  // the column. `word-break` alone would not survive a swap to `pre` either, so require both facts.
  const rule = css.match(/(^|\n)\.candidate-draft\s*\{([^}]*)\}/);
  assert.ok(rule, ".candidate-draft rule is missing");
  assert.match(rule[2], /overflow-wrap\s*:\s*(anywhere|break-word)/,
    ".candidate-draft must break unbroken tokens (pre-wrap breaks at whitespace only)");
  assert.match(rule[2], /max-height\s*:/, ".candidate-draft must cap its height");
  assert.match(rule[2], /overflow-y\s*:\s*auto/, ".candidate-draft must scroll inside its own box");
});

test("the derived-state frame classes exist and blocked is the refusal color", () => {
  // DESIGN.md §2: the frame is keyed to what `assemble()` derived from the trace, and `blocked`
  // means "apply_plan would refuse this". Losing the rule turns the console's whole signature off
  // without breaking anything a Python test can see.
  for (const state of ["blocked", "backed", "inert"]) {
    assert.match(css, new RegExp("\\.candidate-row\\.state-" + state + "\\s*\\{"),
      ".candidate-row.state-" + state + " is missing (DESIGN.md §2)");
  }
  const blocked = css.match(/\.candidate-row\.state-blocked\s*\{([^}]*)\}/);
  assert.match(blocked[1], /var\(--bad\)/, "the blocked frame must use --bad");
  const marker = css.match(/(^|\n)\.candidate-blocked\s*\{([^}]*)\}/);
  assert.ok(marker, ".candidate-blocked (the ⚠ refusal marker) rule is missing");
  assert.match(marker[2], /var\(--bad\)/, "the refusal marker must use --bad");
});

test("the three-track grid stacks below its own total width", () => {
  // 320 + 1fr + 300 needs ~1000px; below that the grid overflowed the viewport horizontally. The
  // height pin must be RELEASED in the same breakpoint — one stacked column of fixed-height scroll
  // tracks crushes every panel to a few rows.
  const mq = css.match(/@media \(max-width:\s*\d+px\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(mq, "no max-width media query — the layout has no responsive stack");
  assert.match(mq[1], /\.layout\s*\{[^}]*grid-template-columns\s*:\s*minmax\(0,\s*1fr\)/,
    "the stacked layout must collapse to one column");
  assert.match(mq[1], /\.layout\s*\{[^}]*height\s*:\s*auto/,
    "the stacked layout must release the calc(100vh - 56px) pin");
});

test("the drawer's REPL wells break unbroken tokens and scroll inside their own box", () => {
  // `.traj-well` renders a turn's `code`/`output` — the REPL's VERBATIM echo, which really does
  // carry drafted bodies and evidence (DESIGN.md §5.7). Same class of untrusted text as
  // `.candidate-draft`, so it needs the same two defenses: `pre-wrap` breaks at whitespace only.
  const rule = css.match(/(^|\n)\.traj-well\s*\{([^}]*)\}/);
  assert.ok(rule, ".traj-well rule is missing");
  assert.match(rule[2], /overflow-wrap\s*:\s*(anywhere|break-word)/,
    ".traj-well must break unbroken tokens (pre-wrap breaks at whitespace only)");
  assert.match(rule[2], /max-height\s*:/, ".traj-well must cap its height");
  assert.match(rule[2], /overflow-y\s*:\s*auto/, ".traj-well must scroll inside its own box");
});

// -- 3. the absolute rule, asserted against EVERY script under static/ ---------------------------

test("the no-markup scan actually reads every static/*.js (a scan that reads nothing reports ok)", () => {
  // The guard on the guard. This assertion exists because the previous version of the scan below
  // read `app.js` and only `app.js`, so `trajectory.js` — added later, and the file with the
  // strongest reason to reach for markup — was never examined at all. Naming both files here means
  // a rename or a move breaks the test loudly instead of silently emptying its input.
  assert.ok(JS_FILES.includes("app.js"), "static/app.js not found by the scan");
  assert.ok(JS_FILES.includes("trajectory.js"), "static/trajectory.js not found by the scan");
  assert.strictEqual(JS_SOURCES.length, JS_FILES.length, "every discovered script must be read");
  for (const [name, src] of JS_SOURCES) assert.ok(src.length > 0, name + " read as empty");
});

test("no static/*.js writes markup: no innerHTML assignment, no HTML-parsing sink", () => {
  // CLAUDE.md invariant 10 and DESIGN.md §7: a drafted memory/skill body — and, in the drawer, a
  // turn's REPL echo — is untrusted model output, never markup to render. A CSS file cannot defend
  // this one, and it is the single rule here whose violation would be a real vulnerability rather
  // than a layout bug.
  //
  // Matched on CODE SHAPE (an assignment / a call), not on the bare identifier, and that is
  // deliberate: both files NAME `innerHTML` in prose — `app.js`'s header ("Never `innerHTML`, ever,
  // anywhere in this file") and again inline at the one site that most needs the reminder,
  // `trajectory.js`'s header at length, because the siblings it was rebuilt from use it seven times
  // each. A bare substring scan would flag the documentation OF the rule as a violation of it. That
  // is the same failure mode `studio/tests/test_boundary.py` already hit on the Python side, where
  // the fix was to stop scanning text and start reading syntax; matching a sink's real shape is
  // this file's equivalent, without pulling a JS parser into a zero-dependency runner.
  for (const [name, src] of JS_SOURCES) {
    for (const sink of [/\.innerHTML\s*=/, /\.outerHTML\s*=/, /insertAdjacentHTML\s*\(/,
                        /document\.write\s*\(/, /\.srcdoc\s*=/]) {
      assert.ok(!sink.test(src), name + " must build every node with textContent — found " + sink);
    }
    // And the rule is actually written down in each file, not merely accidentally true.
    assert.match(src, /never\s+`?innerHTML/i, name + " must state the textContent-only rule");
  }
});

console.log(failed ? "\n" + failed + " test(s) FAILED" : "\nall passing");
process.exit(failed ? 1 : 0);
