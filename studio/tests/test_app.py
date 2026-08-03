"""The FastAPI surface — no real model, no Deno, no dspy: traces are built via a REAL
`rlm_kit.trace.TraceRecorder` (this project's established fixture style — see `tests/test_apply.py`/
`tests/test_session.py`), never hand-rolled JSON, so these tests exercise the actual trace/v1 shape
`TraceRecorder` writes rather than a guessed one."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from ctx_distillery_studio import app as appmod
from fastapi.testclient import TestClient
from rlm_kit.trace import TraceRecorder, record_tool_call

from ctx_distillery.rubric import default_rubric, rubric_to_meta
from ctx_distillery.task import DistillCandidate, DistillPlan

client = TestClient(appmod.app)

_DRAFT = (
    "---\n"
    "name: merge-freeze-policy\n"
    "description: Merges are frozen during a release.\n"
    "metadata:\n"
    "  type: project\n"
    "---\n"
    "Merges into main are frozen for the duration of a release.\n"
)


# ---- /v1/config: reports the ONE thing that genuinely varies by deployment ------------------


def test_config_reports_the_traces_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    assert client.get("/v1/config").json() == {"traces_dir": str(tmp_path)}


# ---- /v1/runs: discovery by globbing *.jsonl (no responses/ artifact exists) -----------------


def test_list_runs_lists_trace_file_stems_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    with TraceRecorder(str(tmp_path / "b-run.jsonl"), run_id="b-run", meta={}):
        pass
    with TraceRecorder(str(tmp_path / "a-run.jsonl"), run_id="a-run", meta={}):
        pass
    assert client.get("/v1/runs").json()["runs"] == ["a-run", "b-run"]


def test_list_runs_is_empty_when_traces_dir_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path / "does-not-exist")
    assert client.get("/v1/runs").json() == {"runs": [], "live": []}


# ---- /v1/runs/{run_id}: the assembled plan + rubric facts, from a REAL recorded trace --------


def test_get_run_returns_the_assembled_plan_and_rubric_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    plan = DistillPlan(
        candidates=[
            DistillCandidate(action="promote_to_memory", artifact_id="a1"),
            DistillCandidate(action="prune", key_fields={"target_path": "/memory/stale.md"}),
        ]
    )
    with TraceRecorder(str(tmp_path / "r0.jsonl"), run_id="r0", meta={"transcripts": 1}) as rec:
        record_tool_call("read_transcript_chunk", args={"transcript_index": 0}, ok=True, length=40)
        record_tool_call("draft_memory_file", ok=True, artifact_id="a1", draft=_DRAFT, errors=[])
        rec.record_result(plan)

    body = client.get("/v1/runs/r0").json()
    assert body["plan"]["problems"] == []
    candidates = body["plan"]["candidates"]
    assert candidates[0]["action"] == "promote_to_memory"
    assert candidates[0]["draft"] == _DRAFT and candidates[0]["draft_ok"] is True
    assert candidates[0]["problems"] == []
    assert candidates[1]["action"] == "prune"
    assert candidates[1]["key_fields"] == {"target_path": "/memory/stale.md"}
    assert body["rubric_facts"]["n_candidates"] == 2
    assert body["rubric_facts"]["n_backed_promotions"] == 1
    assert body["rubric_facts"]["prune_targets_named"] == 1


def test_get_run_serves_the_rubric_CRITERIA_the_run_actually_carried(tmp_path, monkeypatch):
    """The criteria descriptions the console renders as each module's note.

    THE COVERAGE GAP THIS CLOSES: every other fixture here records a `meta` with no `rubric` key, so
    the criteria list was always empty and the conversion from `Criterion` never ran. A first draft
    of the endpoint used `dataclasses.asdict` — `Criterion` is a PYDANTIC model, so it raises — and
    the whole suite stayed green while `GET /v1/runs/{id}` 500'd on every real trace. A fixture that
    never exercises a branch cannot defend it.

    Served PER RUN from the trace's own meta rather than from `default_rubric()`, so an old trace
    explains itself with the rubric it ran under; and as an explicit field allowlist, so a future
    field on `Criterion` cannot ride into an HTTP response unnoticed.
    """
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    meta = {"transcripts": 1, "rubric": rubric_to_meta(default_rubric())}
    with TraceRecorder(str(tmp_path / "r1.jsonl"), run_id="r1", meta=meta) as rec:
        rec.record_result(DistillPlan(candidates=[DistillCandidate(action="keep")]))

    criteria = client.get("/v1/runs/r1").json()["rubric_criteria"]

    assert [c["category"] for c in criteria] == ["TF", "TA", "TG", "PA"]
    assert all(c["description"] for c in criteria), "a description is what the console renders"
    assert set(criteria[0]) == {"name", "category", "description"}, "an allowlist, not a dump"
    # The console keys its notes on `category`, so a duplicate would silently drop one.
    assert len({c["category"] for c in criteria}) == len(criteria)


def test_get_run_reports_no_criteria_for_a_trace_that_recorded_none(tmp_path, monkeypatch):
    """An older trace carries no rubric in its meta. The key must still be present and empty — the
    console reads `body.rubric_criteria || []`, and a 500 or a missing key would take the whole
    panel down for a run whose FACTS are perfectly readable."""
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    with TraceRecorder(str(tmp_path / "r2.jsonl"), run_id="r2", meta={"transcripts": 1}) as rec:
        rec.record_result(DistillPlan(candidates=[DistillCandidate(action="keep")]))

    body = client.get("/v1/runs/r2").json()
    assert body["rubric_criteria"] == []
    assert body["rubric_facts"]["n_candidates"] == 1, "the facts still render"


def test_get_run_404_when_no_such_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    assert client.get("/v1/runs/does-not-exist").status_code == 404
    assert client.get("/v1/runs/does-not-exist/events").status_code == 404


def test_get_run_502_on_a_genuinely_corrupted_trace_file(tmp_path, monkeypatch):
    """A file that exists but is not valid JSONL is a genuinely EXTERNAL failure — 502, never a
    500 that would leak a traceback, and never mistaken for the plan-shape degrades `assemble`
    itself already handles (which stay inside `plan_from_events`/`assemble`'s own None-returning
    contract and never reach this guard at all)."""
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    (tmp_path / "bad.jsonl").write_text("not json at all\n", encoding="utf-8")
    assert client.get("/v1/runs/bad").status_code == 502


def test_get_run_never_500s_on_a_syntactically_valid_but_non_dict_jsonl_line(tmp_path, monkeypatch):
    """FIXED per adversarial review: `rlm_kit.trace.load_events` does NO shape validation — a line
    that IS valid JSON but not an object (`42`, `"x"`, `[1,2,3]`, `null`) parses fine (no
    `ValueError`, so the 502 guard above never fires) and used to reach `plan_from_events` as-is,
    which called `.get("type")` on it unconditionally and raised a raw `AttributeError` — a genuine
    500, reproduced concretely by an adversarial review. `_load_trace` now filters non-dict entries
    before anything downstream ever sees them."""
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    (tmp_path / "weird.jsonl").write_text('{"type": "run_start", "payload": {}}\n42\nnull\n[1, 2, 3]\n"x"\n', encoding="utf-8")
    resp = client.get("/v1/runs/weird")
    assert resp.status_code == 200, resp.text
    events_resp = client.get("/v1/runs/weird/events")
    assert events_resp.status_code == 200, events_resp.text


def test_load_trace_delegates_the_dict_shape_filter_to_the_shared_helper():
    """The filter above used to be an inline comprehension in `_load_trace`. It now lives in
    `ctx_distillery.trace_io.load_trace`, because `eval/` turned out to need the identical guard and
    a second copy is exactly what `CLAUDE.md` invariant 11 exists to prevent. Invariant 10's "don't
    remove this filter" caution is satisfied by DELEGATING it, never by dropping it — the test above
    still proves the behaviour end to end; this one proves there is only one implementation of it."""
    from ctx_distillery import trace_io

    assert appmod.load_trace is trace_io.load_trace


def test_run_id_path_is_slug_sanitized_against_traversal(tmp_path, monkeypatch):
    # a run_id becomes a file path. A traversal attempt must fold to a harmless slug that resolves
    # INSIDE traces_dir (-> 404), never escape it.
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    assert appmod._slug_id("../../etc/passwd") == "etc-passwd"
    assert appmod._slug_id("..") == "unknown"
    assert "/" not in appmod._slug_id("a/b/c")


def test_a_traversal_shaped_request_that_survives_routing_still_reaches_the_slugger(
    tmp_path, monkeypatch
):
    """The three direct `_slug_id(...)` assertions above are the real unit coverage. This is the
    REQUEST-level half, and it is deliberately NOT `GET /v1/runs/..%2F..%2Fetc%2Fpasswd`.

    That request used to be the last line of the test above, and an adversarial review instrumented
    `_slug_id` and proved it NEVER REACHES IT: Starlette normalises the path before routing, so its
    404 comes from the ROUTER, and deleting `_slug_id` entirely would not change the result. It was
    a decorative assertion that could not fail. Measured, same probe: `..%2F..%2Fetc%2Fpasswd` and
    `..%252f..%252fetc` do NOT reach `_slug_id`; `%2e%2e`, `a%00b` and a 250-char id DO.

    So the two below are picked because they SURVIVE routing and arrive at `_slug_id` with the raw
    token still intact — which is the only way a request can exercise this module's sanitizer at
    all. Do not "restore" the traversal-looking one thinking it is stronger; it asserted nothing.
    """
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    real = appmod._slug_id
    seen: list[str] = []
    monkeypatch.setattr(appmod, "_slug_id", lambda raw: seen.append(raw) or real(raw))

    assert client.get("/v1/runs/%2e%2e").status_code == 404          # raw ".." -> "unknown"
    assert client.get("/v1/runs/a%00b").status_code == 404           # a NUL would blow up open()
    assert client.get("/v1/runs/%2e%2e/iterations").status_code == 404  # the same guard, 2nd route

    tokens = list(seen)  # snapshot: `_trace_path` below re-enters the spy and would append again
    monkeypatch.setattr(appmod, "_slug_id", real)  # done spying; every call from here is the real one
    # Each request slugs TWICE now: once in `_refuse_if_still_live` (checked before anything else),
    # once more inside `_trace_path` when the payload/trace is actually read — every one of them
    # actually got there, twice over, in request order.
    assert tokens == ["..", "..", "a\x00b", "a\x00b", "..", ".."]
    assert real("..") == "unknown" and real("a\x00b") == "a-b"
    # and the paths they produce stay inside traces_dir, which is the property that matters
    for raw in tokens:
        assert appmod._trace_path(raw).resolve().parent == tmp_path.resolve()


def test_run_id_is_length_capped(tmp_path, monkeypatch):
    """An over-long run_id becomes a filename component, and most filesystems cap one at 255 BYTES.

    Without the cap this was a genuine 500: reproduced before fixing, `GET /v1/runs/<5000 x's>`
    raised `OSError: [Errno 63] File name too long` out of `_load_trace`'s `path.exists()` —
    `Path.exists()` does not swallow ENAMETOOLONG. `_slug_id` used to be "copied verbatim from
    `diff_sentry_studio.app._slug_id`", which was true and was the gap: diff-sentry has no cap
    either. `toolscout_studio.app._slug_id` does, and this now follows toolscout's stricter form.
    """
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    long_id = appmod._slug_id("x" * 5000)
    assert len(long_id) == appmod._RUN_ID_MAX
    assert len(appmod._trace_path("y" * 5000).name) < 255
    assert client.get("/v1/runs/" + "x" * 5000).status_code == 404  # 404, never a raw OSError

    # the truncation-lands-on-a-separator edge: the cut must not leave a trailing '-'/'.'
    edge = appmod._slug_id("a" * (appmod._RUN_ID_MAX - 1) + "-tail")
    assert len(edge) <= appmod._RUN_ID_MAX and not edge.endswith(("-", "."))
    dot = appmod._slug_id("b" * (appmod._RUN_ID_MAX - 1) + ".tail")
    assert len(dot) <= appmod._RUN_ID_MAX and not dot.endswith(("-", "."))

    # idempotent: every read path re-slugs, so re-slugging a capped id must be the identity
    assert appmod._slug_id(long_id) == long_id and appmod._slug_id(edge) == edge


# ---- /v1/runs/{run_id}/events: SSE replay of a REAL recorded trace ---------------------------


def test_replay_streams_every_mapped_event_type_exactly_once_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    plan = DistillPlan(candidates=[DistillCandidate(action="promote_to_memory", artifact_id="a1")])
    with TraceRecorder(str(tmp_path / "r0.jsonl"), run_id="r0", meta={"transcripts": 1}) as rec:
        # A real main-LM trajectory, recorded the same way rlm_kit.task does at finalize time.
        prediction = SimpleNamespace(
            trajectory=[{"reasoning": "read the transcript first", "code": "x = 1", "output": "ok"}],
            final_reasoning="submitting the plan",
        )
        rec.record_main_trajectory(prediction)
        # A recursive sub-LM escalation (bind_recorder_to_sub_lm's own shape).
        rec.record("sub_call", {"input": "is this a memory or a skill?", "processed": "memory", "raw": "memory"})
        record_tool_call("read_transcript_chunk", args={"transcript_index": 0}, ok=True, length=40)
        record_tool_call("draft_memory_file", ok=True, artifact_id="a1", draft=_DRAFT, errors=[])
        rec.record_result(plan)
    # `rec`'s __exit__ (run_end) has already fired by the time the `with` block above exits.

    with client.stream("GET", "/v1/runs/r0/events") as resp:
        body = "".join(resp.iter_text())

    for event in (
        "distill.run.created",
        "distill.plan.step",
        "distill.sub_lm.call",
        "distill.evidence.read",
        "distill.draft.created",
        "distill.plan.done",
        "distill.run.completed",
    ):
        assert f"event: {event}" in body
    # `final` is real in this trace (record_main_trajectory emits it) but must not double the
    # terminal event — run_end is the sole terminal, exactly once.
    assert body.count("event: distill.run.completed") == 1
    assert body.index("distill.plan.done") < body.index("distill.run.completed")


def test_replay_of_a_truncated_trace_still_ends_with_a_synthesized_completed(tmp_path, monkeypatch):
    """A hard-killed run (SIGKILL) skips the recorder's `__exit__`, so no `run_end` is ever written.
    Built from a REAL, complete `TraceRecorder` trace, then the trailing `run_end` line it wrote is
    dropped to simulate exactly that truncation — the fixture stays recorder-built, only the last
    line is removed by hand."""
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    trace_path = tmp_path / "r0.jsonl"
    with TraceRecorder(str(trace_path), run_id="r0", meta={}):
        record_tool_call("list_memory_files", args={}, ok=True, count=0, kinds=[])
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    trace_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # drop the real run_end

    with client.stream("GET", "/v1/runs/r0/events") as resp:
        body = "".join(resp.iter_text())
    assert body.count("event: distill.run.completed") == 1  # synthesized terminal event
    assert body.rstrip().endswith("data: {}")  # and it is the LAST event


# ---- the zero-build frontend is served same-origin, no-cache --------------------------------


def test_frontend_shell_and_assets_are_served_and_revalidate():
    root = client.get("/")
    assert root.status_code == 200 and "text/html" in root.headers["content-type"]
    assert 'src="/static/app.js"' in root.text
    assert root.headers.get("cache-control") == "no-cache"
    for asset in ("app.js", "style.css"):
        resp = client.get(f"/static/{asset}")
        assert resp.status_code == 200 and resp.headers.get("cache-control") == "no-cache"
    assert client.get("/v1/runs/does-not-exist").status_code == 404  # static mount didn't shadow the API


# ---- live mode: GET /v1/projects, POST /v1/distill, POST /v1/runs/{run_id}/cancel -----------
#
# TWO overrides are both required, and each defends a DIFFERENT half of `_host_is_loopback`:
# `client=("127.0.0.1", 50000)` is the ASGI scope's TCP peer (`request.client.host`) — Starlette's
# default TestClient reports `("testclient", 50000)`, which is not loopback. `base_url=` is what a
# request's own `Host` HEADER defaults to (NOT the `client=` tuple — a real gap this file's own
# tests found: every `Host` header defaulted to the literal "testserver" until this was added,
# which `_host_is_loopback` also refuses now that it requires BOTH the peer and the header to name a
# loopback host).
loopback_client = TestClient(
    appmod.app, base_url="http://127.0.0.1:50000", client=("127.0.0.1", 50000)
)


def test_projects_404_when_live_mode_is_off(monkeypatch):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", ())
    assert client.get("/v1/projects").status_code == 404


def test_projects_lists_the_configured_allowlist(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    assert loopback_client.get("/v1/projects").json() == {"projects": [str(tmp_path)]}


def test_projects_403_off_loopback_even_with_live_mode_on(monkeypatch, tmp_path):
    """Found missing by adversarial review: without this check, once live mode is on, ANY client
    that can reach the port — not just the browser sitting at this machine — could read the
    operator's absolute local project paths."""
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    assert client.get("/v1/projects").status_code == 403  # non-loopback client


