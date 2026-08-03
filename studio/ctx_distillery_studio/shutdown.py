"""Process-signal -> graceful run cancellation: bridge SIGINT/SIGTERM to the studio's per-run
`cancel_event`s so a terminal Ctrl+C (or `kill`) stops in-flight live runs COOPERATIVELY — rlm-kit's
own sandbox watchdog does the actual killing now (see `live.py`), so this genuinely stops a run
blocked inside a sandbox turn, not just one blocked on an LM call.

WHY a signal handler and not just a FastAPI shutdown hook: uvicorn's `Server.shutdown()` waits for
open connections (a live SSE stream) BEFORE it runs the lifespan shutdown, so cancelling only in the
shutdown half deadlocks — the wait never ends because the run never gets cancelled. The cancel has to
fire IN the signal handler, during that wait, so the run ends and the SSE connection closes on its
own. The lifespan shutdown is only a backstop (no open connection -> uvicorn doesn't wait -> the
daemon worker would otherwise be killed before it writes anything at all).

These helpers are PURE (no FastAPI/uvicorn import, no dspy/ctx_distillery import) so they unit-test
without a server; `app.py` owns the lifespan wiring and the `_CANCELS`/`_WORKERS` registries.
"""

from __future__ import annotations

import signal
import threading
import time
from collections.abc import Callable, Mapping


def cancel_all_inflight(cancels: Mapping[str, threading.Event]) -> int:
    """Set every registered cancel Event (-> each run unwinds at its next sandbox watchdog tick, or
    its next LM await); return the count newly set. NEVER raises: this runs inside a signal handler,
    where an escaping exception would propagate into whatever frame the main thread happened to be
    executing. Snapshots the values first — the registry is mutated only from the event-loop thread
    (which the signal handler shares, so they can't interleave), but the snapshot is cheap defense in
    depth."""
    n = 0
    for ev in list(cancels.values()):
        try:
            if not ev.is_set():
                ev.set()
                n += 1
        except Exception:  # noqa: BLE001, S110 — must never propagate out of a signal handler
            pass  # do not abort the rest, and nothing may propagate out of the handler
    return n


def make_signal_wrapper(prev, cancels: Mapping[str, threading.Event]) -> Callable[[int, object], None]:
    """Build a SIGINT/SIGTERM handler that cancels all in-flight runs, then delegates to whatever
    handler was installed before us (`prev` — under uvicorn this is its `handle_exit`, which begins
    the graceful shutdown). `prev` is not guaranteed callable: `signal.SIG_DFL`/`SIG_IGN`/`None` are
    the non-Python dispositions, handled as safety nets (the callable branch is the hot path)."""

    def _handler(signum: int, frame=None) -> None:
        cancel_all_inflight(cancels)
        if callable(prev):
            prev(signum, frame)
        elif prev == signal.SIG_DFL:
            # restore the default disposition and re-raise, so Ctrl+C still behaves like Ctrl+C
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)
        # SIG_IGN / None -> ignore (we've already cancelled the runs)

    return _handler


def install_cancel_on_signal(cancels: Mapping[str, threading.Event]) -> dict:
    """Wrap the current SIGINT/SIGTERM handlers so they cancel in-flight runs first. MUST run on the
    main thread (`signal.signal` raises elsewhere); off-thread it is a no-op returning `{}`. Returns
    the captured previous handlers for a later `restore_signals`. Call at lifespan startup — uvicorn
    has already installed its `handle_exit` by then, so that is what gets captured and delegated to."""
    if threading.current_thread() is not threading.main_thread():
        return {}
    prev_handlers: dict = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        prev = signal.getsignal(sig)
        prev_handlers[sig] = prev
        signal.signal(sig, make_signal_wrapper(prev, cancels))
    return prev_handlers


def restore_signals(prev_handlers: Mapping[int, object]) -> None:
    """Put the captured handlers back (main-thread only). Mainly test hygiene."""
    if threading.current_thread() is not threading.main_thread():
        return
    for sig, handler in prev_handlers.items():
        try:
            signal.signal(sig, handler)
        except (TypeError, ValueError, OSError):  # non-restorable handler — leave the slot as-is
            pass


def join_workers(workers: Mapping[str, threading.Thread], budget_s: float) -> list:
    """Join the live worker threads within a shared TOTAL `budget_s`, so a cancelled run gets a
    moment to write its final trace state before the process exits — covers the case where no SSE
    connection is open to keep uvicorn waiting. Workers stay daemon, so any that blow the budget are
    abandoned (the process still exits). Returns the names still alive after the budget (for a log
    line). Direct bounded join, NOT run_in_executor: the loop is shutting down so briefly blocking it
    is fine, and `budget_s` must stay below uvicorn's `--timeout-graceful-shutdown`."""
    deadline = time.monotonic() + budget_s
    still_alive = []
    for name, t in list(workers.items()):
        if not t.is_alive():
            continue
        t.join(timeout=max(0.0, deadline - time.monotonic()))
        if t.is_alive():
            still_alive.append(name)
    return still_alive
