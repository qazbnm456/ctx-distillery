"""Configuration for ctx-distillery — the `CD_*` env surface `.env.example` documents.

Until the CLI existed, `.env.example` declared four `CD_*` variables that **no code read**: the
library's entry point (`session.run_distillation`) takes a caller-supplied `HarnessAdapter` and
`chat_fn` and never touches the environment, so the file described an intention rather than a
contract. This module is what makes it true. `studio/` still reports no model config for the same
reason it always did (its `/v1/config` says so explicitly): it replays a finished trace and drives
nothing. That statement is unchanged by this module's existence — but the *reason* the studio drives
nothing is, since `cli._cmd_distill` now assembles `run_distillation`'s whole precondition from the
`CD_*` surface below. The live surface it does NOT have is argued in `studio/README.md`'s "Scope:
replay-only, v1" (no cancel seam · no import-level `live`-extra valve · an unauthenticated
`project_dir` input), summarized in `CLAUDE.md` invariant 10. Don't re-derive it here.

Three refusals here are deliberate and LOUD (`SystemExit`), following this project's own precedent
in the `eval/` member's own CLI (`_read_transcripts`) — a required input that is present-but-useless
must fail like a missing one, not degrade quietly:

* **No `CD_ROOT_LM`** — a distillation with no planner model is not a degraded run, it is no run.
* **`CD_INTERPRETER` set to anything but `pyodide`** — `task._forced_config` would silently coerce
  it back (`CLAUDE.md` invariant 1 pins the sandbox IN CODE, which is the actual guarantee), so a
  refusal here changes no security property. It exists so a misconfiguration is SEEN: an operator
  who wrote `CD_INTERPRETER=local` believes something about this run that is not true, and silently
  doing the right thing teaches them the wrong thing.
* **A `claude-agent-sdk/` DRAFTER** — see `SUBSCRIPTION_PREFIX` and `from_env` below.

**The subscription path.** A role whose model reads `claude-agent-sdk/<id>` runs on the operator's
own Claude Pro/Max subscription through rlm-harness's `ClaudeAgentLM` rather than an OpenAI-compatible
proxy. That is squarely this project's audience — a harness that distills *Claude Code* sessions is
used by people who hold a Claude subscription, not necessarily a proxy key. It applies to the two
rlm-harness-built seats ONLY (planner, sub LM); `_maybe_subscription_lm` routes them, and the drafter is
structurally excluded, because `make_chat_fn` below builds an `openai.OpenAI` client directly and
would ship the sentinel to that endpoint as a bogus model id.

**Divergence from the siblings, in our favour.** `cve-reverser`/`diff-sentry`/`toolscout` all put
`_maybe_subscription_lm` in their dspy-BEARING task module, because their `config.py` must stay
import-clean. Ours already imports `rlm_harness` *inside* `setup()`'s body, so the router lives here,
with `from rlm_harness import ClaudeAgentLM` inside the sentinel branch — a branch only reachable from
`setup()`, which already needs dspy. The MODULE level stays dspy-free either way, and that is not a
stylistic claim: `tests/test_public_api.py` and `tests/test_subscription.py` both assert it.

No `dspy` import, and no `rlm_harness` import at module scope — `from_env()` is plain stdlib so it can
be exercised without paying for the model stack, matching the sibling projects' `config.py`
convention. `setup()` and `make_chat_fn()` import their dependencies lazily, at the point they are
actually about to talk to a model.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: The one sandbox this project ever runs in. Duplicated from `task.PINNED_INTERPRETER` rather than
#: imported, because importing `task` here would drag `dspy` into a stdlib-only config module — the
#: two are pinned to agree by `tests/test_cli.py::test_the_pinned_interpreter_constants_agree`.
PINNED_INTERPRETER = "pyodide"

#: The sentinel model-string prefix that routes a ROLE onto the operator's Claude Pro/Max
#: SUBSCRIPTION via rlm-harness's `ClaudeAgentLM` (see `_maybe_subscription_lm`). A naming convention,
#: so it lives in this dspy-free module beside the env surface that carries it.
SUBSCRIPTION_PREFIX = "claude-agent-sdk/"

#: Drafting-model call bounds. Constants, not env knobs: a memory/skill file is a short document,
#: and every extra `CD_*` variable is one more thing `.env.example` has to keep honest.
_DRAFT_MAX_TOKENS = 4096
_DRAFT_TIMEOUT = 60.0


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise SystemExit(f"{name}={raw!r} is not an integer") from None
    # Every `CD_*` integer on this surface is a BUDGET, and none of them is meaningful at zero or
    # below. Without this, `CD_PLANNER_MAX_TOKENS=-5` sailed through to `dspy.LM(max_tokens=-5)` —
    # found by review, and the same "accepted silently, dies far away" shape as the unpassed
    # `max_tokens` itself.
    if value < 1:
        raise SystemExit(f"{name}={raw!r} must be a positive integer (it is a budget)")
    return value


#: rlm-harness's own `KNOWN_ADAPTERS`, mirrored so a typo is refused HERE with the same clean `SystemExit`
#: every other `CD_*` mistake gets. Passing it through unvalidated meant `CD_ADAPTER=Json` died deep
#: inside `RLMConfig.__post_init__` as a raw traceback — the one variable on this surface that
#: behaved differently from all the others. Found by review.
_KNOWN_ADAPTERS = ("json", "chat", "default")


def _adapter_from_env() -> str:
    raw = (os.getenv("CD_ADAPTER") or "").strip()
    if not raw:
        return "json"
    if raw not in _KNOWN_ADAPTERS:
        raise SystemExit(
            f"CD_ADAPTER={raw!r} is not a known adapter; expected one of "
            f"{', '.join(_KNOWN_ADAPTERS)} (see .env.example for what each one does)"
        )
    return raw


@dataclass(frozen=True)
class DistillConfig:
    """The `CD_*` surface, resolved. Build with `from_env()`; construct directly in tests."""

    #: The RLM PLANNER (root LM) driving the REPL loop. Required for a live run.
    main_model: str = ""
    #: The sub LM rlm-harness hands to `llm_query`. Defaults to the planner — one model is a fine
    #: default for a task whose escalations are rare, and `CD_SUB_LM` splits them when they aren't.
    sub_model: str = ""
    api_key: str | None = None
    base_url: str | None = None

    #: The model behind `draft_memory_file` / `draft_skill_file` (rlm-harness's `make_model_tool`
    #: `chat_fn`). Its own endpoint/key may differ from the planner's, exactly as toolscout's
    #: `TS_RUBRIC_LM` may; unset falls back to the sub model, then to the planner.
    draft_model: str = ""
    draft_api_key: str | None = None
    draft_base_url: str | None = None

    #: Pinned. Kept as a field so the value that was actually configured is visible in the trace.
    interpreter: str = PINNED_INTERPRETER
    max_iterations: int = 30
    max_llm_calls: int = 10

    #: The planner's PER-CALL generation cap, and the one knob whose absence used to be able to kill
    #: a whole run with no way out. `RLMConfig.max_tokens` defaults to 8192; this project never
    #: passed it, and no `CD_*` variable could raise it. That is fine for an instruct model and a
    #: trap for a reasoning one: dspy reads `content` and DISCARDS `reasoning_content`, so the
    #: chain-of-thought is billed against the same cap it never appears in. Two deaths follow —
    #: reasoning exhausts the cap (empty `content`) or the answer is cut mid-JSON — and BOTH are
    #: terminal, because `max_retries=1` above deliberately refuses a whole-run retry. A sibling
    #: project hit exactly this on its first live turn (`AdapterParseError: Expected [reasoning,
    #: code], actual [code]`). 16384 is the recommended planner default; raise it with
    #: `CD_PLANNER_MAX_TOKENS`.
    planner_max_tokens: int = 16384

    #: The per-REPL-OUTPUT truncation cap rlm-harness hands `dspy.RLM`, and the LAST field of the same
    #: shape as `planner_max_tokens` — a full audit of `RLMConfig` found exactly these two. dspy
    #: head+tail-truncates every REPL output before it enters the planner's prompt, and THIS
    #: project's transcripts arrive as a REPL variable, so a bare `print(transcripts[0])` silently
    #: lost the middle of any transcript over rlm-harness's 10,000 default with no way to raise it.
    #: Measured over 25 real Claude Code transcripts: median 2,739 chars, max 32,920, 2 already past
    #: the default. Milder than the `max_tokens` trap — dspy leaves a visible "(N characters
    #: omitted)" marker and `read_transcript_chunk` is the deliberate paging escape hatch — but it is
    #: the same class, so it gets the same exit: `CD_MAX_OUTPUT_CHARS`.
    max_output_chars: int = 40000

    #: The structured-output adapter rlm-harness hands dspy (`json` / `chat` / `default`). `json` is
    #: rlm-harness's own default and the right one here: the decoder ENFORCES the schema, so a model
    #: that formats imperfectly still produces a valid plan. `chat` sends no `response_format` at
    #: all and needs the model to follow text field-markers by discipline alone — a dropped field
    #: has no recovery. Switch only for an endpoint with no structured-output support.
    adapter: str = "json"

    @classmethod
    def from_env(cls) -> DistillConfig:
        """Read `CD_*`. Raises `SystemExit` on the three conditions named in the module docstring."""
        main = (os.getenv("CD_ROOT_LM") or "").strip()
        if not main:
            raise SystemExit(
                "CD_ROOT_LM is not set — a distillation needs a planner model. Copy .env.example "
                "to .env, fill it in, and export it (`set -a; . ./.env; set +a`); nothing here "
                "auto-loads a .env file."
            )
        interpreter = (os.getenv("CD_INTERPRETER") or PINNED_INTERPRETER).strip()
        if interpreter != PINNED_INTERPRETER:
            raise SystemExit(
                f"CD_INTERPRETER={interpreter!r} is refused — ctx-distillery only ever runs in the "
                f"{PINNED_INTERPRETER!r} sandbox (CLAUDE.md invariant 1). The pin is enforced in "
                f"code regardless, so this run would have used {PINNED_INTERPRETER!r} anyway; "
                f"refusing rather than silently ignoring what you configured."
            )
        sub = (os.getenv("CD_SUB_LM") or "").strip() or main
        explicit_draft = (os.getenv("CD_DRAFT_LM") or "").strip()
        draft = explicit_draft or sub
        # The DRAFTER is a SEPARATE OpenAI-compatible client (`make_chat_fn` -> `openai.OpenAI` ->
        # rlm-harness's `make_model_tool`), NOT the subscription Agent SDK adapter — so its model may
        # never be a `claude-agent-sdk/…` sentinel. The gate is UNCONDITIONAL (diff-sentry's form,
        # not toolscout's conditional one) because BOTH drafting tools are ALWAYS wired in
        # `DistillSession.__init__`: there is no configuration in which the drafter goes unused, so
        # a sentinel here fails LATE — mid-trajectory, on the single hard-budget attempt
        # (`setup()` pins `max_retries=1`), after the planner has already spent iterations reading.
        # And our fallback chain makes it mandatory rather than defensive: `draft` falls back to
        # `sub`, which falls back to `main`, so setting ONLY `CD_ROOT_LM=claude-agent-sdk/…` — the
        # most natural way to try the subscription path — silently hands the sentinel to the
        # drafter's endpoint as a model id and 404s. Refuse here, naming which of the two it was.
        if draft.startswith(SUBSCRIPTION_PREFIX):
            inherited_from = "CD_SUB_LM" if os.getenv("CD_SUB_LM", "").strip() else "CD_ROOT_LM"
            raise SystemExit(
                f"The drafting model cannot run on a Claude Pro/Max subscription — "
                f"draft_memory_file / draft_skill_file go through a separate OpenAI-compatible "
                f"client (config.make_chat_fn), not the Agent SDK adapter, so the drafter's model "
                f"may not use the {SUBSCRIPTION_PREFIX!r} sentinel. "
                + (
                    f"CD_DRAFT_LM is unset, so it inherited the subscription {inherited_from}. "
                    if not explicit_draft
                    else "CD_DRAFT_LM is set to a subscription sentinel. "
                )
                + "Set CD_DRAFT_LM to the plain model id your drafting endpoint serves (and "
                "CD_DRAFT_BASE_URL / CD_DRAFT_API_KEY if it is a separate box). See the "
                "subscription block in .env.example."
            )
        return cls(
            main_model=main,
            sub_model=sub,
            api_key=(os.getenv("CD_API_KEY") or "").strip() or None,
            base_url=(os.getenv("CD_BASE_URL") or "").strip() or None,
            draft_model=draft,
            draft_api_key=(os.getenv("CD_DRAFT_API_KEY") or "").strip() or None,
            draft_base_url=(os.getenv("CD_DRAFT_BASE_URL") or "").strip() or None,
            interpreter=interpreter,
            max_iterations=_env_int("CD_MAX_ITERATIONS", 30),
            max_llm_calls=_env_int("CD_MAX_LLM_CALLS", 10),
            planner_max_tokens=_env_int("CD_PLANNER_MAX_TOKENS", 16384),
            max_output_chars=_env_int("CD_MAX_OUTPUT_CHARS", 40000),
            adapter=_adapter_from_env(),
        )


def _maybe_subscription_lm(model: str):
    """A `ClaudeAgentLM` when a role's model uses the `claude-agent-sdk/` sentinel, else `None`.

    `from rlm_harness import ClaudeAgentLM` is NOT dspy-free — rlm-harness's package `__getattr__` pulls the
    framework in on that attribute access — so the import sits inside the SENTINEL BRANCH only. It
    is reached from `setup()`, which already needs dspy; a proxy-only run never executes it, and
    this module's TOP stays stdlib-only (asserted, not assumed: `tests/test_public_api.py`).

    `claude-agent-sdk` is the opt-in `[subscription]` extra, and the kit defers THAT import to
    construction — so a missing SDK surfaces as an `ImportError` here, at build time, which we
    re-raise as a `ModuleNotFoundError` naming the actual fix. (`cve-reverser` omits this re-raise;
    `diff-sentry` and `toolscout` have it — the majority, and the actionable one.) The stripped
    remainder is the Claude model: prefer a full id (`claude-sonnet-5`) over an alias, which drifts.
    """
    if not model.startswith(SUBSCRIPTION_PREFIX):
        return None
    from rlm_harness import ClaudeAgentLM

    try:
        return ClaudeAgentLM(model[len(SUBSCRIPTION_PREFIX) :])
    except ImportError as exc:
        raise ModuleNotFoundError(
            f"A role's model is {model!r} (the {SUBSCRIPTION_PREFIX!r} subscription sentinel) but "
            "claude-agent-sdk is not installed in this environment — the extra is opt-in. Run "
            "`uv sync --extra subscription` (and keep the flag on any explicit `uv sync`; a plain "
            "`uv run` won't remove it), log the Claude Code CLI in, and unset ANTHROPIC_API_KEY. "
            "See the subscription block in .env.example."
        ) from exc


def setup(config: DistillConfig) -> DistillConfig:
    """Configure rlm-harness (planner + sub LM) for this process, and return `config` unchanged.

    The sibling projects' `setup(config)` shape. `DistillSession` reads the process-wide config
    through `rlm_harness.runtime.get_config()` when no `config=` is passed, so without this call a live
    run would silently inherit whatever `RLMConfig.from_env()`'s own `RLM_*` defaults produced
    rather than the `CD_*` values the operator set. `task._forced_config` still re-pins the
    interpreter afterwards — this does not replace that, it feeds it.

    A role whose model is `claude-agent-sdk/<id>` runs on the operator's Claude Pro/Max SUBSCRIPTION
    (rlm-harness's `ClaudeAgentLM`, injected through `configure`'s public `main_lm=` / `sub_lm=` seam);
    every other role is built from the `CD_*` proxy config exactly as before. MIXED auth is the
    supported shape, not an accident: the DRAFTER always stays on its own OpenAI-compatible endpoint
    and is never routed through the subscription (`from_env` refuses a sentinel there outright).
    """
    import rlm_harness
    from rlm_harness.config import RLMConfig

    # None -> configure builds a dspy.LM from the config below (the pre-existing proxy behaviour).
    main_lm = _maybe_subscription_lm(config.main_model)
    sub_lm = _maybe_subscription_lm(config.sub_model)
    rlm_harness.configure(
        RLMConfig(
            # Inert for a seat whose LM is injected below (configure builds from the config ONLY for
            # un-supplied seats), but still what LABELS the trace; on the proxy path it is the real
            # model built.
            main_model=config.main_model,
            sub_model=config.sub_model,
            api_key=config.api_key,
            base_url=config.base_url,
            interpreter=config.interpreter,
            max_iterations=config.max_iterations,
            max_llm_calls=config.max_llm_calls,
            # Both were previously UNPASSED, so rlm-harness's defaults applied with no way to reach
            # them from this project's env surface. `max_tokens` in particular is not a tuning
            # knob but a failure mode — see `DistillConfig.planner_max_tokens`.
            max_tokens=config.planner_max_tokens,
            max_output_chars=config.max_output_chars,
            adapter=config.adapter,
            # ONE attempt: max_iterations is a hard budget, never multiplied by a whole-run retry.
            max_retries=1,
        ),
        main_lm=main_lm,
        sub_lm=sub_lm,
    )
    return config


def make_chat_fn(config: DistillConfig) -> Callable[[str], Any]:
    """Build the `chat_fn` the three drafting tools call — an OpenAI-compatible completion closure.

    A CLI can own `run_distillation`'s preconditions end to end — an operator's shell already holds
    the credentials, and a foreground process is where a multi-minute sandboxed RLM episode belongs.
    That does NOT make the studio's abstention a precondition argument: that reasoning was retired
    (it was false once this module existed). `studio/README.md` §Scope holds the three reasons that
    actually survive — no cancel seam, no `live`-extra valve, and a project directory as the input.
    Don't re-derive it here. The closure shape (`Callable[[str], Any]`, `openai` imported inside it so
    an offline `show`/`apply` never pays for it) is `toolscout.cli._rubric_chat_fn`'s, verbatim in
    spirit — rlm-harness's `make_model_tool` normalises whatever it returns.
    """

    def _chat(spec: str) -> str:
        from openai import BadRequestError, OpenAI

        client = OpenAI(
            base_url=config.draft_base_url or config.base_url,
            api_key=config.draft_api_key or config.api_key or "EMPTY",
            max_retries=0,
            timeout=_DRAFT_TIMEOUT,
        )
        request: dict[str, Any] = {
            "model": config.draft_model or config.sub_model or config.main_model,
            "max_tokens": _DRAFT_MAX_TOKENS,
            "messages": [{"role": "user", "content": spec}],
        }
        try:
            reply = client.chat.completions.create(temperature=0.0, **request)
        except BadRequestError as exc:
            # `temperature=0` is the right ask for drafting a structured file, and it is also
            # REFUSED outright by the newer reasoning models, which accept only the default. Found
            # by a live run: every drafting call against a GPT-5-family drafter failed with
            # `Unsupported value: 'temperature' does not support 0 with this model. Only the default
            # (1) value is supported.`, so the whole role was unusable with the best model many
            # operators have. Retrying WITHOUT the parameter costs one extra request on exactly the
            # endpoints that would otherwise fail outright, and nothing on the ones that accept it.
            #
            # This is a PARAMETER-COMPATIBILITY fallback, not a transient retry — `max_retries=0`
            # above stays deliberate, because `make_model_tool` owns transient retries and doubling
            # the two turns a 60s timeout into minutes. A 400 that is not about `temperature`
            # re-raises untouched rather than being swallowed by a broad handler.
            if "temperature" not in str(exc).lower():
                raise
            reply = client.chat.completions.create(**request)
        return reply.choices[0].message.content or ""

    return _chat
