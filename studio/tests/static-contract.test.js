/* Static CSS contracts the zero-build frontend relies on (run: `node tests/static-contract.test.js`).
   Plain CommonJS, no npm, no package.json, no node_modules — the same runner shape the sibling
   studios use. Each rule below is easy to regress silently because nothing else in the suite opens a
   browser, so they are pinned TEXTUALLY against the real `static/style.css`.

   ADAPTED, not copied. Of toolscout's five assertions only three have an analogue here: the
   `[hidden]` guard, the `.layout` viewport-height pin, and `word-break` on model-supplied fields.
   The other two assert selectors this studio does not have — there is no inline SVG anywhere in
   `index.html` (so no `.empty-stage .es-glyph svg` sizing rule) and no `.meta-col`/`.prose`/`.tchip`
   (our analogue is the generic `.panel { overflow-y:auto }`). Asserting them would pin a UI that
   does not exist. Three assertions below are OURS and have no sibling precedent: the draft `<pre>`'s
   `overflow-wrap`, the §2 derived-state frame classes, and the responsive stack.

   See `../DESIGN.md` for what each rule is defending visually. */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const css = fs.readFileSync(path.join(__dirname, "..", "static", "style.css"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");

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

// -- 3. the absolute rule, asserted against app.js itself ---------------------------------------

test("app.js writes no markup: no innerHTML assignment, no HTML-parsing sink", () => {
  // CLAUDE.md invariant 10 and DESIGN.md §7: a drafted memory/skill body is untrusted model output,
  // never markup to render. A CSS file cannot defend this one, and it is the single rule here whose
  // violation would be a real vulnerability rather than a layout bug.
  //
  // Matched on CODE SHAPE (an assignment / a call), not on the bare identifier, and that is
  // deliberate: `app.js` NAMES `innerHTML` twice — once in its header comment ("Never `innerHTML`,
  // ever, anywhere in this file") and once inline at the one site that most needs the reminder. A
  // bare substring scan would flag the documentation OF the rule as a violation of it. That is the
  // same failure mode `studio/tests/test_boundary.py` already hit on the Python side, where the fix
  // was to stop scanning text and start reading syntax; matching a sink's real shape is this
  // file's equivalent, without pulling a JS parser into a zero-dependency runner.
  for (const sink of [/\.innerHTML\s*=/, /\.outerHTML\s*=/, /insertAdjacentHTML\s*\(/,
                      /document\.write\s*\(/, /\.srcdoc\s*=/]) {
    assert.ok(!sink.test(js), "app.js must build every node with textContent — found " + sink);
  }
  // And the rule is actually written down, not merely accidentally true.
  assert.match(js, /never\s+`?innerHTML/i, "app.js must state the textContent-only rule");
});

console.log(failed ? "\n" + failed + " test(s) FAILED" : "\nall passing");
process.exit(failed ? 1 : 0);
