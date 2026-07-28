"""`ctx_distillery_eval.cli` — the "mandatory transcript" content check, and batch survival.

Added per adversarial review, which found `cli.py`/`taskset.py` had NO test coverage at all before
this — exactly the surface the "transcript is mandatory" design decision lives in, and exactly
where two real gaps went unnoticed (the CI job never running this suite at all, and an empty
transcript file slipping through silently). This file still does not attempt full CLI/argparse
coverage — that remains a stated gap — it targets the concrete bugs actually found: the empty
transcript, and (below) a malformed trace line taking down an entire scoring batch.
"""

from __future__ import annotations

import pytest
from ctx_distillery_eval.cli import _pick_judge, _read_transcripts, main, render_scorecard
from ctx_distillery_eval.judge import PROMPT_VERSION, JudgeVerdict, StubJudge
from ctx_distillery_eval.schema import EvalReport, EvalRow, EvalScore
from rlm_kit.trace import TraceRecorder, record_tool_call

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

#: Valid JSON, but not an object — the exact shapes `rlm_kit.trace.load_events` passes through.
NON_DICT_LINES = ("42", "null", '"x"', "[1, 2, 3]")


def _recorded_trace(path, run_id):
    """A REAL `TraceRecorder` trace, not hand-written JSON: this test turns on the `run_id`
    ENVELOPE key (which `collect_tasks` reads) and on `record_result`'s real payload shape, so
    hand-rolling either would assert against a guess instead of what rlm-kit actually writes."""
    plan = DistillPlan(candidates=[DistillCandidate(action="promote_to_memory", artifact_id="a1")])
    with TraceRecorder(str(path), run_id=run_id, meta={"transcripts": 1}) as rec:
        record_tool_call("draft_memory_file", ok=True, artifact_id="a1", draft=_DRAFT, errors=[])
        rec.record_result(plan)
    return path


def test_score_command_survives_a_non_dict_line_in_one_trace_of_a_batch(tmp_path, capsys):
    """THE regression test for this fix. `rlm_kit.trace.load_events` does no shape validation, so a
    JSONL line that is valid JSON but not an object reached three separate unguarded `.get(...)`
    calls on this exact path: `taskset.collect_tasks`'s `e.get("run_id")`, then `load_events`'s OWN
    `run_id` filter (inside rlm-kit, upstream of anything `ctx_distillery` could harden), then
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


def test_render_scorecard_footer_states_the_denominator_and_the_provenance():
    report = EvalReport(n=3, n_unscored=1, judge_model="judge-x", prompt_version=PROMPT_VERSION)
    footer = render_scorecard(report).splitlines()[-1]
    assert "n=3 (1 unscored)" in footer
    assert "judge=judge-x" in footer
    assert f"prompt={PROMPT_VERSION}" in footer


def test_score_command_exits_non_zero_when_the_judge_scored_nothing(tmp_path, transcript_file,
                                                                   monkeypatch, capsys):
    """The CI gate: an all-unscored scorecard is not a green run. Without this, a batch scored by a
    dead judge prints a table of `--` and exits 0, which reads as a pass."""
    _recorded_trace(tmp_path / "good.jsonl", "good-run")

    def dead_judge(plan_text, transcript_texts):
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
