"""`ctx_distillery_eval.taskset` — trace enumeration (`collect_tasks`) AND the taskset (`EvalTask`).

`taskset.py` had NO test coverage at all before this (`test_cli.py`'s docstring already flagged that
gap) — and it is where a batch scoring run's FIRST crash on a malformed trace actually lived: the
`e.get("run_id")` set comprehension, reached before any run was scored.

Traces are built with a REAL `TraceRecorder` rather than hand-written JSON, because the field under
test here is the `run_id` ENVELOPE key, and hand-rolling that would be asserting against a guess at
the trace/v1 shape instead of the shape rlm-harness really writes.

The second half covers the taskset pass. `demo_taskset` is the interesting one: it is the only
`demo_taskset` in the family that MATERIALIZES rather than returning a constant, because a Claude
Code project's storage directory name is derived from its absolute path and is therefore
machine-dependent. Every test here passes `tmp_path`, so nothing is ever created outside pytest's own
scratch directory and no test reads the machine's real `~/.claude` (CLAUDE.md invariant 6).
"""

from __future__ import annotations

import json

import pytest
from ctx_distillery_eval.taskset import (
    DEMO_DIR,
    EvalTask,
    collect_tasks,
    demo_taskset,
    load_taskset,
)
from rlm_harness.trace import EVENT_RESULT, TraceRecorder

from ctx_distillery.adapters.claude_code import (
    ClaudeCodeAdapter,
    sanitize_project_dir,
    transcript_files,
)

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


def test_collect_tasks_reads_the_envelope_run_id_not_the_filename_stem(tmp_path):
    """Load-bearing for `run`, which writes `<task.id>-<UTC stamp>.jsonl` (unique per invocation,
    because `TraceRecorder` appends and nothing in this project may `os.remove` a stale trace) while
    keeping `run_id == task.id`. If the pairing keyed on the filename instead, that timestamp would
    silently break `score --taskset`'s reference lookup."""
    _trace(tmp_path / "my-task-20260728T101500Z.jsonl", "my-task")
    assert [t.run_id for t in collect_tasks(str(tmp_path / "*.jsonl"))] == ["my-task"]


# -- the taskset: EvalTask / load_taskset -----------------------------------------------------


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_load_taskset_accepts_a_bare_list_and_a_tasks_envelope(tmp_path):
    entries = [{"id": "a", "reference": "r"}]
    bare = load_taskset(_write(tmp_path / "bare.json", entries))
    wrapped = load_taskset(_write(tmp_path / "wrapped.json", {"tasks": entries}))
    assert [t.id for t in bare] == [t.id for t in wrapped] == ["a"]
    assert bare[0].reference == "r"


def test_project_is_optional_so_a_score_only_taskset_is_legal(tmp_path):
    """`score --taskset` needs nothing but `{id, reference}` — it reads finished traces. `run` is
    where a missing project is refused, and it refuses per-task rather than aborting the batch."""
    tasks = load_taskset(_write(tmp_path / "ts.json", [{"id": "a", "reference": "r"}]))
    assert tasks[0].project == {}


def test_load_taskset_refuses_a_non_list_payload(tmp_path):
    with pytest.raises(ValueError):
        load_taskset(_write(tmp_path / "ts.json", {"nope": 1}))


def test_load_taskset_refuses_a_missing_or_empty_id(tmp_path):
    with pytest.raises(ValueError, match="item 1"):
        load_taskset(_write(tmp_path / "ts.json", [{"id": "a"}, {"reference": "r"}]))
    with pytest.raises(ValueError, match="item 0"):
        load_taskset(_write(tmp_path / "empty.json", [{"id": ""}]))


def test_load_taskset_refuses_a_non_object_project(tmp_path):
    with pytest.raises(ValueError, match="'project' must be an object"):
        load_taskset(_write(tmp_path / "ts.json", [{"id": "a", "project": "/some/path"}]))


def test_load_taskset_refuses_duplicate_ids(tmp_path):
    """Duplicate ids would silently collapse the reference lookup AND make two runs share one trace
    filename — the check is imperative in the loader because it is a property of the FILE, not of any
    single `EvalTask` (no sibling has a pydantic validator on theirs either)."""
    with pytest.raises(ValueError, match="duplicate task ids"):
        load_taskset(_write(tmp_path / "ts.json", [{"id": "a"}, {"id": "b"}, {"id": "a"}]))