def test_distill_404_when_live_mode_is_off(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", ())
    resp = loopback_client.post("/v1/distill", json={"project_dir": str(tmp_path)})
    assert resp.status_code == 404


def test_distill_403_off_loopback_even_with_live_mode_on(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    resp = client.post("/v1/distill", json={"project_dir": str(tmp_path)})  # non-loopback client
    assert resp.status_code == 403


def test_distill_403_on_cross_origin_even_from_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    resp = loopback_client.post(
        "/v1/distill",
        json={"project_dir": str(tmp_path)},
        headers={"origin": "http://attacker.example", "host": "127.0.0.1:50000"},
    )
    assert resp.status_code == 403


def test_distill_403_on_a_dns_rebinding_attack_even_though_the_tcp_peer_is_loopback(
    monkeypatch, tmp_path
):
    """The bug an adversarial review caught directly: a REAL DNS-rebinding attack makes the peer
    address genuinely loopback (attacker DNS resolved a hostname to 127.0.0.1) while the browser's
    `Host`/`Origin` headers still name the attacker's hostname, because a browser sends the hostname
    it navigated to, never the resolved IP. A first draft's `_host_is_loopback` checked the peer
    address FIRST and returned `True` immediately on a match — exactly backwards, since that is
    precisely what is true in this attack. It must require the peer be loopback AND the `Host`
    header itself name a loopback host."""
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    resp = loopback_client.post(  # loopback_client's TCP peer really is 127.0.0.1
        "/v1/distill",
        json={"project_dir": str(tmp_path)},
        headers={
            "origin": "http://rebind.attacker.example:50000",
            "host": "rebind.attacker.example:50000",
        },
    )
    assert resp.status_code == 403


def test_distill_403_on_same_hostname_different_port_even_from_loopback(monkeypatch, tmp_path):
    """A same-ORIGIN check, not a same-HOSTNAME check: comparing hostname alone would treat every
    port on `127.0.0.1`/`localhost` as mutually trusted, which on a loopback-only threat model means
    any OTHER local dev server or app would count as "same origin" for CSRF purposes. `Origin` here
    names the right HOSTNAME but the WRONG port relative to `Host`."""
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    resp = loopback_client.post(
        "/v1/distill",
        json={"project_dir": str(tmp_path)},
        headers={"origin": "http://127.0.0.1:9999", "host": "127.0.0.1:50000"},
    )
    assert resp.status_code == 403


def test_distill_400_when_project_dir_is_not_in_the_allowlist(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path / "allowed",))
    resp = loopback_client.post("/v1/distill", json={"project_dir": str(tmp_path / "elsewhere")})
    assert resp.status_code == 400


def test_distill_400_when_run_id_collides_with_an_already_live_run(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    monkeypatch.setattr(appmod, "_WORKERS", {"dup": object()})
    resp = loopback_client.post(
        "/v1/distill", json={"project_dir": str(tmp_path), "run_id": "dup"}
    )
    assert resp.status_code == 400


def test_distill_400_when_run_id_collides_with_an_existing_trace_file(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    (tmp_path / "dup.jsonl").write_text("")
    resp = loopback_client.post(
        "/v1/distill", json={"project_dir": str(tmp_path), "run_id": "dup"}
    )
    assert resp.status_code == 400


def test_distill_happy_path_streams_started_then_forwarded_events_then_completed(
    monkeypatch, tmp_path
):
    """`run_live` itself is monkeypatched (its own behaviour is `tests/test_live.py`'s job) — this
    pins the ROUTE's own contract: `distill.run.started` first (before anything the run itself
    emits), every `sink()` call forwarded verbatim as its own SSE event, then exactly one terminal
    `distill.run.completed` once `on_done` fires, and the registries are cleaned up after."""
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)

    def fake_run_live(project_dir, run_id, trace_path, sink, on_done, **kw):
        sink({"event": "distill.run.created", "data": {}})
        sink({"event": "distill.run.completed", "data": {"foo": "bar"}})
        on_done({"cancelled": False})

    monkeypatch.setattr(appmod, "run_live", fake_run_live)

    with loopback_client.stream(
        "POST", "/v1/distill", json={"project_dir": str(tmp_path), "run_id": "live1"}
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    events = [line for line in body.split("\n\n") if line.strip()]
    assert events[0].startswith("event: distill.run.started")
    assert "run_id" in events[0]
    assert any(e.startswith("event: distill.run.created") for e in events)
    # exactly ONE terminal event, and it is the run's OWN completed event (not a synthesized one,
    # since the run genuinely emitted its own before on_done fired)
    completed = [e for e in events if e.startswith("event: distill.run.completed")]
    assert len(completed) == 1
    assert '"foo": "bar"' in completed[0]
    assert "live1" not in appmod._WORKERS
    assert "live1" not in appmod._CANCELS
    assert "live1" not in appmod._RESERVED_RUN_IDS


def test_distill_synthesizes_a_completed_event_if_the_run_never_emitted_one(monkeypatch, tmp_path):
    """Mirrors `stream_run`'s own fallback: a run that fails before any `run_end` is ever recorded
    must still end its stream with a `distill.run.completed`, carrying the `cancelled` flag."""
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)

    def fake_run_live(project_dir, run_id, trace_path, sink, on_done, **kw):
        on_done({"cancelled": True})  # no sink() calls at all — e.g. setup() failed immediately

    monkeypatch.setattr(appmod, "run_live", fake_run_live)

    with loopback_client.stream(
        "POST", "/v1/distill", json={"project_dir": str(tmp_path), "run_id": "live2"}
    ) as resp:
        body = "".join(resp.iter_text())

    events = [line for line in body.split("\n\n") if line.strip()]
    completed = [e for e in events if e.startswith("event: distill.run.completed")]
    assert len(completed) == 1
    assert '"cancelled": true' in completed[0]


def test_cancel_404_when_live_mode_is_off(monkeypatch):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", ())
    assert loopback_client.post("/v1/runs/whatever/cancel").status_code == 404


def test_cancel_404_when_run_is_not_currently_live(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    monkeypatch.setattr(appmod, "_CANCELS", {})
    assert loopback_client.post("/v1/runs/not-live/cancel").status_code == 404


def test_cancel_sets_the_events_and_returns_cancelling(monkeypatch, tmp_path):
    import threading

    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    ev = threading.Event()
    monkeypatch.setattr(appmod, "_CANCELS", {"live3": ev})
    resp = loopback_client.post("/v1/runs/live3/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"cancelling": True}
    assert ev.is_set()


def test_cancel_403_off_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "_LIVE_PROJECTS", (tmp_path,))
    monkeypatch.setattr(appmod, "_CANCELS", {"live4": SimpleNamespace(set=lambda: None)})
    assert client.post("/v1/runs/live4/cancel").status_code == 403


def test_get_run_refuses_409_while_the_run_is_still_live(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(appmod, "_WORKERS", {"still-live": object()})
    assert client.get("/v1/runs/still-live").status_code == 409


def test_get_iterations_refuses_409_while_the_run_is_still_live(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(appmod, "_WORKERS", {"still-live": object()})
    assert client.get("/v1/runs/still-live/iterations").status_code == 409


def test_stream_run_refuses_409_while_the_run_is_still_live(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(appmod, "_WORKERS", {"still-live": object()})
    assert client.get("/v1/runs/still-live/events").status_code == 409


def test_list_runs_reports_live_run_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(appmod, "_WORKERS", {"b-live": object(), "a-live": object()})
    assert client.get("/v1/runs").json()["live"] == ["a-live", "b-live"]
