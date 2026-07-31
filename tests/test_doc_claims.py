"""Pin the CHECKABLE claims the docs make about the code, so drift fails a test instead of a review.

Docs-vs-code drift is this repo's recurring failure mode, not a hypothetical one. `CLAUDE.md` carries
several in-line "this bullet used to say X, which was FALSE" corrections, invariant 6 records that a
stale CONFIRMED/UNCONFIRMED label "has now caught this invariant itself twice", and a two-week window
of history holds four commits whose whole subject is repairing a doc claim. Every one of those was
caught by a human reading carefully. That works until someone doesn't.

Most of what `CLAUDE.md` says is prose about WHY, and prose is not testable — that is fine and is not
what this module is for. But a subset of the claims are numbers, names, or cross-file agreements, and
those are mechanically checkable. This file is the home for that subset: **when you write a doc claim
that a regex could verify, add it here.** The existing precedent is `test_redact_golden.py`, which
already pins `len(_TIER1) == 7` / `len(_TIER2) == 120` for exactly this reason.

Everything below reads source files as TEXT rather than importing them. Two reasons, both deliberate:
the workspace members (`eval/`, `studio/`) are not importable from the root suite in a plain-pip
install, and a textual scan is the idiom this repo already uses for cross-cutting guards
(`test_no_write_capability.py`, `studio/tests/static-contract.test.js`).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    path = ROOT / relative
    assert path.exists(), f"{relative} is missing — this test's premise moved"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------------------
# Claim 1: all four sluggers in the workspace share ONE cap, and CLAUDE.md states that number.
#
# CLAUDE.md invariant 8: the slug is "CAPPED at `_SLUG_MAX` (120, the same number every other slugger
# in the workspace uses — `cli._slug`, `studio`'s `app._RUN_ID_MAX`, `eval`'s `cli._TASK_ID_MAX`". All
# three source files repeat that cross-reference in their own comments. Nothing checked it: each
# member's suite pins only its OWN cap (`eval/tests/test_cli.py`, `studio/tests/test_app.py`), so any
# one of the four could move and leave the docs plus three comments quietly wrong.
#
# The number matters because the input is untrusted MODEL output and a slug becomes ONE path
# component — over ~255 bytes it is an OSError from the first stat that touches it.
# --------------------------------------------------------------------------------------------------

SLUGGERS: dict[str, str] = {
    "ctx_distillery/apply.py": "_SLUG_MAX",
    "ctx_distillery/cli.py": "_RUN_ID_MAX",
    "studio/ctx_distillery_studio/app.py": "_RUN_ID_MAX",
    "eval/ctx_distillery_eval/cli.py": "_TASK_ID_MAX",
}


def _constant(source: str, name: str) -> int | None:
    match = re.search(rf"^{re.escape(name)}\s*=\s*(\d+)\s*$", source, re.MULTILINE)
    return int(match.group(1)) if match else None


@pytest.mark.parametrize(("relative", "name"), sorted(SLUGGERS.items()))
def test_every_slugger_declares_its_cap(relative: str, name: str) -> None:
    """A cap that vanished (or got inlined) makes the equality check below vacuous."""
    assert _constant(_read(relative), name) is not None, (
        f"{relative} no longer declares `{name} = <int>` at module level. If the cap moved, update "
        f"SLUGGERS and CLAUDE.md invariant 8's cross-reference together — do not delete the check."
    )


def test_all_four_sluggers_share_one_cap() -> None:
    found = {rel: _constant(_read(rel), name) for rel, name in SLUGGERS.items()}
    distinct = set(found.values())
    assert len(distinct) == 1, (
        "CLAUDE.md invariant 8 says all four sluggers use the same cap, and three source comments "
        f"repeat it. They disagree: {found}"
    )


def test_claude_md_states_the_cap_the_code_uses() -> None:
    """The doc's own number, not just internal agreement — a synchronized change to all four sources
    that left the prose behind is the exact drift this module exists to catch."""
    stated = re.search(r"`_SLUG_MAX`\s*\((\d+)", _read("CLAUDE.md"))
    assert stated is not None, "CLAUDE.md invariant 8 no longer states `_SLUG_MAX` (<n>"
    assert int(stated.group(1)) == _constant(_read("ctx_distillery/apply.py"), "_SLUG_MAX")


# --------------------------------------------------------------------------------------------------
# Claim 2: the ruff pin is ONE version, spelled identically everywhere it appears.
#
# CLAUDE.md ## Verify and ci.yml's lint job both argue at length that an unpinned `uvx ruff check .`
# resolves the latest ruff at run time and reddens CI with nobody having touched a line of code (ruff
# 0.16's rule-set expansion did exactly that to two sibling projects). The `Makefile` is now a THIRD
# copy of that pin. Three copies of a version string is three chances to bump two of them.
# --------------------------------------------------------------------------------------------------

RUFF_PIN_FILES = ("Makefile", ".github/workflows/ci.yml", "CLAUDE.md")


def test_the_ruff_pin_is_identical_in_every_place_that_carries_it() -> None:
    found = {rel: set(re.findall(r"ruff@(\d+\.\d+\.\d+)", _read(rel))) for rel in RUFF_PIN_FILES}
    for relative, versions in found.items():
        assert versions, f"{relative} no longer pins a ruff version (`ruff@X.Y.Z`) — pin it or drop it"
    distinct = set().union(*found.values())
    assert len(distinct) == 1, (
        "the ruff pin must be bumped everywhere in one commit, together with the fixes it produces. "
        f"Found: {found}"
    )


# --------------------------------------------------------------------------------------------------
# Claim 3: `--directory` survives in both runners for the two workspace members.
#
# ci.yml's own comment records this as a real bug that shipped: `--package` alone selects which
# member's ENVIRONMENT to use but not which pyproject.toml's `testpaths` resolves, so the eval job
# silently re-ran the ROOT suite three times and never executed a single test under `eval/tests/` —
# including the boundary test that gates the whole member. The Makefile now carries the same
# invocation, so it can regress the same way, independently.
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("member", ["eval", "studio"])
@pytest.mark.parametrize("runner", ["Makefile", ".github/workflows/ci.yml"])
def test_workspace_member_suites_are_run_with_directory(runner: str, member: str) -> None:
    assert f"--directory {member}" in _read(runner), (
        f"{runner} runs the {member} member without `--directory {member}`. `--package` alone does "
        f"NOT resolve that member's own testpaths — this exact omission once made CI green while "
        f"running zero of {member}/tests/."
    )


# --------------------------------------------------------------------------------------------------
# Claim 4: the CLI is TWO console scripts, and they are the two invariant 8 names.
#
# CLAUDE.md invariant 8: "This is why the CLI is TWO console scripts, not one binary with three
# subcommands" — a shared `cli.py` offering both `distill` and `apply` would turn
# `test_no_write_capability.py::test_apply_is_unreachable_from_the_planner_path` red. That test
# guards the IMPORT graph; this one guards the packaging surface that makes the split real.
# --------------------------------------------------------------------------------------------------

EXPECTED_SCRIPTS = {
    "ctx-distillery": "ctx_distillery.cli:main",
    "ctx-distillery-apply": "ctx_distillery.apply:main",
}


def test_the_writer_hosts_its_own_entry_point() -> None:
    scripts = tomllib.loads(_read("pyproject.toml"))["project"]["scripts"]
    assert scripts == EXPECTED_SCRIPTS, (
        "CLAUDE.md invariant 8 requires exactly two console scripts, with the human-gated writer "
        f"hosting its own in `apply.py`. Found: {scripts}"
    )