def test_the_checked_in_example_taskset_loads():
    """`eval/taskset.example.json` is documentation that can go stale — so it is parsed here."""
    tasks = load_taskset(str(DEMO_DIR.parent.parent / "taskset.example.json"))
    assert [t.id for t in tasks] == ["acme-widgets-2026-07", "score-only-example"]
    assert tasks[0].project.get("project_dir") and tasks[0].project.get("claude_home")
    assert tasks[1].project == {}  # the {id, reference}-only shape, exercised in the example itself


# -- demo_taskset: the materialization, and who owns it ----------------------------------------


def test_demo_taskset_materializes_under_the_caller_supplied_root(tmp_path):
    """The signature is `demo_taskset(root)` for a LIFETIME reason, not a stylistic one: a `mkdtemp`
    inside would leak a tree per invocation, and a `TemporaryDirectory` cleaned at function exit
    would delete the transcripts before `run` ever read them. So the caller owns the directory —
    `run` passes its `--out`, a test passes `tmp_path` — and nothing lands anywhere else."""
    tasks = demo_taskset(tmp_path)
    assert [t.id for t in tasks] == ["demo-durable-fact", "demo-one-off-debugging"]
    for task in tasks:
        project_dir = tmp_path / task.id
        home = tmp_path / "claude-home"
        assert task.project == {"project_dir": str(project_dir), "claude_home": str(home)}
        assert project_dir.is_dir()
        # everything created is inside the caller's root, nowhere else
        assert tmp_path in project_dir.parents and tmp_path in home.parents


def test_demo_taskset_writes_a_transcript_the_product_actually_discovers(tmp_path):
    """The point of materializing at all: `<claude_home>/projects/<sanitize(abs path)>/` is derived
    from the project's ABSOLUTE path, so a checked-in JSON constant could not name it. This asserts
    the layout through the product's OWN discovery functions rather than re-spelling the convention
    — the reader and this writer must not be able to disagree about a location."""
    tasks = demo_taskset(tmp_path)
    home = tmp_path / "claude-home"
    for task in tasks:
        project_dir = tmp_path / task.id
        found = transcript_files(project_dir, home=home)
        assert len(found) == 1
        assert found[0].parent.name == sanitize_project_dir(project_dir)
        adapter = ClaudeCodeAdapter.for_project(project_dir, home=home)
        raw = adapter.ingest()
        assert len(raw.transcripts) == 1 and raw.transcripts[0].strip()
        assert raw.memory_index == []  # a fresh project: nothing promoted yet


def test_demo_taskset_transcript_content_is_checked_in_data_not_generated(tmp_path):
    """C4's other half: only the LAYOUT is materialized. The transcript bodies stay as reviewable
    fixture files beside the module — the property every sibling's static-JSON demo taskset has, and
    the one a "generate the content in Python" version would throw away."""
    fixtures = sorted(p.name for p in DEMO_DIR.glob("*.jsonl"))
    assert fixtures == ["durable-fact.jsonl", "one-off-debugging.jsonl"]
    demo_taskset(tmp_path)
    storage = (
        tmp_path / "claude-home" / "projects"
        / sanitize_project_dir(tmp_path / "demo-durable-fact")
    )
    written = (storage / "demo-durable-fact-session.jsonl").read_bytes()
    assert written == (DEMO_DIR / "durable-fact.jsonl").read_bytes()


def test_demo_taskset_covers_both_poles_and_says_so_in_the_reference(tmp_path):
    """Every sibling's `demo_taskset` docstring argues for covering both poles. Here they are the
    two failure modes a distillation planner actually has: losing a durable fact, and over-promoting
    a one-off."""
    durable, one_off = demo_taskset(tmp_path)
    assert "PROMOTE" in durable.reference
    assert "OVER-PROMOTION" in one_off.reference and "restraint" in one_off.reference


def test_demo_taskset_is_idempotent_over_the_same_root(tmp_path):
    """A second `run demo --out <same dir>` must not be an error — and must not accumulate."""
    first = demo_taskset(tmp_path)
    second = demo_taskset(tmp_path)
    assert [t.model_dump() for t in first] == [t.model_dump() for t in second]
    storage_root = tmp_path / "claude-home" / "projects"
    assert len(list(storage_root.iterdir())) == 2


def test_eval_task_defaults_are_the_score_only_shape():
    task = EvalTask(id="a")
    assert task.project == {} and task.reference == ""
