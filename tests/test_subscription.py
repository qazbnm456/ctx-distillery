"""The subscription path: the DRAFTER-hazard gate (`from_env`) + the sentinel router (`setup`).

Runs WITHOUT the `[subscription]` extra installed. The hazard tests touch only the dspy-free
`DistillConfig.from_env`; the router tests either exercise the NON-sentinel branch (which returns
before the lazy `from rlm_harness import ClaudeAgentLM` ever runs) or monkeypatch `rlm_harness.ClaudeAgentLM`
so the sentinel branch is deterministic without the SDK present.

One sharp edge worth naming, because it is easy to get wrong: `monkeypatch.setattr(rlm_harness,
"ClaudeAgentLM", ...)` performs a `getattr` FIRST to save the original, which trips rlm-harness's own
package `__getattr__` and pulls dspy into the process. That is acceptable HERE — a test process may
pay for dspy, and `tests/test_task.py` already does — but it must never leak into a module-level
import, which is why `config._maybe_subscription_lm` keeps that import inside the sentinel branch
and `tests/test_public_api.py` asserts the package top stays clean in a FRESH interpreter.
"""

from __future__ import annotations

import sys

import pytest

from ctx_distillery import config
from tests.test_cli import CD_VARS

SENTINEL = "claude-agent-sdk/claude-sonnet-5"
SENTINEL_SUB = "claude-agent-sdk/claude-fable-5"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No `CD_*` leaks in from the developer's own shell (the same scrub `tests/test_cli.py` does)."""
    for name in CD_VARS:
        monkeypatch.delenv(name, raising=False)


# -- the constant ------------------------------------------------------------------------------


def test_the_sentinel_prefix_is_the_documented_one():
    assert config.SUBSCRIPTION_PREFIX == "claude-agent-sdk/"


# -- from_env: the unconditional drafter refusal -----------------------------------------------


def test_from_env_refuses_a_drafter_inherited_from_the_planner(monkeypatch):
    """`CD_ROOT_LM` alone on the sentinel — the most natural way to try the subscription path.

    `draft_model` falls back to `sub_model`, which falls back to `main_model`, so this silently
    handed `claude-agent-sdk/...` to `make_chat_fn`'s OpenAI client as a model id. That 404s
    mid-trajectory, on the single hard-budget attempt — which is exactly why the gate is
    unconditional rather than a warning.
    """
    monkeypatch.setenv("CD_ROOT_LM", SENTINEL)
    with pytest.raises(SystemExit) as excinfo:
        config.DistillConfig.from_env()
    message = str(excinfo.value)
    assert "CD_DRAFT_LM is unset" in message
    assert "CD_ROOT_LM" in message
    assert "subscription" in message.lower()


def test_from_env_refuses_a_drafter_inherited_from_the_sub_lm(monkeypatch):
    """A real planner but a sentinel `CD_SUB_LM` — the error must name the SUB LM, not the root."""
    monkeypatch.setenv("CD_ROOT_LM", "planner")
    monkeypatch.setenv("CD_SUB_LM", SENTINEL_SUB)
    with pytest.raises(SystemExit) as excinfo:
        config.DistillConfig.from_env()
    message = str(excinfo.value)
    assert "CD_DRAFT_LM is unset" in message
    assert "CD_SUB_LM" in message


def test_from_env_refuses_an_explicitly_set_sentinel_drafter(monkeypatch):
    """Explicit `CD_DRAFT_LM` on the sentinel is refused too, and says so DIFFERENTLY.

    All three siblings distinguish explicitly-set from inherited, because the fix differs: an
    inherited one means "you never set CD_DRAFT_LM", an explicit one means "the value you chose is
    wrong". A single generic message would send half the operators looking in the wrong place.
    """
    monkeypatch.setenv("CD_ROOT_LM", "planner")
    monkeypatch.setenv("CD_DRAFT_LM", SENTINEL)
    with pytest.raises(SystemExit) as excinfo:
        config.DistillConfig.from_env()
    message = str(excinfo.value)
    assert "CD_DRAFT_LM is set to a subscription sentinel" in message
    assert "CD_DRAFT_LM is unset" not in message


def test_from_env_accepts_a_subscription_planner_with_a_real_drafter(monkeypatch):
    """The supported MIXED-auth shape: both rlm-harness seats on the subscription, drafter on a proxy."""
    monkeypatch.setenv("CD_ROOT_LM", SENTINEL)
    monkeypatch.setenv("CD_SUB_LM", SENTINEL_SUB)
    monkeypatch.setenv("CD_DRAFT_LM", "qwen/qwen3-next-80b")
    monkeypatch.setenv("CD_DRAFT_BASE_URL", "https://drafter.example/v1")
    cfg = config.DistillConfig.from_env()
    assert cfg.main_model == SENTINEL
    assert cfg.sub_model == SENTINEL_SUB
    assert cfg.draft_model == "qwen/qwen3-next-80b"
    assert cfg.draft_base_url == "https://drafter.example/v1"


