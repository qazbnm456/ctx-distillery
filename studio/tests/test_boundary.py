"""Boundary test: `studio/` must NEVER reach the human-gated writer, and is never imported back.

`CLAUDE.md` invariant 10 requires that this member "NEVER calls `ctx_distillery.apply.apply_plan` —
applying a plan stays a separate, human-invoked action outside any web request, exactly as
invariant 8 already requires". Until now NOTHING asserted it: `eval/` has `eval/tests/test_boundary.py`
pinning its own one-way boundary, and the root package has
`tests/test_no_write_capability.py::test_apply_is_unreachable_from_the_planner_path` pinning the
RLM path's, but the studio — the one member reachable over HTTP — had no equivalent. This is it.

Mirrors `eval/tests/test_boundary.py`'s approach: a STATIC check over this package's own source
tree, so it needs neither package's import machinery to be wired up and cannot be fooled by a lazy
or function-local import. It parses with `ast` rather than scanning raw text, for one concrete
reason: `ctx_distillery_studio/__init__.py`'s docstring legitimately NAMES `apply_plan` in the very
sentence that promises never to call it, and a textual scan would flag the statement of the
invariant as a violation of it. `ast` sees code and never prose — the same distinction
`tests/test_no_write_capability.py` makes with its docstring-stripping pass.

Parity pass 1 added the other half this file was missing, mirroring
`diff-sentry/eval/tests/test_boundary.py`'s first two tests: a FRESH-SUBPROCESS pair pinning that
`import ctx_distillery` never drags this package in, and that importing this package's app module
pulls `ctx_distillery` one way while STAYING LIGHT — no `dspy`, no `openai`. The static scans below
read source; those two measure what actually lands in `sys.modules`, which is a different claim and
the one that regressed: until that pass, EVERY studio HTTP request had already paid for a dspy
import, because `app.py` reached `assemble` through `ctx_distillery.session` -> `ctx_distillery.task`
-> `from rlm_kit import RLMTask`. A replay server that never constructs an `RLMTask` should not
import an LM framework, and `ctx_distillery.schema` is what makes that true.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import ctx_distillery_studio

import ctx_distillery

STUDIO_PACKAGE_DIR = Path(ctx_distillery_studio.__file__).parent
ROOT_PACKAGE_DIR = Path(ctx_distillery.__file__).parent


def _fresh(code: str) -> None:
    """Run `code` in a FRESH interpreter and require it to print `ok`.

    A same-process assertion would be worthless: pytest has already imported half the workspace (this
    module imports both packages at the top), so `'dspy' in sys.modules` would say nothing about who
    pulled it. A new process is the only honest measurement.
    """
    # `check=False` deliberately: a violation must surface as an ASSERTION carrying the child's
    # stderr (which names the offending import), not as a bare CalledProcessError.
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_import_ctx_distillery_does_not_import_the_studio():
    """The rollout core must stay studio-free — the runtime counterpart of the source scan below."""
    _fresh(
        "import sys, ctx_distillery; "
        "assert 'ctx_distillery_studio' not in sys.modules; print('ok')"
    )


def test_import_studio_app_pulls_ctx_distillery_one_way_and_stays_light():
    """Importing the app reads `ctx_distillery`'s contract without dragging dspy or openai.

    Imports `.app` rather than the bare package, for the same reason `eval/tests/test_boundary.py`
    does: `ctx_distillery_studio/__init__.py` is a docstring plus `__version__`, so a bare import
    would pull nothing and assert nothing. `.app` is what `uvicorn` loads, so its import graph
    (`app` -> `mapper` + `ctx_distillery.rubric` / `.schema` / `.trace_io`) is the real one, and it
    is the graph every HTTP request pays for.
    """
    _fresh(
        "import sys, ctx_distillery_studio.app; "
        "assert 'ctx_distillery' in sys.modules; "
        "assert 'dspy' not in sys.modules; "
        "assert 'openai' not in sys.modules; print('ok')"
    )


def _names_apply_plan(node: ast.AST) -> bool:
    """`mod.apply_plan(...)` or a bare `apply_plan(...)` — the call, however the name was reached."""
    if isinstance(node, ast.Attribute):
        return node.attr == "apply_plan"
    return isinstance(node, ast.Name) and node.id == "apply_plan"


def _references_the_writer(tree: ast.AST) -> bool:
    """True if this module imports `…apply` or names `apply_plan` anywhere in real CODE."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "apply":
                return True
            if any(alias.name in ("apply", "apply_plan") for alias in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name.split(".")[-1] == "apply" for alias in node.names):
                return True
        elif _names_apply_plan(node):
            return True
    return False


def test_the_scan_actually_sees_the_studio_package():
    sources = sorted(STUDIO_PACKAGE_DIR.rglob("*.py"))
    assert sources, "no sources found — the scan would vacuously pass"
    assert {p.name for p in sources} >= {"__init__.py", "app.py", "mapper.py"}


def test_studio_never_reaches_ctx_distillery_apply():
    """The invariant-10 promise, finally pinned: no module in this package may import the writer or
    name `apply_plan` — not from an endpoint, not from a helper, not behind a lazy import."""
    offenders = [
        str(path)
        for path in sorted(STUDIO_PACKAGE_DIR.rglob("*.py"))
        if _references_the_writer(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], (
        "studio/ must never call ctx_distillery.apply.apply_plan (CLAUDE.md invariant 10); "
        f"found a reference in: {offenders}"
    )


def test_the_writer_scan_would_catch_a_real_importer():
    """Guard the guard: every plausible way to reach the writer must actually match, or the test
    above is theatre. Mirrors `tests/test_no_write_capability.py`'s own guard-the-guard pair."""
    for source in (
        "from ctx_distillery.apply import apply_plan",
        "from ctx_distillery import apply",
        "import ctx_distillery.apply",
        "def f():\n    from ctx_distillery.apply import apply_plan\n",
        "import ctx_distillery\nctx_distillery.apply.apply_plan(a, b, c)\n",
    ):
        assert _references_the_writer(ast.parse(source)), source


def test_the_writer_scan_does_not_fire_on_unrelated_code():
    for source in (
        "from ctx_distillery.session import assemble",
        "from ctx_distillery.trace_io import load_trace",
        "outcome = applied(candidate)\n",
        '"""This module never calls ctx_distillery.apply.apply_plan."""\n',
    ):
        assert not _references_the_writer(ast.parse(source)), source


def test_ctx_distillery_never_imports_ctx_distillery_studio():
    """The other direction, mirroring `eval/tests/test_boundary.py` verbatim: the root package is
    never a consumer of a workspace member built on top of it."""
    offenders = [
        str(path)
        for path in ROOT_PACKAGE_DIR.rglob("*.py")
        if "ctx_distillery_studio" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"ctx_distillery must never import ctx_distillery_studio, found in: {offenders}"


def test_this_suite_never_inherits_a_developers_private_redaction_rules():
    """Hermeticity, and it needs its own test because no fixture could provide it.

    `ctx_distillery.redact` resolves `CD_REDACTIONS` at MODULE IMPORT, which for this member happens
    during COLLECTION (`app.py` -> `ctx_distillery.schema`/`rubric`) — before any fixture runs. The
    guarantee therefore comes from `studio/tests/conftest.py` popping the variable at its own import
    time, and asserting the RESOLVED tier is the only way to state it. An adversarial review found
    this member running against a developer's private rule file while `CHANGELOG.md` claimed "the
    suite can never inherit" one; that was true of the root suite alone.
    """
    import os

    from ctx_distillery import redact

    assert "CD_REDACTIONS" not in os.environ
    assert redact._TIER3 == ()
