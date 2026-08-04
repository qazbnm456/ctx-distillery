"""`ctx_distillery_eval.cli` — the "mandatory transcript" content check, and batch survival.

Added per adversarial review, which found `cli.py`/`taskset.py` had NO test coverage at all before
this — exactly the surface the "transcript is mandatory" design decision lives in, and exactly
where two real gaps went unnoticed (the CI job never running this suite at all, and an empty
transcript file slipping through silently). This file still does not attempt full CLI/argparse
coverage — that remains a stated gap — it targets the concrete bugs actually found: the empty
transcript, and (below) a malformed trace line taking down an entire scoring batch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ctx_distillery_eval.cli import _pick_judge, _read_transcripts, main, render_scorecard
from ctx_distillery_eval.judge import PROMPT_VERSION, JudgeVerdict, StubJudge
from ctx_distillery_eval.schema import EvalReport, EvalRow, EvalScore
from rlm_harness.trace import EVENT_RESULT, TraceRecorder, record_tool_call

from ctx_distillery.task import DistillCandidate, DistillPlan


def test_read_transcripts_accepts_non_empty_files(tmp_path):
    path = tmp_path / "t.txt"
    path.write_text("a real transcript excerpt", encoding="utf-8")
    assert _read_transcripts([str(path)]) == ["a real transcript excerpt"]


def test_read_transcripts_refuses_an_empty_file(tmp_path):
    """FIXED per adversarial review: an empty (or whitespace-only) transcript used to slip straight
    through and score to completion — exactly the failure mode "transcript is mandatory" exists to
    prevent, just reached via content rather than via an omitted CLI argument."""
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        _read_transcripts([str(path)])


def test_read_transcripts_refuses_a_whitespace_only_file(tmp_path):
    path = tmp_path / "blank.txt"
    path.write_text("   \n\n  ", encoding="utf-8")
    with pytest.raises(SystemExit):
        _read_transcripts([str(path)])


def test_read_transcripts_refuses_if_any_one_of_several_is_empty(tmp_path):
    good = tmp_path / "good.txt"
    good.write_text("real content", encoding="utf-8")
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        _read_transcripts([str(good), str(empty)])


# -- the batch really survives one malformed trace ---------------------------------------------

_DRAFT = "---\nname: merge-freeze-policy\ndescription: Merges are frozen.\n---\nbody\n"

#: Valid JSON, but not an object — the exact shapes `rlm_harness.trace.load_events` passes through.
NON_DICT_LINES = ("42", "null", '"x"', "[1, 2, 3]")


def _recorded_trace(path, run_id):
    """A REAL `TraceRecorder` trace, not hand-written JSON: this test turns on the `run_id`
    ENVELOPE key (which `collect_tasks` reads) and on `record_result`'s real payload shape, so
    hand-rolling either would assert against a guess instead of what rlm-harness actually writes."""
    plan = DistillPlan(candidates=[DistillCandidate(action="promote_to_memory", artifact_id="a1")])
    with TraceRecorder(str(path), run_id=run_id, meta={"transcripts": 1}) as rec:
        record_tool_call("draft_memory_file", ok=True, artifact_id="a1", draft=_DRAFT, errors=[])
        rec.record_result(plan)
    return path


def test_score_command_survives_a_non_dict_line_in_one_trace_of_a_batch(tmp_path, capsys):
    """THE regression test for this fix. `rlm_harness.trace.load_events` does no shape validation, so a
    JSONL line that is valid JSON but not an object reached three separate unguarded `.get(...)`
    calls on this exact path: `taskset.collect_tasks`'s `e.get("run_id")`, then `load_events`'s OWN
    `run_id` filter (inside rlm-harness, upstream of anything `ctx_distillery` could harden), then
    `session.assemble` via `_draft_calls`.

    Reproduced before the fix: scoring a glob of one CLEAN trace plus one carrying a single `42`
    line raised a raw `AttributeError` in `collect_tasks` and scored ZERO runs — the clean one
    included. So it is not enough that the command survives; BOTH runs must still appear in the
    scorecard, which is what distinguishes a real fix from silently skipping the bad file.
    """
    _recorded_trace(tmp_path / "good.jsonl", "good-run")
    weird = _recorded_trace(tmp_path / "weird.jsonl", "weird-run")
    with open(weird, "a", encoding="utf-8") as fh:
        fh.write("\n".join(NON_DICT_LINES) + "\n")
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("a real transcript excerpt", encoding="utf-8")

    assert main(["score", str(tmp_path / "*.jsonl"), str(transcript)]) == 0

    out = capsys.readouterr().out
    assert "good-run" in out and "weird-run" in out  # the WHOLE batch scored, not just the clean one
    assert "mean" in out


def test_score_command_reports_no_runs_found_for_an_empty_glob(tmp_path, capsys):
    """The neighbouring degrade, pinned while we are here: an unmatched glob is a non-zero exit with
    a message, never a traceback."""
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("a real transcript excerpt", encoding="utf-8")
    assert main(["score", str(tmp_path / "*.jsonl"), str(transcript)]) == 1
    assert "no runs found" in capsys.readouterr().err


# -- judge selection: live iff CDEVAL_MODEL, else the stub (parity pass 4) --------------------------


def test_pick_judge_returns_the_stub_when_no_model_is_configured():
    """The DEFAULT is offline. `conftest._offline_judge_env` scrubs `CDEVAL_*`, so this asserts the
    real fallback rather than the state of whoever's shell is running the suite."""
    judge, judge_model, prompt_version = _pick_judge(force_stub=False)
    assert isinstance(judge, StubJudge)
    assert judge_model == "stub"
    # Empty on purpose: a stub never rendered `build_prompt`, so pinning PROMPT_VERSION to a stub
    # scorecard would claim a provenance it does not have.
    assert prompt_version == ""