def test_from_env_accepts_a_subscription_planner_with_a_real_sub_lm(monkeypatch):
    """Sentinel planner + a REAL `CD_SUB_LM`, `CD_DRAFT_LM` unset — the drafter inherits a real id."""
    monkeypatch.setenv("CD_ROOT_LM", SENTINEL)
    monkeypatch.setenv("CD_SUB_LM", "qwen/qwen3-next-80b")
    cfg = config.DistillConfig.from_env()
    assert cfg.main_model == SENTINEL
    assert cfg.draft_model == "qwen/qwen3-next-80b"


def test_from_env_proxy_path_is_unchanged(monkeypatch):
    """No sentinel anywhere -> the drafter still inherits the sub model, byte-identical to before."""
    monkeypatch.setenv("CD_ROOT_LM", "planner")
    monkeypatch.setenv("CD_SUB_LM", "specialist")
    assert config.DistillConfig.from_env().draft_model == "specialist"


# -- the router --------------------------------------------------------------------------------


def test_maybe_subscription_lm_returns_none_for_a_plain_model_id():
    """The non-sentinel branch returns BEFORE the lazy adapter import, so nothing is newly loaded.

    Asserted as "did not CHANGE" rather than "is not in sys.modules": another test in this session
    may legitimately have loaded the adapter already, and a bare absence check would then be
    order-dependent.
    """
    had_adapter = "rlm_harness.claude_agent_lm" in sys.modules
    assert config._maybe_subscription_lm("qwen/qwen3-next-80b") is None
    assert ("rlm_harness.claude_agent_lm" in sys.modules) == had_adapter


def test_maybe_subscription_lm_builds_the_adapter_for_a_sentinel(monkeypatch):
    """The sentinel branch strips the prefix and hands the bare model id to `ClaudeAgentLM`."""
    pytest.importorskip("dspy")  # patching the attribute pulls rlm-harness's dspy-bearing __getattr__
    import rlm_harness

    built: list[str] = []

    class _FakeClaudeAgentLM:
        def __init__(self, model):
            built.append(model)

    monkeypatch.setattr(rlm_harness, "ClaudeAgentLM", _FakeClaudeAgentLM)
    lm = config._maybe_subscription_lm(SENTINEL)
    assert isinstance(lm, _FakeClaudeAgentLM)
    assert built == ["claude-sonnet-5"]


def test_maybe_subscription_lm_missing_extra_is_actionable(monkeypatch):
    """Sentinel + no `claude-agent-sdk` -> an error NAMING the fix, not a bare ImportError.

    The real-world path this guards: `uv lock` records the extra, but only `uv sync --extra
    subscription` installs it — so a sentinel-configured run in a never-synced environment has to
    say which command it is missing.
    """
    pytest.importorskip("dspy")
    import rlm_harness

    def _raises(model):
        raise ImportError("No module named 'claude_agent_sdk'")

    monkeypatch.setattr(rlm_harness, "ClaudeAgentLM", _raises)
    with pytest.raises(ModuleNotFoundError) as excinfo:
        config._maybe_subscription_lm(SENTINEL)
    assert "uv sync --extra subscription" in str(excinfo.value)
    assert SENTINEL in str(excinfo.value)


# -- setup() wires both seats -------------------------------------------------------------------


def test_setup_injects_the_subscription_lm_for_both_rlm_harness_seats(monkeypatch):
    """`main_lm=` / `sub_lm=` carry the adapter; the drafter is not a seat `configure` knows about."""
    pytest.importorskip("dspy")
    import rlm_harness

    class _FakeClaudeAgentLM:
        def __init__(self, model):
            self.model = model

    captured: dict = {}

    def _fake_configure(cfg=None, *, main_lm=None, sub_lm=None):
        captured.update(config=cfg, main_lm=main_lm, sub_lm=sub_lm)
        return cfg

    monkeypatch.setattr(rlm_harness, "ClaudeAgentLM", _FakeClaudeAgentLM)
    monkeypatch.setattr(rlm_harness, "configure", _fake_configure)
    config.setup(
        config.DistillConfig(main_model=SENTINEL, sub_model=SENTINEL_SUB, draft_model="qwen/q3")
    )
    assert captured["main_lm"].model == "claude-sonnet-5"
    assert captured["sub_lm"].model == "claude-fable-5"
    # The model STRINGS still ride along: they are inert for an injected seat but label the trace.
    assert captured["config"].main_model == SENTINEL


