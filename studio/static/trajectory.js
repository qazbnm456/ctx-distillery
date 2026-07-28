/* The Trajectory drawer — a bottom sheet over a FINISHED run's REPL turns, fetched from
   `GET /v1/runs/{run_id}/iterations` (`ctx_distillery_studio/iterations.py` is the data layer).
   Loaded as a plain <script> BEFORE app.js, exposing a `window.Trajectory(deps)` factory.

   REBUILT against `el()` / `clear()`, NOT ported. Every sibling studio's `trajectory.js` assembles
   its panes by assigning `innerHTML` — seven sites each. This project forbids that absolutely
   (`app.js`'s header rule, `DESIGN.md` §7's first Don't, `CLAUDE.md` invariant 10), so there is no
   markup string anywhere below: every node comes from `el(tag, className, text)`, which sets
   `textContent`. Never `innerHTML`, in this file or any other under `static/` —
   `tests/static-contract.test.js` scans every `static/*.js` for the sinks, this file included.

   **And here that rule is the mitigation itself, not hygiene.** `iterations.py`'s leak tests prove
   `timeline` and `initial` carry no paths, no drafted bodies and no evidence. They do NOT cover a
   turn's `reasoning` / `code` / `output`, and cannot: on the real live run those numbers were
   measured from, 4 of 6 drafted bodies and ALL 6 evidence blobs appear in `iterations[*].code` /
   `iterations[*].output`, because that text is the REPL's own echo — the planner printed a drafting
   call's return value and typed the evidence in as a literal. Surfacing turns is the entire reason
   this drawer exists (`mapper.to_event` gives the feed `has_code: bool` and drops `output`
   outright), so the answer is RENDERING, not filtering. Do not read this file's safety off Pass 3's
   leak tests; it comes from `textContent` and nothing else.

   Deliberately NOT built (see `DESIGN.md` §5.7): no `replay-core.js`, no ▶/⏸/speed transport, no
   progress bar, no expand-to-full, no `run-core.js`, nothing implying a live run. A transport's
   payoff scales with tool-call count and this project's runs make a handful of calls; `app.js` has
   never even used the server's existing `?delay=` pacing. ←/→ stop-stepping survives, inlined. */
