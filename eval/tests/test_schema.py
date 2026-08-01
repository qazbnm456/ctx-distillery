"""`ctx_distillery_eval.schema` — `EvalScore` bounds, and `compute_means`'s reward-free contract."""

from __future__ import annotations

import pytest
from ctx_distillery_eval.schema import EVAL_CATEGORIES, EvalReport, EvalRow, EvalScore, compute_means
from pydantic import ValidationError


def test_eval_score_accepts_the_full_0_to_10_range():
    score = EvalScore(TF=0, TA=10, TG=5.5, PA=7)
    assert score.TF == 0 and score.TA == 10


@pytest.mark.parametrize("field", ["TF", "TA", "TG", "PA"])
@pytest.mark.parametrize("value", [-0.1, 10.1])
def test_eval_score_rejects_a_value_outside_0_to_10(field, value):
    kwargs = {"TF": 5, "TA": 5, "TG": 5, "PA": 5}
    kwargs[field] = value
    with pytest.raises(ValidationError):
        EvalScore(**kwargs)


def test_eval_score_notes_defaults_to_empty_string():
    assert EvalScore(TF=5, TA=5, TG=5, PA=5).notes == ""


def test_compute_means_is_empty_for_no_rows_not_a_dict_of_zeros():
    assert compute_means([]) == {}


# -- the unscored shape (parity pass 4) -----------------------------------------------------------


def test_eval_row_score_is_optional_so_a_failed_judge_has_somewhere_to_land():
    """It used to be REQUIRED, which is why a live judge could not exist: a failed judge's only
    representable outcomes were a real number and an exception."""
    row = EvalRow(run_id="r1", trace_path="a.jsonl", unscored_reason="judge endpoint error: boom")
    assert row.score is None and row.unscored is True


def test_eval_row_unscored_is_derived_from_the_score_not_stored_alongside_it():
    """Divergence from the siblings, pinned: they store BOTH an `unscored: bool` and an optional
    `score`, i.e. two representations of one fact that can disagree. Here `score is None` IS
    unscored, so `unscored` must be a property and must NOT appear as a field."""
    assert "unscored" not in EvalRow.model_fields
    scored = EvalRow(run_id="r1", trace_path="a.jsonl", score=EvalScore(TF=5, TA=5, TG=5, PA=5))
    assert scored.unscored is False
    assert "unscored" not in scored.model_dump()


def test_eval_row_refuses_an_unscored_row_with_no_reason():
    """A blank unscored row is the silent-mystery failure mode the optional score exists to prevent."""
    with pytest.raises(ValidationError):
        EvalRow(run_id="r1", trace_path="a.jsonl")
    with pytest.raises(ValidationError):
        EvalRow(run_id="r1", trace_path="a.jsonl", unscored_reason="   ")


def test_compute_means_skips_unscored_rows_entirely():
    rows = [
        EvalRow(run_id="r1", trace_path="a.jsonl", score=EvalScore(TF=8, TA=8, TG=8, PA=8)),
        EvalRow(run_id="r2", trace_path="b.jsonl", unscored_reason="judge output off-schema: nope"),
    ]
    # Not 4.0 — an unscored row is excluded from the DENOMINATOR too, or it would be a fake 0.
    assert compute_means(rows) == {"TF": 8.0, "TA": 8.0, "TG": 8.0, "PA": 8.0}


def test_compute_means_of_only_unscored_rows_is_empty():
    rows = [EvalRow(run_id="r1", trace_path="a.jsonl", unscored_reason="judge endpoint error: boom")]
    assert compute_means(rows) == {}


def test_compute_means_averages_each_category_independently():
    rows = [
        EvalRow(run_id="r1", trace_path="a.jsonl", score=EvalScore(TF=10, TA=0, TG=5, PA=5)),
        EvalRow(run_id="r2", trace_path="b.jsonl", score=EvalScore(TF=0, TA=10, TG=5, PA=5)),
    ]
    means = compute_means(rows)
    assert means == {"TF": 5.0, "TA": 5.0, "TG": 5.0, "PA": 5.0}
    assert set(means) == set(EVAL_CATEGORIES)


def test_compute_means_never_produces_a_composite_key():
    rows = [EvalRow(run_id="r1", trace_path="a.jsonl", score=EvalScore(TF=1, TA=2, TG=3, PA=4))]
    means = compute_means(rows)
    assert set(means) == set(EVAL_CATEGORIES)  # no extra "composite"/"total"/"score" key


def test_eval_report_defaults_to_empty():
    report = EvalReport()
    assert report.rows == [] and report.means == {}
    assert report.n == 0 and report.n_unscored == 0


def test_eval_report_carries_provenance_so_a_number_is_attributable():
    """`prompt_version` is the load-bearing one: two scorecards produced under different prompts are
    not comparable, and nothing else in the report would say so."""
    report = EvalReport(n=3, n_unscored=1, judge_model="judge-x", prompt_version="atlas-ctxd-eval-v1")
    assert (report.n, report.n_unscored) == (3, 1)
    assert report.judge_model == "judge-x" and report.prompt_version == "atlas-ctxd-eval-v1"


# -- per-row transcript composition (deliberately NOT on EvalReport) -----------------------------


def test_eval_row_transcript_composition_fields_default_to_none_not_zero():
    """Constructed with none of the three fields — the shape every pre-existing call site and test
    in this file already uses — they must default to `None`, never a fabricated `0`."""
    row = EvalRow(run_id="r1", trace_path="a.jsonl", score=EvalScore(TF=5, TA=5, TG=5, PA=5))
    assert row.n_transcripts is None
    assert row.transcript_sessions is None
    assert row.transcript_subagents is None


def test_eval_row_accepts_the_transcript_composition_fields():
    row = EvalRow(
        run_id="r1",
        trace_path="a.jsonl",
        score=EvalScore(TF=5, TA=5, TG=5, PA=5),
        n_transcripts=3,
        transcript_sessions=2,
        transcript_subagents=1,
    )
    assert (row.n_transcripts, row.transcript_sessions, row.transcript_subagents) == (3, 2, 1)


def test_eval_report_has_no_taskset_field_because_there_is_no_taskset_concept():
    """Every sibling's report names its taskset. Ours cannot: `taskset.py` enumerates runs from
    TRACES, not tasks, and building a real taskset is the deferred `run` work (see eval/README.md).
    Adding the field before the concept exists would label a report with an unfillable value."""
    assert "taskset" not in EvalReport.model_fields


def test_eval_report_has_no_transcript_composition_field_it_is_deliberately_per_row():
    """A `score` glob's rows each have their own, unrelated transcript composition — there is no
    single meaningful report-level number the way there is one `judge_model`/`prompt_version`."""
    assert "n_transcripts" not in EvalReport.model_fields
    assert "transcript_sessions" not in EvalReport.model_fields
    assert "transcript_subagents" not in EvalReport.model_fields
