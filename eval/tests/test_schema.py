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
