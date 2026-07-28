"""Shared fixtures for the eval-harness suite — OFFLINE BY CONSTRUCTION, on purpose.

Every sibling eval member (`toolscout`, `cve-reverser`, `diff-sentry`) carries a `conftest.py`; this
suite had only an empty `tests/__init__.py`. Symmetry is not why it exists now. Parity pass 4 added a
LIVE judge selected purely from the environment — `cli._pick_judge` goes live the moment
`CDEVAL_MODEL` is set — and `tests/test_cli.py` drives `cli.main(["score", ...])` end to end. On a
developer's machine with `CDEVAL_*` exported (the exact machine most likely to be running this
suite), those tests would have quietly started calling a real endpoint: real money, real latency, and
a result that depends on a model's mood. `_offline_judge_env` deletes the whole `CDEVAL_*` surface
for EVERY test in this package, so "offline" is enforced rather than assumed.

It is `autouse` deliberately: an opt-in fixture protects only the tests someone remembered to
decorate, and the failure mode of forgetting is a live billed call, not a red test. Tests that WANT
those variables set (`test_judge.py::test_config_reads_the_cdeval_env`) still work — they use the
same function-scoped `monkeypatch`, whose `setenv` runs in the test body, i.e. AFTER this fixture's
setup, so the delete-then-set ordering is exactly right.
"""

from __future__ import annotations

import pytest

#: The complete `CDEVAL_*` surface `judge.EvalJudgeConfig.from_env` reads. Keep this in sync with it
#: — a variable missing here is a variable that can leak a live judge into the suite.
CDEVAL_VARS = ("CDEVAL_MODEL", "CDEVAL_BASE_URL", "CDEVAL_API_KEY", "CDEVAL_TIMEOUT")


@pytest.fixture(autouse=True)
def _offline_judge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub `CDEVAL_*` so no test can reach a real judge endpoint. See this module's docstring."""
    for name in CDEVAL_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def transcript_file(tmp_path):
    """A non-empty transcript file — the MANDATORY second input every `score` invocation needs.

    Non-empty matters: `cli._read_transcripts` refuses an empty or whitespace-only transcript with a
    `SystemExit`, because a real judge would otherwise be asked to score a plan against nothing.
    """
    path = tmp_path / "transcript.txt"
    path.write_text("a real transcript excerpt", encoding="utf-8")
    return path
