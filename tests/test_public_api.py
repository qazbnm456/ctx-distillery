"""The public surface — `import ctx_distillery` is dspy-free; `__all__` resolves; the writer is absent.

Supersedes the old `tests/test_import.py` (two bare smoke tests): "the package imports" and "the task
class is reachable" are both subsumed here, by `test_import_ctx_distillery_is_dspy_free` and
`test_lazy_dspy_bearing_names_are_deferred` respectively, and two files both claiming to test
importability is exactly the drift this consolidation removes.

The last two tests are this project's own, with no sibling analogue: `apply_plan` and friends are
deliberately NOT re-exported (CLAUDE.md invariant 8), and that exclusion needs a gate of its own —
`tests/test_no_write_capability.py` stops any *resolution path* into the writer, `test_all_names_resolve`
stops a name being listed in `__all__` without one, and these stop the surface claiming it either way.
"""

from __future__ import annotations

import subprocess
import sys

#: The four writer-side names `ctx_distillery.apply` owns and this package must never re-export.
WRITER_NAMES = ("apply_plan", "ApplyOutcome", "slugify", "ARCHIVE_DIRNAME")


def test_import_ctx_distillery_is_dspy_free():
    """`import ctx_distillery` must NOT pull dspy (the lazy-reexport invariant). Fresh process."""
    code = "import sys, ctx_distillery; assert 'dspy' not in sys.modules; print('ok')"
    # check=False deliberately: a non-zero exit is asserted below, WITH the child's stderr attached,
    # which is far more useful than CalledProcessError's bare exit code when the import regresses.
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_all_names_resolve():
    import ctx_distillery

    for name in ctx_distillery.__all__:
        assert getattr(ctx_distillery, name) is not None


def test_version_matches_pyproject():
    import pathlib
    import tomllib

    import ctx_distillery

    root = pathlib.Path(ctx_distillery.__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text())
    assert data["project"]["version"] == ctx_distillery.__version__


def test_lazy_dspy_bearing_names_are_deferred():
    """The dspy-bearing names live behind `__getattr__`; they resolve, but only import dspy on access."""
    import ctx_distillery

    for name in ("DistillSession", "run_distillation", "main"):
        assert callable(getattr(ctx_distillery, name))


def test_the_writer_is_not_on_the_public_surface():
    """`apply_plan` & co. are reachable only as `ctx_distillery.apply.<name>` (CLAUDE.md invariant 8).

    Note what is asserted and what is NOT. The four names above genuinely fall through `__getattr__`
    and raise, so `hasattr` is a real gate for them. The SUBMODULE name `apply` is a different story:
    `tests/test_apply.py` and `tests/test_apply_cli.py` both import `ctx_distillery.apply` at MODULE
    level, and importing a submodule binds it as an attribute of its parent package — so by the time
    any test in this file runs, pytest's collection phase has already made
    `hasattr(ctx_distillery, "apply")` True. That is a property of Python's import system, not a leak
    in the surface, and asserting against it here would be red on day one. The honest form of that
    claim runs in a fresh interpreter instead — see the next test.
    """
    import ctx_distillery

    for name in (*WRITER_NAMES, "apply"):
        assert name not in ctx_distillery.__all__
    for name in WRITER_NAMES:
        assert not hasattr(ctx_distillery, name), f"{name} must not be re-exported by the package"


def test_a_bare_import_does_not_pull_in_the_writer_module():
    """In a process that never imports it, `ctx_distillery.apply` is not even an attribute.

    The fresh-subprocess form of the claim the test above deliberately does not make: nothing in the
    eager import block (nor anything it transitively imports) reaches the human-gated writer, so the
    submodule is genuinely unloaded until someone asks for it by name.
    """
    code = (
        "import sys, ctx_distillery as cd; "
        "assert not hasattr(cd, 'apply'); "
        "assert 'ctx_distillery.apply' not in sys.modules; "
        "print('ok')"
    )
    # check=False deliberately: a non-zero exit is asserted below, WITH the child's stderr attached,
    # which is far more useful than CalledProcessError's bare exit code when the import regresses.
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
