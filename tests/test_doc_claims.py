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

Most of what follows reads source files as TEXT rather than importing them. Two reasons, both
deliberate: the workspace members (`eval/`, `studio/`) are not importable from the root suite in a
plain-pip install, and a textual scan is the idiom this repo already uses for cross-cutting guards
(`test_no_write_capability.py`, `studio/tests/static-contract.test.js`).

**Claim 5 is the one exception, and it has to be**: it pins a doc claim about BEHAVIOUR (does this
slugger truncate, or refuse?), and the only honest way to check behaviour is to run it. It imports
`ctx_distillery` — the ROOT package, which every other test in this suite already imports, so the
plain-pip reason above does not apply to it. The two workspace members stay textual there, as
everywhere else.
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
# Claim 1: all four sluggers in the workspace share ONE bound, and CLAUDE.md states that number.
#
# CLAUDE.md invariant 8 names `_SLUG_MAX` (120) as "the same number" the other three sluggers use —
# `cli._slug`, `studio`'s `app._RUN_ID_MAX`, `eval`'s `cli._TASK_ID_MAX`. All three source files
# repeat that cross-reference in their own comments. Nothing checked it: each member's suite pins
# only its OWN constant (`eval/tests/test_cli.py`, `studio/tests/test_app.py`), so any one of the
# four could move and leave the docs plus three comments quietly wrong.
#
# This claim is about the NUMBER only. What the four DO with it differs, and claim 5 below is what
# pins that half — the split exists because the number agreeing is exactly what made three
# simultaneously-wrong prose claims about the behaviour look fine.
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

#: Every workflow that installs uv must do it from the SAME pinned SHA. Same reasoning as the ruff
#: pin below: N copies of a version is N chances to bump N-1 of them. Not folded into
#: `RUFF_PIN_FILES` — a different pin with a different bump cadence.
#:
#: DERIVED from the directory rather than hard-coded, because the hard-coded tuple that used to live
#: here drifted within a single afternoon: `install-check.yml` landed as a third workflow and the
#: check kept passing while covering two of three. A list of files that must all agree should never
#: be a list somebody has to remember to extend.
def _workflows_using_setup_uv() -> dict[str, set[str]]:
    found = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        shas = set(re.findall(r"astral-sh/setup-uv@([0-9a-f]{40})", path.read_text(encoding="utf-8")))
        if shas or "astral-sh/setup-uv" in path.read_text(encoding="utf-8"):
            found[path.relative_to(ROOT).as_posix()] = shas
    return found


def test_every_workflow_pins_setup_uv_to_one_sha() -> None:
    found = _workflows_using_setup_uv()
    assert len(found) >= 2, f"expected several workflows to install uv; found {list(found)}"
    for relative, shas in found.items():
        assert shas, (
            f"{relative} references astral-sh/setup-uv without a 40-hex SHA — pin it, never a tag"
        )
    assert len(set().union(*found.values())) == 1, (
        f"workflows install uv from different pinned SHAs: {found}"
    )


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


# --------------------------------------------------------------------------------------------------
# Claim 5: the four sluggers share a NUMBER but not a BEHAVIOUR — three TRUNCATE, `apply.slugify`
# REFUSES — and the docs must not describe the refusing one as truncating.
#
# This claim exists because claim 1 was not enough, and the way it failed is the interesting part.
# Claim 1 pins that all four constants equal 120, and it stayed green while THREE separate prose
# claims said the opposite of what the code does:
#
#   * `apply.py`'s own `slugify` docstring carried a superseded draft line ("then capped") directly
#     above the line that replaced it ("**It does NOT truncate**") — a paragraph contradicting itself
#     in two consecutive lines;
#   * CLAUDE.md invariant 8 said "the slug CAPPED at `_SLUG_MAX`" and listed the three run-id
#     sluggers as using "the same number", which reads as the same handling;
#   * CHANGELOG.md's `[Unreleased]` entry said "`slugify` now caps its output".
#
# The code was right in all three cases. That is what makes this a docs test rather than a bug fix:
# an agent implementing from the invariant would have written the truncation `slugify`'s own
# docstring spends a paragraph arguing against, and the reason it argues that way is a SAFETY
# property, not a style preference — a run id is machine bookkeeping, so shortening it loses nothing
# a human relied on, while a promotion slug is the identity the operator APPROVED, so installing
# under a shortened one substitutes a name they never saw.
#
# Pinned by BEHAVIOUR for the two root-package sluggers (see the module docstring on why this claim
# imports), and textually for all three that truncate — textual is the only check available for the
# two workspace members, and it also names WHICH constant each cut is against.
# --------------------------------------------------------------------------------------------------