def test_pick_judge_goes_live_when_cdeval_model_is_set(monkeypatch):
    """Builds the live judge — it does NOT call it. `openai` is imported lazily inside the chat
    closure, so constructing one needs no credentials, no `judge` extra and no network."""
    monkeypatch.setenv("CDEVAL_MODEL", "judge-model-x")
    judge, judge_model, prompt_version = _pick_judge(force_stub=False)
    assert not isinstance(judge, StubJudge) and callable(judge)
    assert judge_model == "judge-model-x"
    assert prompt_version == PROMPT_VERSION


def test_pick_judge_honours_the_stub_flag_even_with_a_live_model_configured(monkeypatch):
    """`--stub` is what lets a live-configured shell still run an offline scoring pass."""
    monkeypatch.setenv("CDEVAL_MODEL", "judge-model-x")
    judge, judge_model, _ = _pick_judge(force_stub=True)
    assert isinstance(judge, StubJudge) and judge_model == "stub"


def test_score_command_accepts_the_stub_flag(tmp_path, transcript_file):
    _recorded_trace(tmp_path / "good.jsonl", "good-run")
    assert main(["score", str(tmp_path / "*.jsonl"), str(transcript_file), "--stub"]) == 0


# -- unscored rendering + the nothing-scored exit code ---------------------------------------------


def test_render_scorecard_shows_an_unscored_row_as_dashes_with_its_reason():
    """A failed judge must render as `--` plus its reason — never as a 0 (a claim the judge did not
    make) and never dropped from the listing (a batch where the judge died must LOOK like one)."""
    report = EvalReport(
        n=2, n_unscored=1, judge_model="judge-x", prompt_version=PROMPT_VERSION,
        means={"TF": 8.0, "TA": 8.0, "TG": 8.0, "PA": 8.0},
        rows=[
            EvalRow(run_id="ok-run", trace_path="a.jsonl", score=EvalScore(TF=8, TA=8, TG=8, PA=8)),
            EvalRow(run_id="dead-run", trace_path="b.jsonl",
                    unscored_reason="judge endpoint error: connection refused"),
        ],
    )
    text = render_scorecard(report)
    assert "dead-run" in text
    assert "unscored: judge endpoint error: connection refused" in text
    assert "0.0" not in text.splitlines()[2]  # the unscored row shows no number at all
    assert "--" in text.splitlines()[2]


def test_render_scorecard_shows_a_row_s_transcript_composition_when_known():
    report = EvalReport(
        n=1, judge_model="judge-x",
        rows=[
            EvalRow(
                run_id="r0", trace_path="a.jsonl", score=EvalScore(TF=8, TA=8, TG=8, PA=8),
                n_transcripts=3, transcript_sessions=2, transcript_subagents=1,
            ),
        ],
    )
    row_line = render_scorecard(report).splitlines()[1]
    assert "transcripts=3 (sessions=2 subagents=1)" in row_line


