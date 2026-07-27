"""Boundary test: `ctx_distillery` (the rollout package) must NEVER import `ctx_distillery_eval`.

This package is a ONE-WAY reader of `ctx_distillery`'s public surface (`docs/DESIGN.md`'s
eval-member boundary), mirroring `diff-sentry/eval/tests/test_boundary.py`. This test scans
`ctx_distillery`'s own source tree for any reference to `ctx_distillery_eval` — a static, textual
check that doesn't need either package's import machinery to be fully wired up, so it can't be
fooled by a lazy/deferred import either.
"""

from __future__ import annotations

from pathlib import Path

import ctx_distillery

ROOT_PACKAGE_DIR = Path(ctx_distillery.__file__).parent


def test_ctx_distillery_never_imports_ctx_distillery_eval():
    offenders = []
    for path in ROOT_PACKAGE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ctx_distillery_eval" in text:
            offenders.append(str(path))
    assert offenders == [], f"ctx_distillery must never import ctx_distillery_eval, found in: {offenders}"


def test_ctx_distillery_eval_can_import_ctx_distillery():
    """The direction that IS allowed — this package reads the root package's public surface."""
    import ctx_distillery.session
    import ctx_distillery.task  # noqa: F401
