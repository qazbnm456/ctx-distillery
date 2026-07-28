"""`ctx_distillery_eval.taskset.collect_tasks` — enumeration, and its malformed-trace degrade.

`taskset.py` had NO test coverage at all before this (`test_cli.py`'s docstring already flagged that
gap) — and it is where a batch scoring run's FIRST crash on a malformed trace actually lived: the
`e.get("run_id")` set comprehension, reached before any run was scored.

Traces are built with a REAL `TraceRecorder` rather than hand-written JSON, because the field under
test here is the `run_id` ENVELOPE key, and hand-rolling that would be asserting against a guess at
the trace/v1 shape instead of the shape rlm-kit really writes.
"""

from __future__ import annotations

from ctx_distillery_eval.taskset import collect_tasks
from rlm_kit.trace import EVENT_RESULT, TraceRecorder

NON_DICT_LINES = ("42", "null", '"x"', "[1, 2, 3]")


def _trace(path, *run_ids):
    for run_id in run_ids:
        with TraceRecorder(str(path), run_id=run_id, meta={}) as rec:
            rec.record(EVENT_RESULT, {"output": {"candidates": []}})
    return path


def test_collect_tasks_returns_one_task_per_run_id(tmp_path):
    _trace(tmp_path / "a.jsonl", "r0", "r1")
    tasks = collect_tasks(str(tmp_path / "*.jsonl"))
    assert [t.run_id for t in tasks] == ["r0", "r1"]  # deduped across many events, sorted
    assert {t.trace_path for t in tasks} == {str(tmp_path / "a.jsonl")}


def test_collect_tasks_is_sorted_by_path_then_run_id(tmp_path):
    _trace(tmp_path / "b.jsonl", "z")
    _trace(tmp_path / "a.jsonl", "y")
    assert [t.run_id for t in collect_tasks(str(tmp_path / "*.jsonl"))] == ["y", "z"]


def test_collect_tasks_ignores_non_dict_lines(tmp_path):
    """The bug: a line that is valid JSON but not an object raised a raw `AttributeError` here and
    took the ENTIRE glob down — the clean traces in it included — before a single run was scored."""
    path = _trace(tmp_path / "weird.jsonl", "r0")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(NON_DICT_LINES) + "\n")
    assert [t.run_id for t in collect_tasks(str(tmp_path / "*.jsonl"))] == ["r0"]


def test_collect_tasks_of_a_file_with_no_run_ids_contributes_nothing(tmp_path):
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "junk.jsonl").write_text("\n".join(NON_DICT_LINES) + "\n", encoding="utf-8")
    assert collect_tasks(str(tmp_path / "*.jsonl")) == []


def test_collect_tasks_of_a_glob_matching_nothing_is_empty(tmp_path):
    assert collect_tasks(str(tmp_path / "*.jsonl")) == []