def test_render_scorecard_shows_the_bare_count_when_composition_itself_is_unrecorded():
    """An older trace recorded `n_transcripts` before subagent ingestion added `transcript_index` —
    the count is known, the breakdown is not, so only the bare form renders."""
    report = EvalReport(
        n=1, judge_model="judge-x",
        rows=[
            EvalRow(
                run_id="r0", trace_path="a.jsonl", score=EvalScore(TF=8, TA=8, TG=8, PA=8),
                n_transcripts=5,
            ),
        ],
    )
    row_line = render_scorecard(report).splitlines()[1]
    assert "transcripts=5" in row_line
    assert "sessions=" not in row_line


def test_render_scorecard_omits_the_transcripts_suffix_entirely_when_unrecorded():
    report = EvalReport(
        n=1, judge_model="judge-x",
        rows=[EvalRow(run_id="r0", trace_path="a.jsonl", score=EvalScore(TF=8, TA=8, TG=8, PA=8))],
    )
    row_line = render_scorecard(report).splitlines()[1]
    assert "transcripts=" not in row_line


def test_render_scorecard_shows_the_transcripts_suffix_on_an_unscored_row_too():
    report = EvalReport(
        n=1, n_unscored=1, judge_model="judge-x",
        rows=[
            EvalRow(
                run_id="dead-run", trace_path="b.jsonl",
                unscored_reason="judge endpoint error: connection refused",
                n_transcripts=2, transcript_sessions=2, transcript_subagents=0,
            ),
        ],
    )
    row_line = render_scorecard(report).splitlines()[1]
    assert "unscored: judge endpoint error: connection refused" in row_line
    assert "transcripts=2 (sessions=2 subagents=0)" in row_line


def test_render_scorecard_footer_never_carries_a_transcript_composition():
    """The design constraint: composition is PER ROW, never folded into the aggregate footer."""
    report = EvalReport(
        n=1, judge_model="judge-x",
        rows=[
            EvalRow(
                run_id="r0", trace_path="a.jsonl", score=EvalScore(TF=8, TA=8, TG=8, PA=8),
                n_transcripts=3, transcript_sessions=2, transcript_subagents=1,
            ),
        ],
    )
    footer = render_scorecard(report).splitlines()[-1]
    assert "transcripts=" not in footer


def test_render_scorecard_footer_states_the_denominator_and_the_provenance():
    report = EvalReport(n=3, n_unscored=1, judge_model="judge-x", prompt_version=PROMPT_VERSION)
    footer = render_scorecard(report).splitlines()[-1]
    assert "n=3 (1 unscored)" in footer
    assert "judge=judge-x" in footer
    assert f"prompt={PROMPT_VERSION}" in footer


def test_render_scorecard_footer_states_the_prompts_LENGTH_CAPS():
    """A cap is prompt-affecting provenance: a plan or a transcript that was elided was not fully
    read, and two scorecards are only comparable if the budgets were the same. They ride with
    `prompt=`, so the stub — which never rendered a prompt — claims neither."""
    from ctx_distillery_eval.judge import (
        JUDGE_MAX_PLAN_CHARS,
        JUDGE_MAX_TOTAL_CHARS,
        JUDGE_MAX_TRANSCRIPT_CHARS,
    )

    footer = render_scorecard(
        EvalReport(n=1, judge_model="judge-x", prompt_version=PROMPT_VERSION)
    ).splitlines()[-1]
    assert (
        f"caps=plan:{JUDGE_MAX_PLAN_CHARS}/transcript:{JUDGE_MAX_TRANSCRIPT_CHARS}"
        f"/total:{JUDGE_MAX_TOTAL_CHARS}"
    ) in footer

    stub_footer = render_scorecard(EvalReport(n=1, judge_model="stub")).splitlines()[-1]
    assert "caps=" not in stub_footer


def test_score_command_exits_non_zero_when_the_judge_scored_nothing(tmp_path, transcript_file,
                                                                   monkeypatch, capsys):
    """The CI gate: an all-unscored scorecard is not a green run. Without this, a batch scored by a
    dead judge prints a table of `--` and exits 0, which reads as a pass."""
    _recorded_trace(tmp_path / "good.jsonl", "good-run")

    def dead_judge(plan_text, transcript_texts, reference=""):
        return JudgeVerdict(ok=False, reason="judge endpoint error: connection refused")

    monkeypatch.setattr("ctx_distillery_eval.cli._pick_judge",
                        lambda force_stub: (dead_judge, "judge-x", PROMPT_VERSION))
    assert main(["score", str(tmp_path / "*.jsonl"), str(transcript_file)]) == 1
    out = capsys.readouterr().out
    assert "good-run" in out and "unscored:" in out
    assert "(no runs scored)" in out  # no means at all, rather than a row of zeros


