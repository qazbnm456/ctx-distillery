"""`ctx_distillery.rl_export` — the reward-free dataset bundle, plus `ctx-distillery export`.

Events are hand-rolled dicts, following `tests/test_rubric.py`'s established convention for this
repo (the `_tool_call()`-style helper), with ONE exception that writes a real JSONL file: `load_runs`
is a file reader, so its non-dict-line guard and its multi-file grouping can only be exercised
against real bytes on disk.

The two properties this file exists to pin, above the field-by-field checks:

* **Nothing here is an oracle.** `run_labels` counts what `schema.assemble` already established;
  no field claims a judgement was correct. See `rl_export`'s module docstring for why the original
  design's blanket refusal of a `run_labels` surface was aimed at the wrong half of the problem.
* **Nothing here writes.** `rl_export` has no `main()` and the CLI has no `--out`, because
  `tests/test_no_write_capability.py` scans both modules. That scan already covers them
  parametrically; `test_the_exporter_exposes_no_writing_entry_point` states the intent by name so a
  future author sees WHY the sibling projects' `main()` is missing rather than assuming an oversight.
"""

from __future__ import annotations

import json

import pytest
from rlm_kit.rubric import Criterion, RubricCriteria
from rlm_kit.trace import (
    EVENT_MAIN_STEP,
    EVENT_RESULT,
    EVENT_RUN_START,
    EVENT_SUB_CALL,
    EVENT_TOOL_CALL,
)

from ctx_distillery import cli, rl_export
from ctx_distillery.rubric import rubric_to_meta
from ctx_distillery.task import DistillCandidate, DistillPlan

DRAFT = (
    "---\n"
    "name: merge-freeze-policy\n"
    "description: Merges are frozen during a release.\n"
    "---\n"
    "Merges into main are frozen for the duration of a release.\n"
)


def _tool_call(tool, *, run_id="r0", step_id=0, ts=None, artifact_id=None, ok=True,
               draft=DRAFT, circuit_broken=False, **extra):
    payload = {"tool": tool, "ok": ok, "circuit_broken": circuit_broken, **extra}
    if artifact_id is not None:
        payload |= {"artifact_id": artifact_id, "draft": draft, "errors": []}
    event = {"type": EVENT_TOOL_CALL, "run_id": run_id, "step_id": step_id, "payload": payload}
    if ts is not None:
        event["ts"] = ts
    return event


def _main_step(turn, *, run_id="r0", step_id=0, ts=None, code="print(1)"):
    event = {
        "type": EVENT_MAIN_STEP,
        "run_id": run_id,
        "step_id": step_id,
        "payload": {"turn": turn, "reasoning": f"turn {turn}", "code": code, "output": "1"},
    }
    if ts is not None:
        event["ts"] = ts
    return event


def _result(plan: DistillPlan, *, run_id="r0", step_id=99):
    return {
        "type": EVENT_RESULT,
        "run_id": run_id,
        "step_id": step_id,
        "payload": {"output": plan.model_dump()},
    }


def _run_start(*, run_id="r0", meta=None):
    return {"type": EVENT_RUN_START, "run_id": run_id, "step_id": 0, "payload": {"meta": meta or {}}}


def _plan(*candidates: dict) -> DistillPlan:
    return DistillPlan(candidates=[DistillCandidate(**c) for c in candidates])


PROMOTION = {"action": "promote_to_memory", "artifact_id": "artifact-1", "key_fields": {}}
SKILL = {"action": "promote_to_skill", "artifact_id": "artifact-2", "key_fields": {"scope": "global"}}
KEEP = {"action": "keep", "key_fields": {"reason": "still relevant"}}
PRUNE = {"action": "prune", "key_fields": {"target_path": "/m/old.md"}}


