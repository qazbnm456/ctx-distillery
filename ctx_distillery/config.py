"""Configuration for ctx-distillery — the `CD_*` env surface `.env.example` documents.

Until the CLI existed, `.env.example` declared four `CD_*` variables that **no code read**: the
library's entry point (`session.run_distillation`) takes a caller-supplied `HarnessAdapter` and
`chat_fn` and never touches the environment, so the file described an intention rather than a
contract. This module is what makes it true. `studio/` still reports no model config for the same
reason it always did (its `/v1/config` says so explicitly): it replays a finished trace and drives
nothing.

Two refusals here are deliberate and LOUD (`SystemExit`), following this project's own precedent in
the `eval/` member's own CLI (`_read_transcripts`) — a required input that is present-but-useless
must fail like a missing one, not degrade quietly:

* **No `CD_ROOT_LM`** — a distillation with no planner model is not a degraded run, it is no run.
* **`CD_INTERPRETER` set to anything but `pyodide`** — `task._forced_config` would silently coerce
  it back (`CLAUDE.md` invariant 1 pins the sandbox IN CODE, which is the actual guarantee), so a
  refusal here changes no security property. It exists so a misconfiguration is SEEN: an operator
  who wrote `CD_INTERPRETER=local` believes something about this run that is not true, and silently
  doing the right thing teaches them the wrong thing.

No `dspy` import, and no `rlm_kit` import at module scope — `from_env()` is plain stdlib so it can
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

#: Drafting-model call bounds. Constants, not env knobs: a memory/skill file is a short document,
#: and every extra `CD_*` variable is one more thing `.env.example` has to keep honest.
_DRAFT_MAX_TOKENS = 4096
_DRAFT_TIMEOUT = 60.0


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise SystemExit(f"{name}={raw!r} is not an integer") from None


@dataclass(frozen=True)
class DistillConfig:
    """The `CD_*` surface, resolved. Build with `from_env()`; construct directly in tests."""

    #: The RLM PLANNER (root LM) driving the REPL loop. Required for a live run.
    main_model: str = ""
    #: The sub LM rlm-kit hands to `llm_query`. Defaults to the planner — one model is a fine
    #: default for a task whose escalations are rare, and `CD_SUB_LM` splits them when they aren't.
    sub_model: str = ""
    api_key: str | None = None
    base_url: str | None = None

    #: The model behind `draft_memory_file` / `draft_skill_file` (rlm-kit's `make_model_tool`
    #: `chat_fn`). Its own endpoint/key may differ from the planner's, exactly as toolscout's
    #: `TS_RUBRIC_LM` may; unset falls back to the sub model, then to the planner.
    draft_model: str = ""
    draft_api_key: str | None = None
    draft_base_url: str | None = None

    #: Pinned. Kept as a field so the value that was actually configured is visible in the trace.
    interpreter: str = PINNED_INTERPRETER
    max_iterations: int = 30
    max_llm_calls: int = 10

    @classmethod
    def from_env(cls) -> DistillConfig:
        """Read `CD_*`. Raises `SystemExit` on the two conditions named in the module docstring."""
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
        return cls(
            main_model=main,
            sub_model=sub,
            api_key=(os.getenv("CD_API_KEY") or "").strip() or None,
            base_url=(os.getenv("CD_BASE_URL") or "").strip() or None,
            draft_model=(os.getenv("CD_DRAFT_LM") or "").strip() or sub,
            draft_api_key=(os.getenv("CD_DRAFT_API_KEY") or "").strip() or None,
            draft_base_url=(os.getenv("CD_DRAFT_BASE_URL") or "").strip() or None,
            interpreter=interpreter,
            max_iterations=_env_int("CD_MAX_ITERATIONS", 30),
            max_llm_calls=_env_int("CD_MAX_LLM_CALLS", 10),
        )


def setup(config: DistillConfig) -> DistillConfig:
    """Configure rlm-kit (planner + sub LM) for this process, and return `config` unchanged.

    The sibling projects' `setup(config)` shape. `DistillSession` reads the process-wide config
    through `rlm_kit.runtime.get_config()` when no `config=` is passed, so without this call a live
    run would silently inherit whatever `RLMConfig.from_env()`'s own `RLM_*` defaults produced
    rather than the `CD_*` values the operator set. `task._forced_config` still re-pins the
    interpreter afterwards — this does not replace that, it feeds it.
    """
    import rlm_kit
    from rlm_kit.config import RLMConfig

    rlm_kit.configure(
        RLMConfig(
            main_model=config.main_model,
            sub_model=config.sub_model,
            api_key=config.api_key,
            base_url=config.base_url,
            interpreter=config.interpreter,
            max_iterations=config.max_iterations,
            max_llm_calls=config.max_llm_calls,
            # ONE attempt: max_iterations is a hard budget, never multiplied by a whole-run retry.
            max_retries=1,
        )
    )
    return config


def make_chat_fn(config: DistillConfig) -> Callable[[str], Any]:
    """Build the `chat_fn` the two drafting tools call — an OpenAI-compatible completion closure.

    This is the piece that answers "can a CLI own `run_distillation`'s preconditions end to end?"
    with yes, where `CLAUDE.md` invariant 10 answers it with no for a web request: an operator's
    shell already holds the credentials, and a foreground process is where a multi-minute sandboxed
    RLM episode belongs. The closure shape (`Callable[[str], Any]`, `openai` imported inside it so
    an offline `show`/`apply` never pays for it) is `toolscout.cli._rubric_chat_fn`'s, verbatim in
    spirit — rlm-kit's `make_model_tool` normalises whatever it returns.
    """

    def _chat(spec: str) -> str:
        from openai import OpenAI

        client = OpenAI(
            base_url=config.draft_base_url or config.base_url,
            api_key=config.draft_api_key or config.api_key or "EMPTY",
            max_retries=0,
            timeout=_DRAFT_TIMEOUT,
        )
        reply = client.chat.completions.create(
            model=config.draft_model or config.sub_model or config.main_model,
            temperature=0.0,
            max_tokens=_DRAFT_MAX_TOKENS,
            messages=[{"role": "user", "content": spec}],
        )
        return reply.choices[0].message.content or ""

    return _chat