def test_score_command_footer_names_the_stub_on_the_offline_path(tmp_path, transcript_file, capsys):
    _recorded_trace(tmp_path / "good.jsonl", "good-run")
    assert main(["score", str(tmp_path / "*.jsonl"), str(transcript_file)]) == 0
    footer = capsys.readouterr().out.strip().splitlines()[-1]
    assert "n=1 (0 unscored)" in footer and "judge=stub" in footer
    assert "prompt=" not in footer  # the stub claims no prompt provenance


# -- `score --taskset`: the OPTIONAL reference source -----------------------------------------


def _taskset(path, *entries):
    path.write_text(json.dumps(list(entries)), encoding="utf-8")
    return str(path)


def _reference_spy(seen):
    def judge(plan_text, transcript_texts, reference=""):
        seen.append(reference)
        return JudgeVerdict(ok=True, score=EvalScore(TF=5, TA=5, TG=5, PA=5))
    return judge


def test_score_taskset_pairs_a_reference_onto_the_matching_run_id(tmp_path, transcript_file,
                                                                  monkeypatch):
    """The pairing is `Task.run_id == EvalTask.id`, and `collect_tasks` reads the trace ENVELOPE's
    run_id — so a `run`-produced `<id>-<stamp>.jsonl` filename pairs just as well as `<id>.jsonl`."""
    _recorded_trace(tmp_path / "good-run-20260728T000000Z.jsonl", "good-run")
    ts = _taskset(tmp_path / "ts.json", {"id": "good-run", "reference": "expected: promote X"})
    seen: list[str] = []
    monkeypatch.setattr("ctx_distillery_eval.cli._pick_judge",
                        lambda force_stub: (_reference_spy(seen), "judge-x", PROMPT_VERSION))
    assert main(["score", str(tmp_path / "*.jsonl"), str(transcript_file), "--taskset", ts]) == 0
    assert seen == ["expected: promote X"]


def test_score_without_a_taskset_passes_no_reference_at_all(tmp_path, transcript_file, monkeypatch):
    """`--taskset` is OPTIONAL and the two positionals did not move — the shipped `score` contract is
    unchanged, and the no-taskset path renders the byte-identical v1 prompt."""
    _recorded_trace(tmp_path / "good.jsonl", "good-run")
    seen: list[str] = []
    monkeypatch.setattr("ctx_distillery_eval.cli._pick_judge",
                        lambda force_stub: (_reference_spy(seen), "judge-x", PROMPT_VERSION))
    assert main(["score", str(tmp_path / "*.jsonl"), str(transcript_file)]) == 0
    assert seen == [""]


def test_score_taskset_leaves_an_undescribed_run_with_an_empty_reference(tmp_path, transcript_file,
                                                                        monkeypatch):
    """A run the taskset does not mention is scored WITHOUT a reference, never skipped and never
    refused: scoring traces a taskset does not describe is the normal case here, not an error."""
    _recorded_trace(tmp_path / "a.jsonl", "described")
    _recorded_trace(tmp_path / "b.jsonl", "not-described")
    ts = _taskset(tmp_path / "ts.json", {"id": "described", "reference": "R"})
    seen: list[str] = []
    monkeypatch.setattr("ctx_distillery_eval.cli._pick_judge",
                        lambda force_stub: (_reference_spy(seen), "judge-x", PROMPT_VERSION))
    assert main(["score", str(tmp_path / "*.jsonl"), str(transcript_file), "--taskset", ts]) == 0
    assert sorted(seen) == ["", "R"]


def test_score_refuses_a_malformed_taskset_the_operator_explicitly_passed(tmp_path, transcript_file):
    """Asymmetry with the case above, and it is deliberate: a run the taskset does not describe is
    normal, but a taskset FILE that cannot be read is a typo to fix, not a condition to degrade past."""
    _recorded_trace(tmp_path / "good.jsonl", "good-run")
    bad = _taskset(tmp_path / "ts.json", {"reference": "no id here"})
    with pytest.raises(ValueError):
        main(["score", str(tmp_path / "*.jsonl"), str(transcript_file), "--taskset", bad])


# -- `run`: the drive-then-score subcommand ----------------------------------------------------


