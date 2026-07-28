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

**The taskset pass widened this to `CD_*` as well, for exactly the same reason one step further
out.** `cli.run` drives a REAL distillation (`ctx_distillery.session.run_distillation_artifacts`
behind `cli._drive`), whose whole precondition comes from the root package's `CD_*` env surface. On a
developer's machine with `CD_ROOT_LM` exported — again, the machine most likely to be running this
suite — a `run` test would have started a live, billed, multi-minute sandboxed RLM episode instead of
landing on `DistillConfig.from_env`'s refusal. Scrubbing `CD_*` makes "offline" enforced for the run
path too, not just the judge path.
"""

from __future__ import annotations

import pytest

#: The complete `CDEVAL_*` surface `judge.EvalJudgeConfig.from_env` reads. Keep this in sync with it
#: — a variable missing here is a variable that can leak a live judge into the suite.
CDEVAL_VARS = ("CDEVAL_MODEL", "CDEVAL_BASE_URL", "CDEVAL_API_KEY", "CDEVAL_TIMEOUT")

#: The root package's `CD_*` surface (`ctx_distillery.config.DistillConfig.from_env`). Keep in sync
#: with it — a variable missing here is one that can leak a live DISTILLATION into the suite.
CD_VARS = (
    "CD_ROOT_LM", "CD_SUB_LM", "CD_DRAFT_LM", "CD_API_KEY", "CD_BASE_URL",
    "CD_DRAFT_API_KEY", "CD_DRAFT_BASE_URL", "CD_INTERPRETER",
    "CD_MAX_ITERATIONS", "CD_MAX_LLM_CALLS", "CD_PLANNER_MAX_TOKENS", "CD_ADAPTER",
    "CD_MAX_OUTPUT_CHARS",
)


def _cd_vars_actually_read() -> set[str]:
    """Every `CD_*` name `ctx_distillery.config` really reads, scraped from its source.

    The "keep in sync" comment above is not enough on its own and this project has the receipts: the
    tuple was already out of date the day it was written, missing `CD_PLANNER_MAX_TOKENS` and
    `CD_ADAPTER` — both added in the SAME batch that added the comment. A hand-maintained mirror of
    another module's surface rots by default; `test_the_scrub_list_covers_every_CD_var` below turns
    that rot into a failing test instead of a silent hole in an offline guarantee.

    Source-scraped rather than imported because importing `ctx_distillery.config` here is fine but
    reading its RESOLVED values is not — `from_env` is exactly what we are protecting the suite from
    calling with a live key present.
    """
    import re
    from pathlib import Path

    import ctx_distillery.config as config_module

    source = Path(config_module.__file__).read_text(encoding="utf-8")
    return set(re.findall(r'["\'](CD_[A-Z_]+)["\']', source))


@pytest.fixture(autouse=True)
def _offline_judge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub `CDEVAL_*` and `CD_*` so no test can reach a real endpoint. See this module's docstring."""
    for name in (*CDEVAL_VARS, *CD_VARS):
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
