"""Boundary test: `ctx_distillery` (the rollout package) must NEVER import `ctx_distillery_eval`.

This package is a ONE-WAY reader of `ctx_distillery`'s public surface (`CLAUDE.md`'s
eval-member boundary), mirroring `diff-sentry/eval/tests/test_boundary.py` — which has THREE tests
where this file long had two. The two that were missing are the REAL gates, and both run in a FRESH
SUBPROCESS so no module some earlier test already imported can mask a violation:

1. `import ctx_distillery` must not drag this package in (the one-way fence, checked at RUNTIME
   rather than by reading source).
2. importing this package must pull `ctx_distillery` one way AND STAY LIGHT — no `dspy`, no
   `openai`. Scoring with the stub judge needs neither, and until parity pass 1 it got both:
   `score.py` reached `assemble` through `ctx_distillery.session`, which imports `ctx_distillery.task`,
   which does `from rlm_kit import RLMTask`. That is the whole point of `ctx_distillery.schema`
   existing, and a docstring cannot enforce it — this can.

The textual scan below is KEPT, exactly as diff-sentry keeps its own third test: it is belt and
braces over the source tree, not the main gate. It doesn't need either package's import machinery to
be wired up, so it can't be fooled by a lazy/deferred import — and its known cost is that any prose
in the ROOT package which merely NAMES this package turns it red (`studio/tests/test_boundary.py`
uses `ast` for that reason; this one stays textual to match its diff-sentry original).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import ctx_distillery

ROOT_PACKAGE_DIR = Path(ctx_distillery.__file__).parent


def _fresh(code: str) -> None:
    """Run `code` in a FRESH interpreter and require it to print `ok`.

    A same-process assertion would be worthless here: pytest has already imported half the workspace
    (this very module imports `ctx_distillery`), so `'dspy' in sys.modules` says nothing about who
    pulled it. A new process is the only honest measurement.
    """
    # `check=False` deliberately: a violation must surface as an ASSERTION carrying the child's
    # stderr (which names the offending import), not as a bare CalledProcessError.
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_import_ctx_distillery_does_not_import_the_eval_harness():
    """The rollout core must stay eval-free: importing `ctx_distillery` may not pull the harness."""
    _fresh(
        "import sys, ctx_distillery; "
        "assert 'ctx_distillery_eval' not in sys.modules; print('ok')"
    )


def test_import_eval_pulls_ctx_distillery_one_way_and_stays_light():
    """The harness reads `ctx_distillery`'s contract without dragging dspy or openai at import time.

    Deliberate divergence from diff-sentry, which imports its BARE eval package here: that works
    there because `diff_sentry_eval/__init__.py` eagerly re-exports its whole public surface, so the
    bare import already walks the real graph. `ctx_distillery_eval/__init__.py` is still just a
    docstring plus `__version__` (the eager-`__all__` public-surface pass has not happened), so a
    bare import would pull NOTHING and both assertions would pass vacuously — the `ctx_distillery`
    one would in fact FAIL, for the uninteresting reason that nothing was imported at all.

    So this imports `.cli`, the module the `ctx-distillery-eval` console script actually runs. That
    is the STRONGER check anyway: it covers the entire real import graph (`cli` -> `score` ->
    `ctx_distillery.render` / `.rubric` / `.schema`), not a curated re-export list.
    """
    _fresh(
        "import sys, ctx_distillery_eval.cli; "
        "assert 'ctx_distillery' in sys.modules; "
        "assert 'dspy' not in sys.modules; "
        "assert 'openai' not in sys.modules; print('ok')"
    )


def test_ctx_distillery_never_imports_ctx_distillery_eval():
    offenders = []
    for path in ROOT_PACKAGE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ctx_distillery_eval" in text:
            offenders.append(str(path))
    assert offenders == [], f"ctx_distillery must never import ctx_distillery_eval, found in: {offenders}"


def test_ctx_distillery_eval_can_import_ctx_distillery():
    """The direction that IS allowed — this package reads the root package's public surface."""
    import ctx_distillery.schema
    import ctx_distillery.session
    import ctx_distillery.task  # noqa: F401