class _FrozenClock:
    """Just enough of `datetime` for `_run_command`'s one `datetime.now(UTC).strftime(...)` call."""

    def __init__(self, stamp: str) -> None:
        self._stamp = stamp

    def now(self, _tz=None):
        return self

    def strftime(self, _fmt):
        return self._stamp


class _FakeArtifacts:
    """The `DistillArtifacts` fields `_run_command` reads. A real one needs dspy + a live model."""

    def __init__(self, trace_path, run_id):
        self.trace_path = str(trace_path)
        self.run_id = run_id
        self.events = [
            {"type": EVENT_RESULT,
             "payload": {"output": DistillPlan(
                 candidates=[DistillCandidate(action="keep", key_fields={"reason": "still true"})]
             ).model_dump()}}
        ]
        self.transcripts = ["a redacted transcript the run actually saw"]


def test_run_demo_materializes_drives_and_scores(tmp_path, monkeypatch, capsys):
    """The happy path end to end, with the DRIVE stubbed at `_drive` (a real one needs `CD_*`
    credentials and a sandbox, which CI has neither of). Everything else is real: the demo taskset is
    genuinely materialized under `--out`, the trace filename is derived, the rows are scored by the
    stub judge and the scorecard is rendered."""
    driven: list[tuple[str, str]] = []

    def fake_drive(task, trace_path):
        driven.append((task.id, str(trace_path)))
        return _FakeArtifacts(trace_path, task.id)

    monkeypatch.setattr("ctx_distillery_eval.cli._drive", fake_drive)
    assert main(["run", "demo", "--out", str(tmp_path), "--stub"]) == 0

    assert [task_id for task_id, _ in driven] == ["demo-durable-fact", "demo-one-off-debugging"]
    # the demo taskset really materialized, under --out and nowhere else
    assert (tmp_path / "demo" / "demo-durable-fact").is_dir()
    assert (tmp_path / "demo" / "claude-home" / "projects").is_dir()
    out = capsys.readouterr().out
    assert "demo-durable-fact" in out and "demo-one-off-debugging" in out
    assert "n=2 (0 unscored)" in out and "judge=stub" in out


def test_run_gives_each_invocation_a_unique_trace_filename_but_keeps_run_id(tmp_path, monkeypatch):
    """`TraceRecorder` APPENDS and `os.remove` is forbidden in this project, so a second `run` of the
    same taskset must not write into the first's file — the FILENAME carries a UTC stamp while the
    run_id stays `task.id`, which is what `score --taskset`'s pairing keys on."""
    seen: list[str] = []

    def fake_drive(task, trace_path):
        seen.append(Path(trace_path).name)
        return _FakeArtifacts(trace_path, task.id)

    monkeypatch.setattr("ctx_distillery_eval.cli._drive", fake_drive)
    monkeypatch.setattr("ctx_distillery_eval.cli.datetime", _FrozenClock("20260728T101500Z"))
    main(["run", "demo", "--out", str(tmp_path), "--stub"])
    monkeypatch.setattr("ctx_distillery_eval.cli.datetime", _FrozenClock("20260728T101600Z"))
    main(["run", "demo", "--out", str(tmp_path), "--stub"])

    assert seen == [
        "demo-durable-fact-20260728T101500Z.jsonl",
        "demo-one-off-debugging-20260728T101500Z.jsonl",
        "demo-durable-fact-20260728T101600Z.jsonl",
        "demo-one-off-debugging-20260728T101600Z.jsonl",
    ]
    assert len(set(seen)) == 4  # two invocations of two tasks, four distinct files


def test_slug_is_length_capped_so_a_task_id_cannot_overflow_a_filename():
    """A task id comes out of a hand-edited JSON file and becomes `<slug>-<stamp>.jsonl`, which is
    ONE filename component — capped at 255 BYTES on most filesystems. Without the cap an over-long
    id turned into an `OSError` (ENAMETOOLONG) inside `TraceRecorder` mid-batch rather than a task
    that simply runs. Same gap, same fix, and the same review as
    `ctx_distillery_studio.app._slug_id`'s — this is the WRITE side of it.
    """
    from ctx_distillery_eval.cli import _TASK_ID_MAX, _slug

    assert len(_slug("x" * 5000)) == _TASK_ID_MAX
    assert len(f"{_slug('y' * 5000)}-20260728T101500Z.jsonl") < 255
    # the truncation-lands-on-a-separator edge: the cut must not leave a trailing '-'/'.'
    for tail in ("-tail", ".tail"):
        edge = _slug("a" * (_TASK_ID_MAX - 1) + tail)
        assert len(edge) <= _TASK_ID_MAX and not edge.endswith(("-", "."))
    # unchanged behaviour for everything short, including the degenerate "" the caller falls back on
    assert _slug("demo-durable-fact") == "demo-durable-fact" and _slug("..") == ""