#: Longer than every cap in `SLUGGERS`, and made of characters `slugify`'s own class keeps, so the
#: only thing that can shorten the result is a truncation.
_OVER_LONG = "a" * 300

#: The three that DO truncate, and the slice each one must keep. `apply.py` is deliberately absent.
TRUNCATING_SLUGGERS: dict[str, str] = {
    "ctx_distillery/cli.py": "[:_RUN_ID_MAX]",
    "studio/ctx_distillery_studio/app.py": "[:_RUN_ID_MAX]",
    "eval/ctx_distillery_eval/cli.py": "[:_TASK_ID_MAX]",
}


def test_apply_slugify_refuses_rather_than_truncating() -> None:
    """`slugify` returns the over-long slug INTACT — the caller is what refuses it.

    Would have failed on a `slugify` "fixed" to match the three run-id sluggers, which is precisely
    what the three wrong prose claims described.
    """
    from ctx_distillery.apply import _SLUG_MAX, slugify

    produced = slugify(_OVER_LONG)
    assert len(produced) == len(_OVER_LONG), (
        "`apply.slugify` truncated its output. It must NOT: a promotion slug is the identity the "
        "operator approved, so `_promote`/`_promote_skill` REFUSE one over `_SLUG_MAX` instead of "
        "silently installing under a name nobody saw. If this changed on purpose, CLAUDE.md "
        "invariant 8 and `slugify`'s own docstring have to change with it."
    )
    assert len(produced) > _SLUG_MAX, "the probe is no longer over the bound — raise `_OVER_LONG`"


def test_the_refusal_lives_in_both_promotion_paths() -> None:
    """Both write paths must carry the length refusal `slugify` deliberately does not do itself."""
    source = _read("ctx_distillery/apply.py")
    assert source.count("len(slug) > _SLUG_MAX") == 2, (
        "`apply.py` must refuse an over-long slug in BOTH `_promote` and `_promote_skill`. Since "
        "`slugify` does not truncate, a missing branch here is an unbounded path component reaching "
        "the filesystem — an OSError (ENAMETOOLONG) out of a half-applied `--approve` run."
    )
    assert "[:_SLUG_MAX]" not in source, (
        "`apply.py` now truncates with `[:_SLUG_MAX]` — see "
        "`test_apply_slugify_refuses_rather_than_truncating` for why that is the wrong bound here."
    )


def test_the_run_id_slugger_in_this_package_does_truncate() -> None:
    """The other half of the asymmetry, so "they differ" is pinned from both sides rather than
    asserted about one of them."""
    from ctx_distillery.cli import _RUN_ID_MAX, _slug

    assert len(_slug(_OVER_LONG)) == _RUN_ID_MAX, (
        "`cli._slug` stopped truncating. A run id names a trace file nobody approved, so it caps "
        "where `apply.slugify` refuses — if that changed, CLAUDE.md invariant 8's statement of the "
        "asymmetry has to change with it."
    )


@pytest.mark.parametrize(("relative", "slice_form"), sorted(TRUNCATING_SLUGGERS.items()))
def test_every_truncating_slugger_keeps_its_slice(relative: str, slice_form: str) -> None:
    """All THREE, textually — the only check available for `eval/` and `studio/`, which are not
    importable from the root suite. `ctx_distillery/cli.py` is in here too even though the test
    above already pins it by behaviour: this one names the exact slice form, so it says WHICH
    constant the cut is against, and the duplication costs a line."""
    assert slice_form in _read(relative), (
        f"{relative}'s slugger no longer truncates with `{slice_form}`. All three run-id sluggers "
        f"cut; only `apply.slugify` refuses. Both halves are stated in CLAUDE.md invariant 8."
    )


