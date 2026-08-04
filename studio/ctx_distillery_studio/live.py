"""Drives a LIVE distillation and streams it, mirroring the trace-event vocabulary replay already
uses. Every ctx_distillery/dspy-bearing import is deferred into `run_live`'s own body (except the
two names its own exception-handling tail needs by name — see `run_live`'s docstring for why those
two are safe to import eagerly here) — see the module-level comment in `app.py` for why (this
module must be importable, at zero cost, even when CTXD_LIVE_PROJECTS is unset).

No `asyncio.Task` wrapping, no polling loop: cancellation is real now, built into rlm-harness's own
sandbox interpreter (`RLMTask(cancel_event=...)`, `rlm_harness.SandboxCancelled`) — this module's only
job is to build a plain `threading.Event`, hand it straight to `run_distillation_artifacts`, and
await normally.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rlm_harness import SandboxCancelled

from .iterations import _project_label


def trace_event_sink(sink: Callable[[dict], None]) -> Callable[[dict], None]:
    """A `TraceRecorder(on_event=...)` observer -> `mapper.to_event` -> `sink`, live. Reuses
    `to_event` UNCHANGED: `run_start` -> `distill.run.created`, `tool_call` -> `distill.evidence.read`
    / `distill.draft.created`, `result` -> `distill.plan.done`, `run_end` -> `distill.run.completed`
    — every one of these ALREADY has a frontend handler from replay. `main_step` -> `distill.plan.step`
    is never reached THIS way (`TraceRecorder.note_main_step` only buffers a timestamp for post-hoc
    reconciliation and never fires `on_event`) — that is exactly why `_build_studio_callback` below
    exists as the second, necessary live source."""
    from .mapper import to_event

    def on_event(event: dict) -> None:
        out = to_event(event)
        if out:
            sink(out)

    return on_event


def _build_studio_callback(sink: Callable[[dict], None]) -> Any:
    """A dspy callback for the planner's per-turn REASONING, live — built lazily (dspy imported
    inside this function, never at module top). A ROOT-planner turn is the only
    `on_adapter_parse_end` payload carrying BOTH `reasoning` and `code` — the same filter
    `rlm_harness.task._MainStepTimer` uses internally. dspy parses each turn TWICE; drop the
    consecutive duplicate. Emits `distill.plan.step` — REUSING the exact event name `mapper.to_event`
    already emits for a REPLAYED `main_step` (`{"turn", "reasoning", "has_code"}`), so the frontend
    needs NO new event handler for this to render live — except that `turn` is absent here (dspy's
    parse callback carries no turn number)."""
    from dspy.utils.callback import BaseCallback

    class _StudioCallback(BaseCallback):
        def __init__(self) -> None:
            self._last_reasoning: object = object()

        def on_adapter_parse_end(self, call_id, outputs, exception=None):
            if not (isinstance(outputs, dict) and "reasoning" in outputs and "code" in outputs):
                return
            reasoning = outputs.get("reasoning")
            if reasoning == self._last_reasoning:
                return
            self._last_reasoning = reasoning
            sink({"event": "distill.plan.step",
                  "data": {"reasoning": reasoning, "has_code": bool(outputs.get("code"))}})

    return _StudioCallback()


def run_live(
    project_dir: Path,
    run_id: str,
    trace_path: Path,
    sink: Callable[[dict], None],
    on_done: Callable[[dict], None],
    *,
    cancel_event: threading.Event | None = None,
    include_subagents: bool = False,
    claude_home: str | Path | None = None,
) -> None:
    """SYNCHRONOUS — call it in a worker thread. Builds the SAME precondition `cli._cmd_distill`
    builds (`DistillConfig.from_env()` -> `setup()` -> `make_chat_fn()` ->
    `ClaudeCodeAdapter.for_project(...)`) — deliberately the SAME calls, not a re-derivation, so a
    live run and a CLI run of the same project can never silently diverge in what credentials/config
    they run under.

    `cancel_event` is threaded STRAIGHT INTO `run_distillation_artifacts(..., cancel_event=...)` —
    no wrapping needed. rlm-harness's own sandbox watchdog (inside the interpreter `_build_rlm()`
    constructs) is what actually kills a wedged sandbox turn; this function's only job is to build
    the Event and hand it over. If the caller never sets `cancel_event`, the run behaves exactly
    like a plain `distill`.

    `SandboxCancelled` and `_project_label` are imported at this MODULE's top (not inside the try
    below) because the `except`/fallback-tail code needs them by name regardless of which line
    raised — moving them inside the try would trade an (already extremely unlikely) `ImportError`
    escaping this function for a `NameError` doing the same, no better. This is a cache hit, never a
    fresh load that could fail: by the time `run_live` is reachable at all, `rlm_harness` and this
    module's own siblings are ALREADY in `sys.modules` (the studio app imported them to even define
    the route that calls this function). Only the imports needed EXCLUSIVELY by the happy path stay
    inside the try, below.

    On completion (success, failure, OR `SandboxCancelled`) calls `on_done(payload)` EXACTLY ONCE —
    the one invariant this function must satisfy, and the reason the `try` below wraps the entire
    body rather than just the drive coroutine: `setup()`/`make_chat_fn()`/`ClaudeCodeAdapter.
    for_project()` can all genuinely raise (a misconfigured `CD_*` var is a highly plausible
    first-time-enabling-live-mode mistake, not an exotic edge case), and a raise escaping uncaught
    in this `daemon=True` worker thread would permanently hang the client's SSE connection, give a
    false-positive "cancelling" Cancel response forever, and leak the run_id's registry entries
    forever. `payload` is built by `_run_payload_core(run_id)` — the SAME shape `GET
    /v1/runs/{run_id}` already returns, re-read from whatever trace actually got written — wrapped
    in its OWN nested try/except too, since that call raising (not just returning `None`) would
    reproduce the identical "on_done never called" failure one line later.

    **The `except` below is `BaseException`, not `Exception` — deliberately.**
    `DistillConfig.from_env()` (called on the happy path just below) raises `SystemExit` as its
    documented, USER-FACING error contract on exactly the three misconfigurations it names (no
    `CD_ROOT_LM`, a non-pinned `CD_INTERPRETER`, a subscription sentinel on the drafter) — precisely
    the "highly plausible first-time-enabling-live-mode mistake" this docstring already argues for.
    `SystemExit` is a `BaseException`, not an `Exception`, so `except Exception` would miss it —
    and CPython's default `threading.excepthook` SILENTLY SWALLOWS an uncaught `SystemExit` in a
    non-main thread (unlike any other exception, which it at least logs), so the failure would be
    invisible AND `on_done` would still never fire, reproducing the exact bug this whole `try`
    exists to prevent one exception type later. `BaseException` is safe specifically HERE, in a
    thread that is never the main thread: `KeyboardInterrupt`/`SIGINT` are only ever delivered to
    the main thread in CPython, so there is no real interrupt for this handler to be swallowing.
    """
    exc: BaseException | None = None
    try:
        import asyncio

        import dspy

        # ABSOLUTE, not relative: `ctx_distillery_studio` is its own top-level workspace member,
        # not a subpackage of `ctx_distillery` — a `from ..config import ...` here would try to
        # climb past `ctx_distillery_studio` itself and fail with "attempted relative import
        # beyond top-level package" the first time this function actually RUNS (a bug an earlier
        # pass's static/docstring-level review never caught, because nothing had executed this
        # line yet).
        from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter
        from ctx_distillery.config import DistillConfig, make_chat_fn, setup
        from ctx_distillery.session import run_distillation_artifacts

        config = setup(DistillConfig.from_env())
        chat_fn = make_chat_fn(config)
        adapter = ClaudeCodeAdapter.for_project(
            project_dir, home=claude_home, include_subagents=include_subagents
        )

        async def _drive():
            with dspy.context(callbacks=[_build_studio_callback(sink)]):
                return await run_distillation_artifacts(
                    adapter, chat_fn, str(trace_path), run_id=run_id,
                    meta={
                        "project_dir": str(project_dir),
                        "planner": config.main_model,
                        "drafter": config.draft_model or config.sub_model,
                        "interpreter": config.interpreter,
                        "max_iterations": config.max_iterations,
                        "max_llm_calls": config.max_llm_calls,
                    },
                    on_event=trace_event_sink(sink),
                    cancel_event=cancel_event,
                )

        asyncio.run(_drive())
    except BaseException as e:  # noqa: BLE001 — deliberately wider than Exception; see the docstring
        # SandboxCancelled is one possible exception here — a genuine, intentional stop, not a
        # bug — but the CONTROL FLOW is identical to any other failure, WHICHEVER line raised it:
        # either way, whatever trace got written (possibly NONE, if setup() itself failed) is
        # re-read and returned as the payload below. `isinstance(exc, SandboxCancelled)` is
        # inspected ONLY to set the cosmetic `cancelled` flag — never to take a different path.
        exc = e

    # `str(exc)` computed ONCE here, never re-called inside either fallback dict below — a second
    # call inside the nested `except Exception:` fallback would itself raise (uncaught, by nothing)
    # if a pathological exception's own `__str__` is what failed.
    exc_str = str(exc) if exc is not None else None
    project_label = _project_label(str(project_dir))

    # The `or {...}` fallback below only triggers on `_run_payload_core` RETURNING `None` — it does
    # nothing if that call itself RAISES, which would reproduce the same "on_done never called"
    # failure one line later. So this tail is its OWN try/except too: nothing after this point can
    # prevent `on_done` from firing.
    try:
        from .app import _run_payload_core

        payload = _run_payload_core(run_id) or {
            "plan": {"candidates": [], "problems": [exc_str] if exc_str else []},
            "rubric_facts": {}, "rubric_criteria": [], "project": project_label,
            "transcript_index": [],
        }
    except Exception:  # noqa: BLE001 — must not let a second failure suppress on_done, see above
        payload = {
            "plan": {"candidates": [], "problems": [exc_str] if exc_str else ["_run_payload_core itself failed"]},
            "rubric_facts": {}, "rubric_criteria": [], "project": project_label,
            "transcript_index": [],
        }
    payload["cancelled"] = isinstance(exc, SandboxCancelled)
    on_done(payload)
