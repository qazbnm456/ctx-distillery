"""`live.py` — the LIVE-drive worker-thread function. `run_live` is exercised via heavy
monkeypatching of the real `ctx_distillery.config`/`ctx_distillery.adapters.claude_code`/
`ctx_distillery.session` call sites it reaches through its own deferred `from ... import ...`
statements (they resolve dynamically at call time, so patching the source module's attribute before
calling `run_live` is picked up) — no real model, no real sandbox, no network, matching this
project's established testing style (`tests/test_session.py`).

`trace_event_sink` is pure and needs none of that. `_build_studio_callback` needs `dspy` (it
subclasses `dspy.utils.callback.BaseCallback`), so its own tests `importorskip("dspy")`.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("fastapi")

from ctx_distillery_studio.live import run_live, trace_event_sink

import ctx_distillery.config as cd_config
import ctx_distillery.session as cd_session
from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter

# ---- trace_event_sink: pure passthrough to mapper.to_event -----------------------------------


def test_trace_event_sink_forwards_a_mapped_event():
    seen = []
    on_event = trace_event_sink(seen.append)
    on_event({"type": "run_start", "payload": {"meta": {}}})
    assert len(seen) == 1
    assert seen[0]["event"] == "distill.run.created"


def test_trace_event_sink_drops_an_event_mapper_maps_to_nothing():
    seen = []
    on_event = trace_event_sink(seen.append)
    on_event({"type": "main_step", "payload": {}})  # main_step -> distill.plan.step needs a turn
    # whatever mapper.to_event does with this shape, the sink never raises and never appends None
    assert None not in seen


# ---- run_live: exactly-once on_done, across every failure shape ------------------------------


def _patch_happy_setup(monkeypatch, tmp_path):
    """Get `run_live` past `setup()`/`make_chat_fn()`/`ClaudeCodeAdapter.for_project()` cheaply, so
    a test can target exactly ONE later failure point without a real model or real `~/.claude`."""
    monkeypatch.setattr(
        cd_config.DistillConfig, "from_env", classmethod(lambda cls: cd_config.DistillConfig(main_model="x"))
    )
    monkeypatch.setattr(cd_config, "setup", lambda config: config)
    monkeypatch.setattr(cd_config, "make_chat_fn", lambda config: (lambda prompt: ""))
    monkeypatch.setattr(
        ClaudeCodeAdapter,
        "for_project",
        classmethod(lambda cls, project_dir, *, home=None, include_subagents=False: object()),
    )


def test_run_live_calls_on_done_exactly_once_when_config_from_env_raises_system_exit(
    tmp_path, monkeypatch
):
    """The fix this session made: `DistillConfig.from_env()` raises `SystemExit` as ITS documented
    error contract, and `SystemExit` is a `BaseException`, not an `Exception` — an `except
    Exception` here would miss it, and CPython's default `threading.excepthook` silently swallows an
    uncaught `SystemExit` in a non-main thread, so `on_done` would never fire at all. Reproduced by
    calling `run_live` directly on the CURRENT (main) thread — the exception's TYPE, not which
    thread it unwinds on, is what this test pins."""
    monkeypatch.setattr(
        cd_config.DistillConfig,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(SystemExit("CD_ROOT_LM is not set"))),
    )
    calls = []
    run_live(tmp_path, "r1", tmp_path / "r1.jsonl", lambda e: None, calls.append)
    assert len(calls) == 1
    assert calls[0]["cancelled"] is False
    assert any("CD_ROOT_LM" in p for p in calls[0]["plan"]["problems"])


def test_run_live_calls_on_done_exactly_once_when_setup_raises_a_plain_exception(tmp_path, monkeypatch):
    _patch_happy_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(cd_config, "setup", lambda config: (_ for _ in ()).throw(RuntimeError("bad config")))
    calls = []
    run_live(tmp_path, "r2", tmp_path / "r2.jsonl", lambda e: None, calls.append)
    assert len(calls) == 1
    assert calls[0]["cancelled"] is False
    assert any("bad config" in p for p in calls[0]["plan"]["problems"])


def test_run_live_calls_on_done_exactly_once_when_for_project_raises(tmp_path, monkeypatch):
    _patch_happy_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ClaudeCodeAdapter,
        "for_project",
        classmethod(lambda cls, *a, **kw: (_ for _ in ()).throw(OSError("no such project"))),
    )
    calls = []
    run_live(tmp_path, "r3", tmp_path / "r3.jsonl", lambda e: None, calls.append)
    assert len(calls) == 1
    assert any("no such project" in p for p in calls[0]["plan"]["problems"])


def test_run_live_sets_cancelled_true_only_for_sandbox_cancelled(tmp_path, monkeypatch):
    """`isinstance(exc, SandboxCancelled)` decides ONLY the cosmetic `cancelled` flag, never a
    different control-flow path — pin that a plain failure never gets mislabeled as a cancel."""
    from rlm_kit import SandboxCancelled

    _patch_happy_setup(monkeypatch, tmp_path)

    async def _raise_cancelled(*a, **kw):
        raise SandboxCancelled("stopped")

    monkeypatch.setattr(cd_session, "run_distillation_artifacts", _raise_cancelled)
    calls = []
    run_live(tmp_path, "r4", tmp_path / "r4.jsonl", lambda e: None, calls.append)
    assert len(calls) == 1
    assert calls[0]["cancelled"] is True


def test_run_live_calls_on_done_exactly_once_when_run_payload_core_itself_raises(tmp_path, monkeypatch):
    """The tail's OWN try/except: `_run_payload_core` raising (not just returning `None`) must not
    reproduce the "on_done never called" bug one line later."""
    _patch_happy_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(cd_config, "setup", lambda config: (_ for _ in ()).throw(RuntimeError("boom")))

    import ctx_distillery_studio.app as appmod

    monkeypatch.setattr(appmod, "_run_payload_core", lambda run_id: (_ for _ in ()).throw(ValueError("x")))
    calls = []
    run_live(tmp_path, "r5", tmp_path / "r5.jsonl", lambda e: None, calls.append)
    assert len(calls) == 1
    assert calls[0]["plan"]["candidates"] == []


def test_run_live_never_raises_out_of_a_worker_thread(tmp_path, monkeypatch):
    """End-to-end proof of the exactly-once guarantee across a REAL thread boundary — matches how
    `app.py`'s `distill()` actually calls this function."""
    monkeypatch.setattr(
        cd_config.DistillConfig,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(SystemExit("nope"))),
    )
    calls: list[dict] = []
    errors: list[BaseException] = []

    def _worker():
        try:
            run_live(tmp_path, "r6", tmp_path / "r6.jsonl", lambda e: None, calls.append)
        except BaseException as exc:  # noqa: BLE001 — would fail the test either way; caught for a clean message
            errors.append(exc)

    t = threading.Thread(target=_worker)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    assert errors == []
    assert len(calls) == 1


def test_run_live_fallback_payload_uses_the_project_basename_not_the_full_path(tmp_path, monkeypatch):
    """The fallback dicts route the project through `_project_label`, never `str(project_dir)`
    raw — the operator's home directory must never leak into an HTTP response."""
    monkeypatch.setattr(
        cd_config.DistillConfig,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(SystemExit("nope"))),
    )
    project_dir = tmp_path / "my-real-project"
    project_dir.mkdir()
    calls = []
    run_live(project_dir, "r7", tmp_path / "r7.jsonl", lambda e: None, calls.append)
    assert calls[0]["project"] == "my-real-project"
    assert str(project_dir) not in str(calls[0])
