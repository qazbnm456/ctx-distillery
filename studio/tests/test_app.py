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
    assert client.get("/v1/runs").json() == {"runs": []}


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
    assert client.get("/v1/runs/..%2F..%2Fetc%2Fpasswd").status_code == 404


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