def test_run_refuses_to_append_into_an_existing_trace_file(tmp_path, monkeypatch, capsys):
    """The one case the per-invocation stamp does not cover: two runs in the same second, or two
    distinct task ids that slug to the same token. `TraceRecorder` appends and nothing here may
    delete, so it must REFUSE that one task rather than interleave two runs under two run ids into
    one file — after which `load_trace(path, run_id=...)` could no longer separate them."""
    monkeypatch.setattr("ctx_distillery_eval.cli.datetime", _FrozenClock("20260728T101500Z"))
    monkeypatch.setattr("ctx_distillery_eval.cli._drive",
                        lambda task, trace_path: _FakeArtifacts(trace_path, task.id))
    traces = tmp_path / "traces"
    traces.mkdir(parents=True)
    (traces / "demo-durable-fact-20260728T101500Z.jsonl").write_text("{}\n", encoding="utf-8")

    assert main(["run", "demo", "--out", str(tmp_path), "--stub"]) == 0
    out = capsys.readouterr().out
    assert "run skipped:" in out and "already exists" in out
    assert "n=2 (1 unscored)" in out  # the OTHER task still ran


def test_run_reports_a_failing_task_as_unscored_and_keeps_going(tmp_path, monkeypatch, capsys):
    """cve-reverser raises a bare `SystemExit` inside its run loop, so task 1 of 50 kills the other
    49. Here a failure is a ROW: the batch survives, the reason is printed, and the aggregate gate
    still refuses to call an all-unscored batch green."""
    def fake_drive(task, trace_path):
        if task.id == "demo-durable-fact":
            raise SystemExit("CD_ROOT_LM is not set")
        return _FakeArtifacts(trace_path, task.id)

    monkeypatch.setattr("ctx_distillery_eval.cli._drive", fake_drive)
    assert main(["run", "demo", "--out", str(tmp_path), "--stub"]) == 0
    out = capsys.readouterr().out
    assert "unscored: run failed: SystemExit: CD_ROOT_LM is not set" in out
    assert "demo-one-off-debugging" in out and "n=2 (1 unscored)" in out


def test_run_exits_non_zero_when_every_task_failed(tmp_path, monkeypatch, capsys):
    def boom(task, trace_path):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr("ctx_distillery_eval.cli._drive", boom)
    assert main(["run", "demo", "--out", str(tmp_path), "--stub"]) == 1
    assert "(no runs scored)" in capsys.readouterr().out


def test_run_refuses_a_task_with_no_project_loudly_but_per_row(tmp_path, capsys):
    """`_drive` is REAL here — the refusal under test is its own, before any model is reached.
    cve-reverser's stance (refuse loudly) over diff-sentry's (silently drive `{}`), but as a row."""
    ts = tmp_path / "ts.json"
    ts.write_text(json.dumps([{"id": "no-project", "reference": "r"}]), encoding="utf-8")
    assert main(["run", str(ts), "--out", str(tmp_path / "out"), "--stub"]) == 1
    out = capsys.readouterr().out
    assert "no-project" in out and "has no project.project_dir" in out


def test_run_reports_a_missing_taskset_file_without_a_traceback(tmp_path, capsys):
    assert main(["run", str(tmp_path / "nope.json"), "--out", str(tmp_path / "out")]) == 1
    assert "cannot load taskset" in capsys.readouterr().err


def test_run_writes_only_under_out(tmp_path, monkeypatch):
    """cve-reverser's `run` builds CWD-relative `sources/`/`traces/`/`responses/` paths despite its
    docstring promising everything lands under `--out`. This one must not: the working directory
    stays empty, and both the traces and the materialized demo taskset are inside `--out`."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    out = tmp_path / "out"
    monkeypatch.setattr("ctx_distillery_eval.cli._drive",
                        lambda task, trace_path: _FakeArtifacts(trace_path, task.id))
    monkeypatch.chdir(cwd)
    assert main(["run", "demo", "--out", str(out), "--stub"]) == 0
    assert list(cwd.iterdir()) == []
    assert (out / "traces").is_dir() and (out / "demo").is_dir()
