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
"""

from __future__ import annotations

import ast
from pathlib import Path

import ctx_distillery_studio

import ctx_distillery

STUDIO_PACKAGE_DIR = Path(ctx_distillery_studio.__file__).parent
ROOT_PACKAGE_DIR = Path(ctx_distillery.__file__).parent


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
