"""Pure unit tests over `shutdown.py` — no FastAPI, no dspy, no real signal delivery (the module's
own docstring states these helpers are PURE for exactly this reason)."""

from __future__ import annotations

import signal
import threading
import time

from ctx_distillery_studio.shutdown import (
    cancel_all_inflight,
    install_cancel_on_signal,
    join_workers,
    make_signal_wrapper,
    restore_signals,
)


def test_cancel_all_inflight_sets_every_unset_event_and_counts_only_the_new_ones():
    a, b, c = threading.Event(), threading.Event(), threading.Event()
    c.set()  # already set — must not be double-counted
    assert cancel_all_inflight({"a": a, "b": b, "c": c}) == 2
    assert a.is_set() and b.is_set() and c.is_set()


def test_cancel_all_inflight_on_an_empty_mapping_is_a_noop():
    assert cancel_all_inflight({}) == 0


def test_cancel_all_inflight_never_raises_even_if_a_value_is_pathological():
    class _Hostile:
        def is_set(self):
            raise RuntimeError("boom")

    # Must not propagate: this runs inside a signal handler, where an escaping exception would
    # interrupt whatever frame the main thread happened to be executing.
    cancel_all_inflight({"ok": threading.Event(), "bad": _Hostile()})


def test_signal_wrapper_cancels_then_delegates_to_a_callable_previous_handler():
    calls: list[tuple[int, object]] = []
    ev = threading.Event()
    handler = make_signal_wrapper(lambda signum, frame: calls.append((signum, frame)), {"r": ev})
    handler(signal.SIGINT, None)
    assert ev.is_set()
    assert calls == [(signal.SIGINT, None)]


def test_signal_wrapper_with_sig_ign_previous_handler_still_cancels_and_does_not_raise():
    ev = threading.Event()
    handler = make_signal_wrapper(signal.SIG_IGN, {"r": ev})
    handler(signal.SIGINT, None)
    assert ev.is_set()


def test_signal_wrapper_with_none_previous_handler_still_cancels_and_does_not_raise():
    ev = threading.Event()
    handler = make_signal_wrapper(None, {"r": ev})
    handler(signal.SIGTERM, None)
    assert ev.is_set()


def test_install_and_restore_signals_round_trips_on_the_main_thread():
    prev_int = signal.getsignal(signal.SIGINT)
    prev_term = signal.getsignal(signal.SIGTERM)
    try:
        captured = install_cancel_on_signal({})
        assert signal.getsignal(signal.SIGINT) is not prev_int
        assert signal.getsignal(signal.SIGTERM) is not prev_term
        restore_signals(captured)
        assert signal.getsignal(signal.SIGINT) == prev_int
        assert signal.getsignal(signal.SIGTERM) == prev_term
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def test_install_cancel_on_signal_off_the_main_thread_is_a_noop():
    result: dict = {}

    def _run():
        result["value"] = install_cancel_on_signal({})

    t = threading.Thread(target=_run)
    t.start()
    t.join()
    assert result["value"] == {}


def test_restore_signals_off_the_main_thread_is_a_noop_and_never_raises():
    def _run():
        restore_signals({signal.SIGINT: signal.SIG_DFL})

    t = threading.Thread(target=_run)
    t.start()
    t.join()  # would hang/raise inside the thread if this were not a no-op; join() proves it returned


def test_join_workers_waits_for_a_fast_worker_and_reports_none_still_alive():
    done = threading.Event()
    t = threading.Thread(target=done.set)
    t.start()
    still_alive = join_workers({"w": t}, budget_s=2.0)
    assert still_alive == []
    assert done.is_set()


def test_join_workers_abandons_a_slow_worker_within_its_budget_and_reports_it():
    started = time.monotonic()
    t = threading.Thread(target=lambda: time.sleep(5), daemon=True)
    t.start()
    still_alive = join_workers({"slow": t}, budget_s=0.05)
    elapsed = time.monotonic() - started
    assert still_alive == ["slow"]
    assert elapsed < 1.0  # bounded by the budget, not by the worker's own 5s sleep


def test_join_workers_skips_a_worker_that_never_started():
    t = threading.Thread(target=lambda: None)
    # never started — is_alive() is False, so this must not try to join it (join() on an unstarted
    # thread raises RuntimeError)
    assert join_workers({"never-started": t}, budget_s=1.0) == []