(function () {
  "use strict";

  window.Trajectory = function createTrajectory(deps) {
    // Checked explicitly, and at CONSTRUCTION. The siblings get this for free because they call an
    // injected `$` while building their element map; this drawer reads the DOM the way `app.js`
    // does (`document.getElementById`), so nothing would touch a dep until the handle was clicked
    // and a missing one would surface as a runtime error in front of the user instead of here.
    ["el", "clear", "getRunId", "onError"].forEach(function (name) {
      if (typeof deps[name] !== "function") {
        throw new TypeError("Trajectory: missing injected dependency `" + name + "`");
      }
    });
    const el = deps.el;
    const clear = deps.clear;
    const getRunId = deps.getRunId;   // a GETTER, never a construction-time snapshot — see open()
    const onError = deps.onError;

    const dom = {
      handle: document.getElementById("traj-handle"),
      backdrop: document.getElementById("traj-backdrop"),
      drawer: document.getElementById("traj-drawer"),
      run: document.getElementById("traj-run"),
      stat: document.getElementById("traj-stat"),
      note: document.getElementById("traj-note"),
      steps: document.getElementById("traj-steps"),
      detail: document.getElementById("traj-detail"),
      timeline: document.getElementById("traj-timeline"),
      close: document.getElementById("traj-close"),
    };

    let data = null;          // the last `/iterations` envelope
    let sel = null;           // {kind: "init"|"turn"|"tool", index}
    let stepItems = [];       // [{node, kind, index}] — the left nav, in stop order
    let segItems = [];        // [{node, seq, turnIndex}] — the flat timeline, in seq order
    let closeTimer = null;

    // -- formatting ----------------------------------------------------------------------------

    function secs(s) {
      if (s === null || s === undefined) return "—";
      if (s < 1) return Math.round(s * 1000) + "ms";
      if (s < 60) return s.toFixed(1) + "s";
      return Math.floor(s / 60) + "m" + Math.round(s % 60) + "s";
    }

    function plural(n, word) {
      return n + " " + word + (n === 1 ? "" : "s");
    }

    function fmt(value) {
      if (value === null || value === undefined) return "—";
      if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
      if (typeof value === "object") return JSON.stringify(value);
      return String(value);
    }

    function firstLine(s) {
      if (!s) return "";
      const lines = String(s).split("\n");
      let line = "";
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].trim()) { line = lines[i].trim(); break; }
      }
      return line.length > 46 ? line.slice(0, 46) + "…" : line;
    }

    // -- node builders (every one of these sets textContent, never markup) -----------------------

    function kv(parent, key, value) {
      const row = el("div", "kv");
      row.appendChild(el("span", "kv-key", key));
      row.appendChild(el("span", "kv-val", fmt(value)));
      parent.appendChild(row);
    }

    function head(parent, title, sub) {
      const h = el("div", "det-head");
      h.appendChild(el("h3", "det-title", title));
      if (sub) h.appendChild(el("span", "det-sub", sub));
      parent.appendChild(h);
    }

    function well(parent, title, body) {
      const wrap = el("div", "traj-block");
      wrap.appendChild(el("div", "block-head", title));
      wrap.appendChild(el("pre", "traj-well", body));
      parent.appendChild(wrap);
    }

    // -- open / close --------------------------------------------------------------------------

    function showHandle() {
      // The getter is consulted HERE, on every call — `app.js` can load a second run without a
      // reload, and a value captured at construction would pin the drawer to the first one.
      if (dom.handle && getRunId()) dom.handle.hidden = false;
    }

    function reset() {
      data = null;
      sel = null;
      if (dom.handle) dom.handle.hidden = true;
      if (dom.drawer && !dom.drawer.hidden) close();
    }

    async function open() {
      const runId = getRunId();   // read at CLICK time, not at construction
      if (!runId) return;
      let payload;
      try {
        const res = await fetch("/v1/runs/" + encodeURIComponent(runId) + "/iterations");
        if (!res.ok) {
          // Same 404-vs-502 distinction the plan panel keeps (DESIGN.md §5.6): the two have
          // different fixes, so the status goes through verbatim rather than becoming "error".
          onError("no trajectory for " + runId + " (HTTP " + res.status + ")");
          return;
        }
        payload = await res.json();
      } catch (err) {
        onError("failed to load the trajectory: " + err);
        return;
      }
      data = payload || {};
      render();
      // A re-open inside the close window must not be re-hidden by the stale timer.
      clearTimeout(closeTimer);
      dom.backdrop.hidden = false;
      dom.drawer.hidden = false;
      dom.handle.setAttribute("aria-expanded", "true");
      // Flush the unhide before animating: coming from display:none the slide would otherwise have
      // no start frame and jump straight to the end.
      void dom.drawer.offsetHeight;
      dom.backdrop.classList.add("show");
      dom.drawer.classList.add("open");
    }

    function close() {
      dom.drawer.classList.remove("open");
      dom.backdrop.classList.remove("show");
      if (dom.handle) dom.handle.setAttribute("aria-expanded", "false");
      closeTimer = setTimeout(function () {
        dom.drawer.hidden = true;
        dom.backdrop.hidden = true;
      }, 260);
    }

    // -- render --------------------------------------------------------------------------------

    function render() {
      const its = (data && data.iterations) || [];
      const tl = (data && data.timeline) || [];
      dom.run.textContent = getRunId() || "";
      dom.stat.textContent =
        plural(its.length, "turn") + " · " + plural(tl.length, "call") +
        (data.total_s !== null && data.total_s !== undefined ? " · " + secs(data.total_s) : "");
      renderNote();
      renderTimeline(tl);
      renderSteps(its);
      selectStop("init", 0);
      dom.steps.scrollTop = 0;
      dom.timeline.scrollTop = 0;
    }

    function renderNote() {
      clear(dom.note);
      const note = data && data.timing_note;
      dom.note.hidden = !note;
      if (!note) return;
      // VERBATIM, as the server wrote it. `per_turn_timing` picks the tag and nothing else — the
      // sentence itself is the honest description of what the timing on this trace does and does
      // not mean, and paraphrasing it here would be the studio inventing a claim.
      dom.note.className = "traj-note " + (data.per_turn_timing ? "live" : "info");
      dom.note.appendChild(el("span", "note-tag", data.per_turn_timing ? "● per-turn timing" : "ⓘ timing"));
      dom.note.appendChild(el("span", "note-body", note));
    }

    function renderSteps(its) {
      clear(dom.steps);
      stepItems = [];
      addStep("init", 0, "Init", "the run's inputs + the pinned sandbox", null);
      its.forEach(function (it) {
        addStep("turn", it.index, "Turn " + (it.turn !== null && it.turn !== undefined ? it.turn : it.index),
          firstLine(it.reasoning), it);
      });
    }

    function addStep(kind, index, label, sub, it) {
      const node = el("button", "tstep");
      node.type = "button";
      node.appendChild(el("span", "ts-lab", label));
      if (sub) node.appendChild(el("span", "ts-sub", sub));
      // Per-turn `duration_s` exists ONLY when the trace carried live per-turn timing; absent is the
      // common case here, and an absent line is the honest rendering of "we don't know".
      if (it && it.duration_s !== null && it.duration_s !== undefined) {
        node.appendChild(el("span", "ts-dur", secs(it.duration_s)));
      }
      node.addEventListener("click", function () { selectStop(kind, index); });
      dom.steps.appendChild(node);
      stepItems.push({ node: node, kind: kind, index: index });
    }

    // The timeline is FLAT and UNCONDITIONAL. `turn_index` is absent from every entry whenever
    // `per_turn_timing` is false — which is every trace the offline test harness can produce, and
    // any run fast enough not to span a second — so a drawer that surfaced tool calls only THROUGH
    // turn grouping would render an empty pane in exactly those cases. Nothing in this function
    // reads `turn_index`; the cross-highlight in selectStop() does, and only as an enrichment.
    function renderTimeline(tl) {
      clear(dom.timeline);
      segItems = [];
      if (!tl.length) {
        dom.timeline.appendChild(el("div", "traj-empty", "no tool or sub-LM calls recorded"));
        return;
      }
      tl.forEach(function (t) {
        let cls = "seg";
        if (t.ok === false) cls += " seg-bad";
        if (t.unrecognized) cls += " seg-unknown";
        const node = el("button", cls);
        node.type = "button";
        node.appendChild(el("span", "seg-lab", t.label || t.tool || "call"));
        node.appendChild(el("span", "seg-time", "+" + secs(t.rel_s) + " · gap " + secs(t.duration_s)));
        node.addEventListener("click", function () { selectStop("tool", t.seq); });
        dom.timeline.appendChild(node);
        segItems.push({
          node: node,
          seq: t.seq,
          turnIndex: t.turn_index === null || t.turn_index === undefined ? null : t.turn_index,
        });
      });
    }

    // -- selection -----------------------------------------------------------------------------

    function selectStop(kind, index) {
      sel = { kind: kind, index: index };
      const its = (data && data.iterations) || [];
      const tl = (data && data.timeline) || [];
      // The link between the two panes, and it is OPTIONAL by construction: `relTurn` stays null on
      // every trace without live per-turn timing, which switches the cross-highlight off and leaves
      // both panes fully usable on their own.
      let relTurn = null;
      if (kind === "turn") {
        relTurn = index;
      } else if (kind === "tool") {
        const entry = tl[index] || {};
        relTurn = entry.turn_index === null || entry.turn_index === undefined ? null : entry.turn_index;
      }
      stepItems.forEach(function (s) {
        s.node.classList.toggle("on", kind !== "tool" && s.kind === kind && s.index === index);
        s.node.classList.toggle("related",
          kind === "tool" && s.kind === "turn" && relTurn !== null && s.index === relTurn);
      });
      segItems.forEach(function (g) {
        g.node.classList.toggle("on", kind === "tool" && g.seq === index);
        g.node.classList.toggle("related",
          kind === "turn" && relTurn !== null && g.turnIndex === relTurn);
      });
      clear(dom.detail);
      if (kind === "init") detailInit(dom.detail, (data && data.initial) || {});
      else if (kind === "turn") detailTurn(dom.detail, its[index] || {}, linkedCount(index));
      else detailEntry(dom.detail, tl[index] || {});
      dom.detail.scrollTop = 0;
    }

    function linkedCount(turnIndex) {
      let n = 0;
      segItems.forEach(function (g) { if (g.turnIndex === turnIndex) n++; });
      return n;
    }

    // -- detail panes --------------------------------------------------------------------------

    function detailInit(parent, ini) {
      head(parent, "Initial state", "what this run was pointed at, from the trace's own run_start.meta");
      const models = ini.models || {};
      // `project` is a BASENAME — `iterations._project_label` never carries the absolute path.
      kv(parent, "project", ini.project);
      kv(parent, "transcripts", ini.transcripts);
      // The COMPOSITION, when the trace carries the identity list — a jump from 1 to 43 because
      // subagent transcripts were included is otherwise invisible here. Both rows are simply
      // omitted on an old or malformed trace: rendering 0 would claim something it never said.
      if (ini.sessions !== null && ini.sessions !== undefined) {
        kv(parent, "sessions", ini.sessions);
      }
      if (ini.subagents !== null && ini.subagents !== undefined) {
        kv(parent, "subagents", ini.subagents);
      }
      kv(parent, "memory artifacts", ini.memory_artifacts);
      // planner/drafter are the TRACE's recorded model names, not a `/v1/config` field, and they
      // stay plain kv rows in here. DESIGN.md §5.1's "no model-role chips" Don't is about the
      // HEADER, where `/v1/config` returns `{traces_dir}` only and a chip would fabricate a field
      // the response does not have. This is a recorded fact about a past run; that would be a lie.
      kv(parent, "planner", models.planner);
      kv(parent, "drafter", models.drafter);
      // Always "pyodide", and that IS the point: CLAUDE.md invariant 1's sandbox pin is enforced in
      // code (`task._forced_config`), and this row is where a reviewer sees, per run, that it held.
      kv(parent, "interpreter", ini.interpreter);
      kv(parent, "rubric criteria", ini.rubric_criteria);
      kv(parent, "max iterations", ini.max_iterations);
      kv(parent, "max LLM calls", ini.max_llm_calls);
    }

    function detailTurn(parent, it, linked) {
      const timed = (it.rel_s !== null && it.rel_s !== undefined) ||
                    (it.duration_s !== null && it.duration_s !== undefined);
      head(parent, "Turn " + (it.turn !== null && it.turn !== undefined ? it.turn : it.index),
        timed ? "+" + secs(it.rel_s) + " · took " + secs(it.duration_s)
              : "the planner's reasoning, then the REPL code it ran");
      if (!timed) {
        parent.appendChild(el("div", "det-muted",
          "no per-turn timing on this trace — see the note above. The tool timeline is unaffected."));
      }
      if (linked) {
        parent.appendChild(el("div", "det-muted",
          linked + " timeline " + (linked === 1 ? "entry maps" : "entries map") + " to this turn"));
      }
      if (it.reasoning) parent.appendChild(el("div", "det-reason", it.reasoning));
      else parent.appendChild(el("div", "det-muted", "no reasoning recorded for this turn"));

      if (it.code || it.output) {
        const repl = el("details", "traj-repl");
        repl.open = true;
        repl.appendChild(el("summary", null, "REPL · code + output"));
        // The honest caption. A turn's code/output is the REPL's verbatim echo and really does
        // repeat drafted bodies and the evidence behind them (measured on a real run: 4/6 drafted
        // bodies, 6/6 evidence blobs). It is NOT scrubbed — it is rendered as text, below.
        repl.appendChild(el("div", "det-caption",
          "verbatim REPL echo — may repeat a drafted body or the evidence behind it; " +
          "rendered as text, never markup"));
        if (it.code) well(repl, "code", it.code);
        if (it.output) well(repl, "output", it.output);
        parent.appendChild(repl);
      }
    }

    // Envelope keys the head/caption already rendered — everything else on the entry is whatever
    // that tool's own allowlist branch in `iterations._tool_entry` contributed.
    const ENVELOPE_KEYS = ["entry_kind", "label", "seq", "rel_s", "duration_s", "turn_index",
                           "ok", "unrecognized"];

    function detailEntry(parent, t) {
      head(parent, t.label || t.tool || "call",
        "+" + secs(t.rel_s) + " · gap " + secs(t.duration_s) + (t.ok === false ? " · failed" : ""));
      // Say what `duration_s` IS, every time it is shown. There is no per-call instrumentation
      // anywhere in this project, so it is the gap since the previous recorded event — planner-think
      // AND tool-exec together. Labelling it "took 2.5s" alone would be a fabricated tool latency.
      parent.appendChild(el("div", "det-caption",
        "gap since the previous recorded event — planner-think + tool-exec, not instrumented tool time"));
      if (t.unrecognized) {
        parent.appendChild(el("div", "det-muted",
          "unrecognized tool — the read-only tool set is closed, so no payload fields are surfaced"));
      }

      if (t.entry_kind === "sub_lm") {
        kv(parent, "model", t.model);
        if (t.error) parent.appendChild(el("div", "det-bad", "error · " + t.error));
        well(parent, "input", t.input || "—");
        well(parent, "output", t.output || "—");
        return;
      }

      if (t.tool) kv(parent, "tool", t.tool);
      kv(parent, "ok", t.ok);
      // Iterated rather than switched per tool ON PURPOSE: `iterations._tool_entry` already applied
      // the per-tool allowlist server-side, so whatever survives is exactly what that tool
      // contributes. A switch here would be a second roster to keep in sync — adding a tool there
      // needs no edit here.
      Object.keys(t).forEach(function (key) {
        if (ENVELOPE_KEYS.indexOf(key) >= 0 || key === "tool") return;
        if (key === "errors" && Array.isArray(t[key]) && t[key].length) {
          t[key].forEach(function (e) { parent.appendChild(el("div", "det-bad", e)); });
          return;
        }
        kv(parent, key.replace(/_/g, " "), t[key]);
      });
    }

    // -- ←/→ stop-stepping ----------------------------------------------------------------------
    // Inlined from the siblings' `replay-core.js` (buildStops / stopIndex / stepTarget). The rest of
    // that module — dwellMs / accrue / nextStop / realMsFor — exists only to drive the ▶/⏸/speed
    // transport this drawer deliberately does not have, so vendoring the file to use a quarter of it
    // would mean carrying a replay engine for a keyboard shortcut.

    function buildStops() {
      const its = (data && data.iterations) || [];
      return [{ kind: "init", index: 0 }].concat(its.map(function (it) {
        return { kind: "turn", index: it.index };
      }));
    }

    function stepBy(dir) {
      const stops = buildStops();
      const cur = stops.findIndex(function (s) {
        return sel && s.kind === sel.kind && s.index === sel.index;
      });
      const at = cur < 0 ? 0 : cur;   // a TOOL selection is not a walkable stop — step from Init
      const target = stops[Math.min(stops.length - 1, Math.max(0, at + dir))];
      if (target) selectStop(target.kind, target.index);
    }

    // -- wiring --------------------------------------------------------------------------------

    if (dom.handle) {
      dom.handle.addEventListener("click", open);
      if (dom.close) dom.close.addEventListener("click", close);
      if (dom.backdrop) dom.backdrop.addEventListener("click", close);
      document.addEventListener("keydown", function (evt) {
        if (!dom.drawer || dom.drawer.hidden) return;
        if (evt.key === "Escape") { close(); return; }
        if (evt.key === "ArrowRight" || evt.key === "ArrowLeft") {
          evt.preventDefault();
          stepBy(evt.key === "ArrowRight" ? 1 : -1);
        }
      });
    }

    return { open: open, reset: reset, showHandle: showHandle };
  };
})();
