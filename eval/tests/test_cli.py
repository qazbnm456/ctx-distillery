"""`ctx_distillery_eval.cli._read_transcripts` — the "mandatory transcript" content check.

Added per adversarial review, which found `cli.py`/`taskset.py` had NO test coverage at all before
this — exactly the surface the "transcript is mandatory" design decision lives in, and exactly
where two real gaps went unnoticed (the CI job never running this suite at all, and an empty
transcript file slipping through silently). This file does not attempt full CLI/argparse coverage
in one pass — that remains a stated gap — it targets the ONE concrete bug just fixed.
"""

from __future__ import annotations

import pytest
from ctx_distillery_eval.cli import _read_transcripts


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