def _full_run(run_id="r0"):
    """A realistic finished run: read, draft, submit — one candidate of every action."""
    return [
        _run_start(run_id=run_id, meta={"max_iterations": 30, "project_dir": "/p"}),
        _main_step(0, run_id=run_id, step_id=1, ts=100.0),
        _tool_call("list_memory_files", run_id=run_id, step_id=2, ts=100.5, count=2),
        _tool_call("read_transcript_chunk", run_id=run_id, step_id=3, ts=101.0),
        _tool_call("read_memory_file", run_id=run_id, step_id=4, ts=101.5),
        _main_step(1, run_id=run_id, step_id=5, ts=102.0),
        _tool_call("draft_memory_file", run_id=run_id, step_id=6, ts=102.5, artifact_id="artifact-1"),
        _tool_call("draft_skill_file", run_id=run_id, step_id=7, ts=103.0, artifact_id="artifact-2"),
        {"type": EVENT_SUB_CALL, "run_id": run_id, "step_id": 8,
         "payload": {"model": "specialist", "input": "?", "processed": "!"}},
        _result(_plan(PROMOTION, SKILL, KEEP, PRUNE), run_id=run_id, step_id=9),
    ]


# -- load_runs ---------------------------------------------------------------------------------


def _write_jsonl(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return str(path)


def test_load_runs_groups_multiple_files_by_run_id(tmp_path):
    first = _write_jsonl(tmp_path / "a.jsonl", _full_run("r0"))
    second = _write_jsonl(tmp_path / "b.jsonl", _full_run("r1"))
    runs = rl_export.load_runs(first, second)
    assert sorted(runs) == ["r0", "r1"]
    assert len(runs["r0"]) == len(runs["r1"]) == 10


def test_load_runs_drops_non_dict_lines(tmp_path):
    """`CLAUDE.md` invariant 11: a new reader is a new call site for the `trace_io` guard, not an
    exception to it. `rlm_kit.trace.load_events` would hand `42` / `null` straight through, and the
    very first `event.get("type")` below would raise."""
    path = tmp_path / "dirty.jsonl"
    lines = [json.dumps(e) for e in _full_run("r0")]
    lines.insert(0, "42")
    lines.append("null")
    lines.append(json.dumps([1, 2, 3]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runs = rl_export.load_runs(str(path))
    assert list(runs) == ["r0"]
    assert len(runs["r0"]) == 10
    # and the whole bundle builds over it without raising
    assert rl_export.export_dataset(runs)["labels"]["r0"]["finalized"] is True


# -- run_labels: structural, never an oracle ----------------------------------------------------


def test_run_labels_counts_the_action_histogram():
    labels = rl_export.run_labels(_full_run())
    assert labels["finalized"] is True
    assert labels["n_candidates"] == 4
    assert (labels["n_keep"], labels["n_prune"]) == (1, 1)
    assert (labels["n_promote_memory"], labels["n_promote_skill"]) == (1, 1)
    assert labels["n_unbacked"] == 0
    assert labels["n_draft_not_ok"] == 0
    assert labels["plan_problems"] == []


def test_run_labels_is_not_finalized_without_a_result_event():
    events = [e for e in _full_run() if e["type"] != EVENT_RESULT]
    labels = rl_export.run_labels(events)
    assert labels["finalized"] is False
    assert labels["n_candidates"] == 0
    assert labels["plan_problems"] == ["no plan was produced by this run"]


def test_run_labels_counts_a_fabricated_artifact_id_as_unbacked():
    """The plan names an artifact no drafting call produced — `CLAUDE.md` invariant 2's failure mode,
    and exactly the candidate `apply.apply_plan` refuses."""
    events = [
        _run_start(),
        _result(_plan({"action": "promote_to_memory", "artifact_id": "ghost"}, KEEP)),
    ]
    labels = rl_export.run_labels(events)
    assert labels["n_unbacked"] == 1
    assert labels["n_draft_not_ok"] == 1
    assert labels["n_candidates"] == 2


def test_run_labels_separates_unbacked_from_draft_not_ok():
    """A drafting call that RAN but failed its validator: backed by a real event, `draft_ok` False.

    Both counters fire here, and that overlap is deliberate — `n_unbacked` answers "was anything
    wrong with this candidate", `n_draft_not_ok` answers "did the drafter produce valid bytes".
    """
    events = [
        _run_start(),
        _tool_call("draft_memory_file", step_id=1, artifact_id="artifact-1", ok=False),
        _result(_plan(PROMOTION)),
    ]
    labels = rl_export.run_labels(events)
    assert labels["n_draft_not_ok"] == 1
    assert labels["n_unbacked"] == 1


@pytest.mark.parametrize(
    "payload_extra",
    [
        {},                                            # the validator declined the text
        {"endpoint_error": "Connection refused"},       # the endpoint died after its retries
        {"circuit_broken": True},                       # the breaker never called the model
    ],
)
def test_n_draft_not_ok_is_a_deliberate_aggregate_over_all_three_causes(payload_extra):
    """It counts "the drafter yielded no usable bytes", NOT "the validator declined".

    The distinction matters because this label reads `AssembledCandidate.draft_ok`, which is
    per-candidate and cause-blind by design — so all three of `make_model_tool`'s `ok=False` causes
    land here identically, and the DOCSTRING must say that rather than name the validator. A reader
    who wants the cause split reads `run_metrics`, which sees the drafting payloads directly.
    """
    events = [
        _run_start(),
        _tool_call("draft_memory_file", step_id=1, artifact_id="artifact-1", ok=False,
                   **payload_extra),
        _result(_plan(PROMOTION)),
    ]
    assert rl_export.run_labels(events)["n_draft_not_ok"] == 1


def test_run_labels_carries_no_correctness_field():
    """The oracle boundary, stated as a test: nothing here claims the plan was RIGHT.

    `cve-reverser`'s `valid`/`complete` is the one sibling label with ground truth behind it (does
    this template match the patch?). ctx-distillery has no such oracle for "was this the right thing
    to prune", and inventing one would fabricate the exact signal the design says does not exist.
    """
    labels = rl_export.run_labels(_full_run())
    for forbidden in ("valid", "complete", "correct", "score", "reward", "met", "quality"):
        assert forbidden not in labels


# -- run_metrics --------------------------------------------------------------------------------


def test_run_metrics_counts_every_seat_separately():
    metrics = rl_export.run_metrics(_full_run())
    assert metrics["steps"] == 2
    assert metrics["list_memory_files_calls"] == 1
    assert metrics["read_memory_file_calls"] == 1
    assert metrics["read_transcript_chunk_calls"] == 1
    assert metrics["draft_memory_file_calls"] == 1
    assert metrics["draft_skill_file_calls"] == 1
    assert metrics["draft_not_ok"] == 0
    assert metrics["draft_validator_rejects"] == 0
    assert metrics["draft_endpoint_errors"] == 0
    assert metrics["draft_circuit_breaks"] == 0
    assert metrics["sub_calls"] == 1
    assert metrics["elapsed_s"] == 3.0
    assert metrics["hit_iteration_cap"] is False


def test_run_metrics_tells_the_three_ok_false_causes_apart():
    """`make_model_tool` reports `ok=False` for three different things, and only one is the
    validator. Folding them into one "rejects" count taught a trainer to read a 502 as model
    dishonesty — see `rl_export._draft_cause` and `schema._not_ok_problem`."""
    events = [
        _run_start(),
        _tool_call("draft_memory_file", step_id=1, artifact_id="a", ok=False),
        _tool_call("draft_memory_file", step_id=2, artifact_id="b", ok=False,
                   endpoint_error="Connection refused"),
        _tool_call("draft_skill_file", step_id=3, artifact_id="c", ok=False, circuit_broken=True),
        _tool_call("draft_skill_file", step_id=4, artifact_id="d", ok=True),
    ]
    metrics = rl_export.run_metrics(events)
    assert metrics["draft_validator_rejects"] == 1
    assert metrics["draft_endpoint_errors"] == 1
    assert metrics["draft_circuit_breaks"] == 1
    assert metrics["draft_not_ok"] == 3  # the successful call is not in any bucket


def test_run_metrics_causes_partition_the_aggregate():
    """The containment relation, pinned — because the comment that USED to sit on these counters
    claimed the breaks were "a subset of the first's *cause*, not of its count", and the test right
    beside it disproved that in the same file (a plain `ok=False` plus an `ok=False` +
    `circuit_broken` gave `draft_rejects == 2`, `draft_circuit_breaks == 1` — the break WAS inside
    the total). The three causes are now DISJOINT and sum EXACTLY to `draft_not_ok`, which is a
    claim a reader can act on: slice, or total, never both.

    The last event sets `circuit_broken` AND `endpoint_error` together — something
    `make_model_tool` never does — precisely because `_draft_cause` classifies in a chain rather
    than as three independent predicates, so the identity holds even for a hand-written trace.
    """
    events = [
        _run_start(),
        _tool_call("draft_memory_file", step_id=1, artifact_id="a", ok=False),
        _tool_call("draft_memory_file", step_id=2, artifact_id="b", ok=False, circuit_broken=True),
        _tool_call("draft_skill_file", step_id=3, artifact_id="c", ok=False,
                   endpoint_error="502 Bad Gateway"),
        _tool_call("draft_skill_file", step_id=4, artifact_id="d", ok=False,
                   circuit_broken=True, endpoint_error="502 Bad Gateway"),
        _tool_call("draft_memory_file", step_id=5, artifact_id="e", ok=True),
    ]
    m = rl_export.run_metrics(events)
    parts = ("draft_validator_rejects", "draft_endpoint_errors", "draft_circuit_breaks")
    assert sum(m[part] for part in parts) == m["draft_not_ok"] == 4
    assert m["draft_circuit_breaks"] == 2  # the both-flags call counts ONCE, as the stronger claim
    assert m["draft_not_ok"] <= m["draft_memory_file_calls"] + m["draft_skill_file_calls"]


def test_an_endpoint_error_that_STRINGIFIED_TO_NOTHING_is_not_counted_as_a_validator_reject():
    """`endpoint_error` is `Optional[str]` and rlm-kit sets `str(exc)`, which is `''` for
    `httpx.ConnectTimeout`/`ReadTimeout`/`ConnectError`, `TimeoutError`, `OSError` and
    `RemoteDisconnected`. Under a truthiness test every one of those was counted in
    `draft_validator_rejects` — TRAINING SIGNAL teaching a trainer to read a dropped connection as
    model dishonesty, which is exactly the harm invariant 12 names.
    """
    events = [
        _run_start(),
        _tool_call("draft_memory_file", step_id=1, artifact_id="a", ok=False, endpoint_error=""),
    ]
    metrics = rl_export.run_metrics(events)
    assert metrics["draft_endpoint_errors"] == 1
    assert metrics["draft_validator_rejects"] == 0
    assert metrics["draft_not_ok"] == 1


def test_run_metrics_names_no_counter_after_the_validator_that_it_does_not_mean():
    """The old key was `draft_rejects` — a "reject" of three causes only one of which is a
    rejection. Only the genuinely validator-scoped counter may carry validator vocabulary."""
    metrics = rl_export.run_metrics(_full_run())
    assert "draft_rejects" not in metrics
    assert [k for k in metrics if "reject" in k] == ["draft_validator_rejects"]


def test_run_metrics_hits_the_cap_from_the_runs_own_meta():
    """The budget comes from the run's `run_start` meta (`cli._cmd_distill` stamps it), not a guess."""
    events = [_run_start(meta={"max_iterations": 2})] + [
        _main_step(i, step_id=i + 1) for i in range(2)
    ]
    assert rl_export.run_metrics(events)["hit_iteration_cap"] is True


def test_run_metrics_falls_back_to_the_default_cap_on_a_legacy_trace():
    events = [_run_start(meta={})] + [_main_step(i, step_id=i + 1) for i in range(30)]
    assert rl_export.run_metrics(events)["hit_iteration_cap"] is True
    assert rl_export.run_metrics(events[:-1])["hit_iteration_cap"] is False


def test_run_metrics_elapsed_is_none_without_two_timestamps():
    assert rl_export.run_metrics([_run_start()])["elapsed_s"] is None


# -- rubric_signal ------------------------------------------------------------------------------


def test_rubric_signal_backfills_the_default_rubric_on_a_legacy_trace():
    """`cve-reverser`/`diff-sentry`'s backfill, NOT `toolscout`'s bare form — a trace recorded before
    the rubric existed must not report an EMPTY rubric beside a full set of facts."""
    signal = rl_export.rubric_signal(_full_run())
    assert [c["name"] for c in signal["rubric"]] == [
        "plan_carries_real_judgement",
        "evidence_gathered_before_drafting",
        "candidates_backed_by_real_drafts",
        "plan_structurally_well_formed",
    ]
    assert len(signal["criteria_facts"]) == len(signal["rubric"])


def test_rubric_signal_reports_the_runs_own_recorded_rubric():
    custom = RubricCriteria(
        criteria=[Criterion(name="only_one", category="TF", weight=1.0, description="the plan exists")]
    )
    events = [_run_start(meta={"rubric": rubric_to_meta(custom)}), *_full_run()[1:]]
    signal = rl_export.rubric_signal(events)
    assert [c["name"] for c in signal["rubric"]] == ["only_one"]
    # facts are computed against the SAME criteria that are reported — no orphans either way
    assert [f["criterion"] for f in signal["criteria_facts"]] == ["only_one"]


def test_rubric_signal_has_no_judge_observations():
    """`toolscout` nests them because it wires an in-trajectory `rubric_judge` TOOL; ours lives in
    the separate `eval/` member and never writes into the trace this module reads."""
    assert set(rl_export.rubric_signal(_full_run())) == {"rubric", "criteria_facts"}


# -- export_dataset -----------------------------------------------------------------------------


def test_export_dataset_top_level_keys():
    bundle = rl_export.export_dataset({"r0": _full_run()})
    assert set(bundle) == {
        "actions", "drafting", "orchestrator_tools", "planner", "sft_turns",
        "labels", "metrics", "rubric_signal",
    }
    assert "reward" not in bundle


def test_every_action_record_is_reward_free():
    bundle = rl_export.export_dataset({"r0": _full_run()})
    assert bundle["actions"]
    assert all(record["reward"] is None for record in bundle["actions"])


def test_the_role_split_is_by_tool_name():
    bundle = rl_export.export_dataset({"r0": _full_run()})
    assert [a["tool"] for a in bundle["drafting"]] == ["draft_memory_file", "draft_skill_file"]
    assert [a["tool"] for a in bundle["orchestrator_tools"]] == [
        "list_memory_files", "read_transcript_chunk", "read_memory_file",
    ]
    assert len(bundle["planner"]) == 2


def test_the_drafting_split_is_not_filtered_on_outcome_output():
    """The sibling exporters narrow their generator split to records with a non-empty
    `outcome.output`; here that filter would produce an EMPTY split and look like "the planner never
    drafted". `tools/drafting.py` records the bytes under `draft=`, and rlm-kit's `_action_record`
    only reads `raw`/`result`/`results`/`preview` — so `outcome.output` is None for every one of
    this project's tool calls. Pinned, because the fix for a fabricated-looking empty split would
    otherwise be to re-add the filter."""
    bundle = rl_export.export_dataset({"r0": _full_run()})
    assert bundle["drafting"], "the split must not be empty"
    assert all(a["outcome"]["output"] is None for a in bundle["drafting"])


def test_slices_never_lose_an_action():
    """`actions` is the complete stream; the two splits are slices of it, so an unknown tool lands in
    neither list but is never dropped."""
    events = [*_full_run(), _tool_call("some_future_tool", step_id=20)]
    bundle = rl_export.export_dataset({"r0": events})
    tools = [a for a in bundle["actions"] if a["kind"] == "tool"]
    assert len(tools) == 6
    assert len(bundle["drafting"]) + len(bundle["orchestrator_tools"]) == 5


def test_sft_turns_seed_the_history_with_the_runs_meta():
    bundle = rl_export.export_dataset({"r0": _full_run()})
    assert len(bundle["sft_turns"]) == 2
    first = bundle["sft_turns"][0]
    assert first["input"]["initial"]["project_dir"] == "/p"
    assert first["input"]["history"] == []
    assert first["output"]["code"] == "print(1)"


def test_the_label_surfaces_are_keyed_by_run_id():
    bundle = rl_export.export_dataset({"r0": _full_run("r0"), "r1": _full_run("r1")})
    for surface in ("labels", "metrics", "rubric_signal"):
        assert sorted(bundle[surface]) == ["r0", "r1"]


def test_a_reward_surface_is_structurally_refused_by_the_transport():
    """Not our check — rlm-kit's. Stated here so the guarantee is visible where the bundle is built."""
    from rlm_kit.dataset import run_label_bundle

    with pytest.raises(ValueError) as excinfo:
        run_label_bundle({"r0": _full_run()}, reward=rl_export.run_labels)
    assert "never reward" in str(excinfo.value)


def test_the_bundle_is_json_serialisable():
    bundle = rl_export.export_dataset({"r0": _full_run()})
    assert json.loads(json.dumps(bundle, default=str))["labels"]["r0"]["n_candidates"] == 4


# -- the no-writer shape ------------------------------------------------------------------------


def test_the_exporter_exposes_no_writing_entry_point():
    """The sibling projects' `rl_export.main()` ends in `open(out, "w")`; `CLAUDE.md` invariant 1's
    scan covers this module, and a red tripwire IS the finding. So there is no `main` and no
    `__out__`-shaped parameter anywhere — the CLI prints to stdout instead."""
    assert not hasattr(rl_export, "main")
    assert "main" not in rl_export.__all__


# -- the CLI subcommand -------------------------------------------------------------------------


def test_parser_wires_the_export_subcommand():
    args = cli.build_parser().parse_args(["export", "traces/*.jsonl"])
    assert args.func is cli._cmd_export
    assert args.trace == ["traces/*.jsonl"]


def test_export_has_no_out_option():
    """Same reason `show` has none: this module is inside the mutation scan. Redirect with `>`."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["export", "traces/*.jsonl", "--out", "ds.json"])


def test_export_prints_the_bundle_to_stdout(tmp_path, capsys):
    _write_jsonl(tmp_path / "r0.jsonl", _full_run("r0"))
    assert cli.main(["export", str(tmp_path / "*.jsonl")]) == 0
    captured = capsys.readouterr()
    bundle = json.loads(captured.out)
    assert set(bundle) >= {"actions", "drafting", "sft_turns", "labels"}
    assert bundle["labels"]["r0"]["finalized"] is True
    # the one-line summary goes to STDERR so `> ds.json` yields valid JSON and nothing else
    assert "reward-free" in captured.err


def test_export_reads_several_traces_at_once(tmp_path, capsys):
    _write_jsonl(tmp_path / "r0.jsonl", _full_run("r0"))
    _write_jsonl(tmp_path / "r1.jsonl", _full_run("r1"))
    assert cli.main(["export", str(tmp_path / "*.jsonl")]) == 0
    assert sorted(json.loads(capsys.readouterr().out)["labels"]) == ["r0", "r1"]


def test_export_refuses_an_empty_match(tmp_path, capsys):
    """A mistyped or unquoted glob must not print a well-formed dataset containing zero runs."""
    assert cli.main(["export", str(tmp_path / "nothing-*.jsonl")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no trace files matched" in captured.err


def test_export_reports_an_unreadable_trace_without_a_traceback(tmp_path, capsys):
    (tmp_path / "torn.jsonl").write_text("{not json at all\n", encoding="utf-8")
    assert cli.main(["export", str(tmp_path / "*.jsonl")]) == 1
    assert "cannot read the trace(s)" in capsys.readouterr().err