def test_claude_md_states_the_refusal_and_never_calls_it_a_cap() -> None:
    """The doc wording itself — the thing that actually drifted, three times, while everything else
    in this module stayed green."""
    sentence = re.search(r"`_SLUG_MAX` \(\d+\)(.{0,600})", _read("CLAUDE.md"), re.DOTALL)
    assert sentence is not None, "CLAUDE.md invariant 8 no longer states `_SLUG_MAX` (<n>)"
    text = sentence.group(1)
    assert re.search(r"REFUSE", text, re.IGNORECASE), (
        "CLAUDE.md invariant 8 no longer says `apply.slugify` REFUSES an over-long name. It must: "
        "the previous wording ('the slug CAPPED at `_SLUG_MAX`', beside the three sluggers that "
        "really do cap) described the opposite behaviour, and nothing here caught it."
    )
    # A NEGATED mention of truncation, in any of the natural phrasings. Bare "TRUNCAT" would not do:
    # a rewrite to "the slug is TRUNCATED at `_SLUG_MAX`" — the exact drift this catches — contains
    # it. The old wording ("the slug CAPPED at ...") contains neither this nor the refusal above.
    assert re.search(r"(not|never|rather than|instead of)\s+TRUNCAT", text, re.IGNORECASE), (
        "CLAUDE.md invariant 8 must say explicitly that `slugify` does NOT truncate. Naming the "
        "refusal alone is not enough — the wording that drifted read as a cap precisely because it "
        "sat next to three sluggers that cap."
    )


# --------------------------------------------------------------------------------------------------
# Claim 6: the release workflow's two irreversibility guards survive.
#
# Publishing to PyPI is the one action in this repository that cannot be undone — a version number
# can never be reused. Two things in `.github/workflows/release.yml` stand between a mistake and a
# permanent one, and both are a single deletable line:
#
#   * the FORK GUARD, which stops a fork's own release from asking PyPI to publish OUR project;
#   * the TAG/VERSION check, which stops `v0.2.0` from shipping a wheel that says `0.1.0`.
#
# Neither can fail visibly in testing: the fork guard only matters in someone else's repository, and
# the version check only fires on a mismatch nobody creates on purpose. So they are pinned here.
# --------------------------------------------------------------------------------------------------

RELEASE_WORKFLOW = ".github/workflows/release.yml"


def test_the_release_workflow_only_publishes_from_this_repository() -> None:
    source = _read(RELEASE_WORKFLOW)
    guard = re.search(r"if:\s*github\.repository\s*==\s*'([^']+)'", source)
    assert guard is not None, (
        f"{RELEASE_WORKFLOW}'s publish job lost its `if: github.repository == '...'` fork guard. "
        f"`release: published` is inherited by every fork, so without it a fork cutting a release "
        f"asks PyPI to publish this project."
    )
    expected = "qazbnm456/ctx-distillery"
    assert guard.group(1) == expected, (
        f"the fork guard names {guard.group(1)!r}, not {expected!r} — a guard on the wrong "
        f"repository is the same as no guard"
    )


def test_the_release_workflow_refuses_a_tag_that_disagrees_with_the_version() -> None:
    """A PyPI version can never be reused, so a tag/version mismatch is unrecoverable rather than
    merely wrong. `test_version_matches_pyproject` already ties `__version__` to `pyproject.toml`;
    this ties the git TAG to the same number, which is the half CI cannot otherwise see."""
    source = _read(RELEASE_WORKFLOW)
    assert 'github.event.release.tag_name' in source and '"v$version"' in source, (
        f"{RELEASE_WORKFLOW} no longer compares the release tag against `pyproject.toml`'s version. "
        f"Without it, a `vX.Y.Z` tag can publish a wheel claiming some other version, permanently."
    )