def test_setup_injects_nothing_on_the_plain_proxy_path(monkeypatch):
    """No sentinel -> both seats stay None, so `configure` builds them from the `CD_*` config."""
    pytest.importorskip("dspy")
    import rlm_harness

    captured: dict = {}

    def _fake_configure(cfg=None, *, main_lm=None, sub_lm=None):
        captured.update(main_lm=main_lm, sub_lm=sub_lm)
        return cfg

    monkeypatch.setattr(rlm_harness, "configure", _fake_configure)
    config.setup(config.DistillConfig(main_model="planner", sub_model="specialist"))
    assert captured == {"main_lm": None, "sub_lm": None}


# -- the module-level dspy-freedom this whole design depends on ---------------------------------


def test_config_module_has_no_dspy_at_module_level():
    """A FRESH interpreter: `import ctx_distillery.config` must not pull dspy.

    The sentinel router lives in `config.py` (a divergence from all three siblings, which keep it in
    their dspy-bearing task module) purely BECAUSE the adapter import is inside the branch. If that
    import ever migrates to the module top, this goes red — which is the point.
    """
    import subprocess

    code = "import sys, ctx_distillery.config; assert 'dspy' not in sys.modules; print('ok')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


# --------------------------------------------------------------------------------------------------
# `make_chat_fn`'s temperature-compatibility fallback.
#
# Found by a LIVE run, not by review: every drafting call against a GPT-5-family drafter failed with
# `Unsupported value: 'temperature' does not support 0 with this model.`, so the whole drafting role
# was unusable with the best model many operators hold. Both directions are pinned — the fallback
# fires for a 400 about temperature, and a 400 about anything else still propagates rather than
# being swallowed by a broad handler.
# --------------------------------------------------------------------------------------------------


def _fake_openai(monkeypatch):
    """Install a fake `openai` module. Returns `(calls, BadRequestError, failures)`: append an
    exception to `failures` and the next `create` raises it instead of returning."""
    import sys
    import types

    calls: list[dict] = []
    failures: list[Exception] = []

    class BadRequestError(Exception):
        pass

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if failures:
                raise failures.pop(0)
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="drafted"))]
            )

    class OpenAI:
        def __init__(self, **_):
            self.chat = types.SimpleNamespace(completions=_Completions())

    module = types.ModuleType("openai")
    module.OpenAI = OpenAI
    module.BadRequestError = BadRequestError
    monkeypatch.setitem(sys.modules, "openai", module)
    return calls, BadRequestError, failures


def test_a_temperature_refusal_is_retried_without_the_parameter(monkeypatch) -> None:
    """The real message, verbatim from the live run that found this."""
    from ctx_distillery.config import DistillConfig, make_chat_fn

    calls, bad_request, failures = _fake_openai(monkeypatch)
    failures.append(bad_request(
        "Error code: 400 - Unsupported value: 'temperature' does not support 0 with this model. "
        "Only the default (1) value is supported."
    ))
    assert make_chat_fn(DistillConfig(main_model="m", draft_model="d"))("spec") == "drafted"

    assert len(calls) == 2, "the refusal should be retried exactly once"
    assert calls[0]["temperature"] == 0.0
    assert "temperature" not in calls[1], "the retry must OMIT the parameter, not pick another value"
    assert calls[1]["model"] == "d"
    assert calls[1]["messages"] == calls[0]["messages"]
    assert calls[1]["max_tokens"] == calls[0]["max_tokens"]


def test_a_400_about_anything_else_still_propagates(monkeypatch) -> None:
    from ctx_distillery.config import DistillConfig, make_chat_fn

    calls, bad_request, failures = _fake_openai(monkeypatch)
    failures.append(bad_request("Error code: 400 - model not found"))
    with pytest.raises(bad_request, match="model not found"):
        make_chat_fn(DistillConfig(main_model="m", draft_model="d"))("spec")
    assert len(calls) == 1, "a 400 that is not about temperature must not be retried"


def test_an_endpoint_that_accepts_temperature_pays_nothing(monkeypatch) -> None:
    """The fallback must cost one request, not two, on every endpoint that works today."""
    from ctx_distillery.config import DistillConfig, make_chat_fn

    calls, _, _ = _fake_openai(monkeypatch)
    assert make_chat_fn(DistillConfig(main_model="m", draft_model="d"))("spec") == "drafted"
    assert len(calls) == 1 and calls[0]["temperature"] == 0.0
