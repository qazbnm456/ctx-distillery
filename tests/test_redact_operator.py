"""Tier three — the operator's own redaction rules (`CD_REDACTIONS`).

The feature follows toolscout's `TS_TOOLSPACE` convention (an env var naming a JSON file, a
checked-in `*.example.json` you copy and edit) with ONE deliberate divergence that these tests exist
to pin: `TS_TOOLSPACE` REPLACES toolscout's built-in catalog, and `CD_REDACTIONS` may only ADD. The
worst case of a bad toolspace is fewer tools; the worst case of a replaced redaction catalog is a
leaked credential.

Three of the checks below are the ones that make an operator-supplied regex safe to run at all, and
each fails SILENTLY without them:

* `sample` is mandatory and EXECUTED — a regex that compiles but never matches gives false
  confidence, the same failure class as the Airtable POSIX trap `scripts/port_gitleaks.py` guards.
* ReDoS is refused at LOAD, by calibration against a probe grid — Python's `re` has no timeout, and
  a catastrophic pattern over a 500 KB transcript never returns.
* `replace_group` is checked against the rule's OWN named groups — a closed vocabulary, so the field
  stays data and never becomes a hook for code.

`redactions.example.json` is loaded here through the REAL loader, which is what keeps it from
rotting: toolscout's example can drift out of date, ours cannot.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ctx_distillery import redact
from ctx_distillery.redact import (
    _TIER1,
    _TIER2,
    REDACTIONS_ENV_VAR,
    OperatorRule,
    _apply_operator_rule,
    _replace_assignment,
    load_operator_rules,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "redactions.example.json"

#: A minimal, well-behaved rule — the baseline every malformed variant below is a mutation of.
GOOD = {
    "label": "corp-internal-token",
    "regex": r"\bcorp_[A-Za-z0-9]{32}\b",
    "description": "An internal service token.",
    "sample": "corp_abc123def456ghi789jkl012mno345pq",
    "replace_group": None,
}


def write_rules(tmp_path: Path, rules: object, name: str = "redactions.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


def load(tmp_path: Path, rules: object) -> tuple[OperatorRule, ...]:
    return load_operator_rules(write_rules(tmp_path, rules))


@pytest.fixture
def reload_redact(monkeypatch):
    """Reload `ctx_distillery.redact` with `CD_REDACTIONS` set, and put it back afterwards.

    The variable is resolved at IMPORT time on purpose (fail-closed: a broken operator file must stop
    the process, never leave a silently weaker redactor running), so the only honest way to exercise
    the tier end-to-end is a reload. `importlib.reload` re-executes into the SAME module `__dict__`,
    so functions other modules already imported by name — `session.py`'s `redact_transcript` — pick
    the new `_TIER3` up too, and the teardown reload really does restore the empty tier.
    """

    def _reload(path=None):
        if path is None:
            monkeypatch.delenv(REDACTIONS_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(REDACTIONS_ENV_VAR, str(path))
        return importlib.reload(redact)

    yield _reload
    monkeypatch.delenv(REDACTIONS_ENV_VAR, raising=False)
    importlib.reload(redact)


# ---------------------------------------------------------------------------------------------
# The convention, and the empty default
# ---------------------------------------------------------------------------------------------


def test_the_env_var_follows_the_toolscout_convention():
    assert REDACTIONS_ENV_VAR == "CD_REDACTIONS"
    assert EXAMPLE.is_file(), "the example ships at the repo root, like toolspace.example.json"
    assert "redactions.json" in (ROOT / ".gitignore").read_text(), "the copy target is git-ignored"


def test_an_unset_or_empty_env_var_means_no_third_tier():
    assert redact._load_operator_tier({}) == ()
    assert redact._load_operator_tier({REDACTIONS_ENV_VAR: ""}) == ()
    assert redact._load_operator_tier({REDACTIONS_ENV_VAR: "   "}) == ()


def test_the_suite_itself_runs_with_an_empty_third_tier():
    """`tests/conftest.py` pops the variable before the first import — hermeticity, stated."""
    assert REDACTIONS_ENV_VAR not in os.environ
    assert redact._TIER3 == ()


def test_the_env_var_names_a_path_and_expands_a_tilde(tmp_path, monkeypatch):
    assert write_rules(tmp_path, [GOOD]).name == "redactions.json"
    monkeypatch.setenv("HOME", str(tmp_path))
    loaded = redact._load_operator_tier({REDACTIONS_ENV_VAR: "~/redactions.json"})
    assert [rule.label for rule in loaded] == [GOOD["label"]]


# ---------------------------------------------------------------------------------------------
# `redactions.example.json` — a REAL working file, loaded through the REAL loader
# ---------------------------------------------------------------------------------------------


def test_the_example_file_loads_through_the_real_loader():
    """This is the anti-rot test. Loading it AT ALL runs every check, including each rule's sample."""
    rules = load_operator_rules(EXAMPLE)
    assert len(rules) == 8


@pytest.mark.parametrize("rule", load_operator_rules(EXAMPLE), ids=lambda r: r.label)
def test_every_example_rule_redacts_its_own_sample(rule):
    """Stated again per rule, so a failure names the offending label rather than the whole file."""
    out = _apply_operator_rule(rule, rule.sample)
    assert out != rule.sample
    assert f"[REDACTED:{rule.label}]" in out
    assert _apply_operator_rule(rule, out) == out, "idempotent"


def test_the_example_file_expresses_every_built_in_hand_written_pattern():
    """The dogfood: the schema has to be able to say what tier one already says, or it is too weak."""
    labels = [rule.label for rule in load_operator_rules(EXAMPLE)]
    assert labels[:7] == [label for label, _pattern in _TIER1]
    assert labels[7] == "corp-internal-token", "plus one illustrative org-specific entry"


def test_the_example_files_secret_assignment_reproduces_the_built_in_exactly():
    """`replace_group` earns its place HERE: it is the one built-in `{label, regex}` cannot express.

    `secret_assignment` keeps `password = "` and replaces only the value. Expressed as data, that is
    `"replace_group": "value"` over a regex that already names its groups — and the generic
    substitution must produce byte-identical output to the hand-written `_replace_assignment`.
    """
    rule = next(r for r in load_operator_rules(EXAMPLE) if r.label == "secret_assignment")
    assert rule.replace_group == "value"
    built_in = dict(_TIER1)["secret_assignment"]
    for line in ('password = "hunter2000"', "api_key: s3cr3t-value-here", "ACCESS_TOKEN=abcdef123456"):
        assert _apply_operator_rule(rule, line) == built_in.sub(_replace_assignment, line)


def test_the_example_files_illustrative_rule_actually_adds_coverage(reload_redact):
    """The eighth entry is what an operator would really write — so it must catch something new."""
    token = "corp_abc123def456ghi789jkl012mno345pq"
    assert token in redact.redact_transcript(f"the token is {token}"), "no built-in tier knows it"
    module = reload_redact(EXAMPLE)
    out = module.redact_transcript(f"the token is {token}")
    assert token not in out
    assert "[REDACTED:corp-internal-token]" in out


# ---------------------------------------------------------------------------------------------
# `sample` is MANDATORY and EXECUTED — the single most valuable check in the tier
# ---------------------------------------------------------------------------------------------


def test_a_sample_its_own_regex_misses_is_refused(tmp_path):
    """A regex that compiles but never matches is the failure this whole check exists for."""
    rule = {**GOOD, "regex": r"\bcorp_[A-Za-z0-9]{64}\b"}  # 64, but the sample carries 32
    with pytest.raises(SystemExit) as excinfo:
        load(tmp_path, [rule])
    message = str(excinfo.value)
    assert "corp-internal-token" in message, "the refusal NAMES the label"
    assert "does NOT redact its own `sample`" in message
    assert GOOD["sample"] in message


def test_a_long_sample_is_TRUNCATED_in_the_refusal_rather_than_reprinted_whole(tmp_path):
    """`sample` is the one field the schema requires to be credential-SHAPED, and a refusal goes to
    stderr — into scrollback, a CI log, a crash report. The docs say to use a synthetic value; this
    is the belt to that braces, so a real credential pasted in by mistake is not echoed in full.

    Untested until now: deleting the truncation from `_excerpt` left the whole suite green, which
    made the mitigation a comment rather than a behaviour.
    """
    secret = "A" * 400
    with pytest.raises(SystemExit) as excinfo:
        load(tmp_path, [{**GOOD, "regex": r"\bcorp_[A-Za-z0-9]{32}\b", "sample": secret}])
    message = str(excinfo.value)
    assert secret not in message, "the whole sample was reprinted — the echo cap did nothing"
    assert secret[: redact._SAMPLE_ECHO_CHARS] in message, "the head is still shown, to be useful"
    assert f"truncated, {len(secret)} characters" in message, "and the cut is DECLARED, not silent"


def test_a_sample_that_fits_the_echo_cap_is_shown_in_full():
    """The other side of the cap: it is long enough that a realistic synthetic sample still shows
    whole, so the truncation never makes a legitimate refusal harder to read."""
    short = "corp_" + "A" * 32
    assert len(short) <= redact._SAMPLE_ECHO_CHARS
    assert redact._excerpt(short) == repr(short)
    assert "truncated" not in redact._excerpt(short)


@pytest.mark.parametrize("missing", ["label", "regex", "description", "sample"])
def test_every_required_key_is_required(tmp_path, missing):
    rule = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(SystemExit, match=f"{missing!r} is REQUIRED"):
        load(tmp_path, [rule])


@pytest.mark.parametrize("value", ["", "   ", None, 7, ["a"]])
def test_a_blank_or_non_string_sample_is_refused(tmp_path, value):
    with pytest.raises(SystemExit, match="'sample' is REQUIRED"):
        load(tmp_path, [{**GOOD, "sample": value}])


def test_a_rule_that_re_matches_its_own_placeholder_is_refused(tmp_path):
    """Idempotence is a promise `redact_transcript` makes across every tier — enforced at load."""
    rule = {
        "label": "greedy",
        "regex": r"[A-Za-z]{6,}",
        "description": "Far too wide — it matches the word REDACTED inside its own placeholder.",
        "sample": "hunter2000secret",
        "replace_group": None,
    }
    with pytest.raises(SystemExit, match="is not idempotent"):
        load(tmp_path, [rule])


def test_a_replace_group_that_never_participates_is_caught_by_the_sample_check(tmp_path):
    """An optional group that the sample does not exercise makes the substitution a silent no-op."""
    rule = {
        "label": "internal-auth",
        "regex": r"X-Internal-Auth:\s*(?P<never>ZZZ)?(?P<value>\S+)",
        "description": "The named group is optional and the sample never reaches it.",
        "sample": "X-Internal-Auth: abc123def456",
        "replace_group": "never",
    }
    with pytest.raises(SystemExit) as excinfo:
        load(tmp_path, [rule])
    assert "does NOT redact its own `sample`" in str(excinfo.value)
    assert "may not have participated" in str(excinfo.value)


# ---------------------------------------------------------------------------------------------
# ReDoS — refused at LOAD, not survived at run time
# ---------------------------------------------------------------------------------------------

#: Catastrophic-backtracking shapes, and the LOAD-TIME budget in seconds each must be refused within.
#: The budget is the point of the whole rework: refusal has to be BOUNDED, because an operator file
#: is read at import and there is nowhere for a wedged import to report to. Every entry below was
#: refused in under 200 ms on this machine; the assertions leave a 10x margin for a slow CI box.
CATASTROPHIC = [
    r"(\w+\s?)+$",
    r"(a+)+$",
    r"(a|a)+$",
    r"(\d+)+$",
    r"([A-Za-z0-9]+)*$",
    # The one a FIXED probe alphabet could never trigger — `x` is in no fixed seed. It is caught
    # because `_literal_seeds` extends the grid with the pattern's OWN literal characters.
    r"(x+x+)+y",
    r"([a-zA-Z0-9_.-]+)+@corp\.example$",
    # --- the three an adversarial review got PAST the original calibration ---
    # A literal PREFIX plus a catastrophic tail: the single most common operator rule shape (it is
    # what `redactions.example.json`'s own `corp-internal-token` is, and what an `X-Internal-Auth:`
    # rule is). A probe of one repeated character never spells the prefix, so the match failed at
    # position 0 and the tail was never reached — calibration passed in 0.3 ms while the rule really
    # cost 1172 ms on `"CORPSECRETPREFIX-" + "a"*24`. `_reaching_prefixes` is what closes it.
    r"CORPSECRETPREFIX-(\w+\s?)+$",
    # --- the three a THIRD review got past the LITERAL-RUN scraper that first closed the above ---
    # A 2-character marker, under the scraper's 3-character minimum. Loaded in ~107 ms, then hung
    # for over 15 SECONDS on a 46-character line.
    r"X-(\w+\s?)+$",
    # A marker with a CHARACTER CLASS in it: the scraped run was `ORG-`, and no filler in the grid
    # spells `1234-`, so the tail was unreachable at any marker length.
    r"ORG-[0-9]{4}-(\w+\s?)+$",
    # The bisection's other side — 3 characters, so the scraper DID catch this one, which is exactly
    # what made the cutoff invisible. It must stay caught now that the cutoff is gone.
    r"XY-(\w+\s?)+$",
    # The marker is not at the START: everything left of it is itself a quantified tail. The parse
    # walk records a prefix at EVERY ambiguous quantifier, so `0-CORPSECRET-` is derived too.
    r"\d+-CORPSECRET-(\w+\s?)+$",
    # `z` is the SEVENTH distinct literal, and derived seeds used to stop at six.
    r"(?:abcdef)?(z+z+)+Q",
    # Already astronomical at the SMALLEST probe: 16.8 ms at 4 characters, 3.3 SECONDS at 6. The old
    # ascending-absolute-budget grid started at 12 and simply never returned — a HANG, not a refusal.
    r"(a?a?a?a?a?a?a?a?a?a?)+$",
    # POLYNOMIAL, not exponential: degree-7 backtracking whose growth ratio is under 2 by the time it
    # is above the noise floor, so gate 1 never fires and only the absolute budget catches it.
    r"a*a*a*a*a*a*b",
]


@pytest.mark.parametrize("pattern", CATASTROPHIC)
def test_a_catastrophic_pattern_is_refused_at_load(tmp_path, pattern):
    rule = {**GOOD, "label": "boom", "regex": pattern, "sample": "irrelevant — it never gets here"}
    start = time.perf_counter()
    with pytest.raises(SystemExit) as excinfo:
        load(tmp_path, [rule])
    elapsed = time.perf_counter() - start
    message = str(excinfo.value)
    assert "'boom'" in message, "the refusal NAMES the label"
    assert "backtracks catastrophically" in message
    assert "Rewrite it without nested/ambiguous quantifiers" in message
    assert elapsed < 2.0, f"refusal must be BOUNDED, took {elapsed:.1f}s — see redact.py's gate 1"


def test_the_growth_gate_and_the_budget_gate_each_catch_what_the_other_misses(tmp_path, monkeypatch):
    """Two gates, and neither is redundant — the reason both exist is that each has a live miss.

    Each gate is tested with THE OTHER ONE TURNED OFF, which is the literal statement of "catches
    what the other misses" and is also what makes the assertion deterministic. Reading the refusal
    message with both gates live was timing-fragile: under 20-way CPU contention this failed 1 run in
    3, because the polynomial pattern's jittered timings hit a 3x ratio and gate 1 refused it first.
    That direction is harmless (a refusal either way) but it made the which-gate claim untestable.
    """

    def refusal(pattern):
        with pytest.raises(SystemExit) as excinfo:
            load(tmp_path, [{**GOOD, "label": "boom", "regex": pattern, "sample": "unreached"}])
        return str(excinfo.value)

    # Gate 2 disabled: a budget nothing can exceed. The EXPONENTIAL shape must still be refused.
    monkeypatch.setattr(redact, "_REDOS_BUDGET_SECONDS", 30.0)
    assert "cost grew" in refusal(r"(a+)+$"), "exponential — caught by the growth ratio alone"

    # Gate 1 disabled: a ratio nothing can reach. The POLYNOMIAL shape must still be refused — its
    # growth is under 2x by the time it is above the noise floor, so only the budget can see it.
    monkeypatch.setattr(redact, "_REDOS_BUDGET_SECONDS", 0.020)
    monkeypatch.setattr(redact, "_GROWTH_RATIO", float("inf"))
    assert "budget 20 ms" in refusal(r"a*a*a*a*a*a*b"), "polynomial — caught by the budget alone"


def test_a_pattern_astronomical_at_the_smallest_probe_is_refused_rather_than_hanging(tmp_path):
    """The regression for the calibration HANG: `(a?){10}+` costs 3.3 s at SIX characters.

    The old grid started at 12 characters and timed a probe only after `search` returned, so this
    wedged the import outright (still running at 120 s). The ladder now starts at 2, where the same
    pattern costs ~0.09 ms, and the ratio gate sees 0.09 ms -> ~22 ms and refuses.
    """
    start = time.perf_counter()
    with pytest.raises(SystemExit, match="cost grew"):
        load(tmp_path, [{**GOOD, "label": "boom", "regex": r"(a?a?a?a?a?a?a?a?a?a?)+$",
                         "sample": "unreached"}])
    assert time.perf_counter() - start < 2.0


class _Stub:
    """A fake pattern whose `search` costs whatever the test says it costs."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.calls = 0

    def search(self, _probe):
        self.calls += 1
        if self.seconds:
            time.sleep(self.seconds)


def test_a_one_off_timing_spike_never_refuses_a_well_behaved_pattern():
    """A refusal is permanent and fatal; a descheduled interpreter is neither.

    This is not hypothetical — a good rule really was refused in this suite when a neighbouring
    process loaded the machine mid-`search`. `_settled_cost` re-times a suspicious probe and the
    SMALLEST reading decides, and it stops at the first honest one so noise costs a single re-run.
    """
    cheap = _Stub(0.0)
    settled = redact._settled_cost(cheap, "aaaa", 0.030, redact._REDOS_BUDGET_SECONDS)
    assert settled < redact._REDOS_BUDGET_SECONDS, "the spike is discarded"
    assert cheap.calls == 1, "one re-time was enough"


def test_a_genuinely_expensive_probe_survives_every_re_timing():
    """The other direction: confirmation must not become a way for a bad pattern to slip through."""
    slow = _Stub(0.025)
    settled = redact._settled_cost(slow, "aaaa", 0.025, redact._REDOS_BUDGET_SECONDS)
    assert settled > redact._REDOS_BUDGET_SECONDS
    assert slow.calls == redact._CONFIRM_RUNS, "it spent its whole confirmation budget and stood"


def test_calibration_runs_before_the_sample_check(tmp_path):
    """Order is load-bearing: the sample check RUNS the pattern, so a hang must be refused first."""
    rule = {**GOOD, "label": "boom", "regex": r"(a+)+$", "sample": "aaaaaaaaaaaaaaaaaaaaaaaaaaaa!"}
    with pytest.raises(SystemExit, match="backtracks catastrophically"):
        load(tmp_path, [rule])


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bcorp_[A-Za-z0-9]{32}\b",
        r"(?i)(?<=x-internal-auth:\s)[A-Za-z0-9+/=]{16,}",
        r"(?s)-----BEGIN CORP TOKEN-----.*?-----END CORP TOKEN-----",
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        # The WELL-BEHAVED version of the shape that evaded the old grid — a literal marker plus a
        # bounded tail. Run-prefixed probes must not start refusing this.
        r"CORPSECRETPREFIX-[A-Za-z0-9]{16,64}\b",
        r"(?i)(?P<header>X-Internal-Auth:\s*)(?P<value>[A-Za-z0-9+/=]{8,})",
    ],
)
def test_a_sane_pattern_passes_calibration(pattern):
    redact._calibrate("sane", re.compile(pattern))  # must not raise


#: Every pattern this project actually ships — the honest sample of "what a redactor wants".
BUILT_INS = [*_TIER1, *((rule.rule_id, rule.pattern) for rule in _TIER2)]


@pytest.mark.parametrize("label, pattern", BUILT_INS, ids=[label for label, _p in BUILT_INS])
def test_every_built_in_pattern_passes_the_same_calibration(label, pattern):
    """The budget's other direction, over real data: all 127 shipped patterns clear both gates.

    They are not subject to the calibration in production (only tier three is), but they are the
    honest sample of "patterns a redactor actually wants", and both thresholds were chosen against
    them — the slowest needs ~1.6 us for the grid's worst probe (best of five, on an idle box),
    four orders of magnitude under the 20 ms budget.
    """
    redact._calibrate(label, pattern)


@pytest.mark.parametrize("label, pattern", BUILT_INS, ids=[label for label, _p in BUILT_INS])
def test_no_built_in_probe_even_reaches_the_growth_floor(label, pattern):
    """Why the RATIO gate cannot false-positive on a well-behaved pattern — checked, not asserted.

    Gate 1 is ignored entirely below `_GROWTH_FLOOR_SECONDS`, so a pattern whose every probe stays
    under that floor is structurally out of its reach no matter how the timings jitter. This is the
    measurement that makes the floor a safety argument rather than a hopeful constant.

    Best-of-three per probe, for the same reason `_settled_cost` exists: on a loaded box a single
    wall-clock reading measures the scheduler, not the regex.
    """
    worst = max(
        min(redact._time_search(pattern, probe) for _ in range(3))
        for _n, probe, _f in redact._probes(pattern.pattern)
    )
    assert worst < redact._GROWTH_FLOOR_SECONDS, (
        f"{label} needs {worst * 1000:.2f} ms for one <=32-byte probe, at or over the "
        f"{redact._GROWTH_FLOOR_SECONDS * 1000:.0f} ms floor where the growth gate starts looking"
    )


def test_the_probe_grid_is_shortest_first_and_starts_at_two_characters():
    """Ascending length is what BOUNDS the load cost: the first breach aborts the whole grid.

    Starting at TWO is the fix for the hang. A pattern that is already astronomical at the smallest
    probe cannot be bounded at all (there is no way to time a search without running it), so the
    smallest probe has to be small enough that no realistic shape is expensive on it.
    """
    lengths = [length for length, _probe, _family in redact._probes(r"\bcorp_[a-z]+\b")]
    assert lengths == sorted(lengths)
    assert lengths[0] == 2 and lengths[-1] == 32


def test_the_probe_grid_is_extended_with_the_patterns_own_literals():
    """The `(x+x+)+y` case: a fixed alphabet cannot make a pattern keyed on `x` backtrack."""
    assert "x" in redact._literal_seeds(r"(x+x+)+y")
    assert "w" not in redact._literal_seeds(r"(\w+\s?)+$"), "a backslash escape is not a literal"


def test_every_distinct_literal_becomes_a_seed_not_just_the_first_few():
    """The `(?:abcdef)?(z+z+)+Q` evasion: `z` was the SEVENTH literal, and the cap was six."""
    seeds = redact._literal_seeds(r"(?:abcdef)?(z+z+)+Q")
    assert "z" in seeds and "Q" in seeds
    assert not hasattr(redact, "_MAX_DERIVED_SEEDS"), "the cap is gone, not merely raised"


def test_the_probe_grid_prefixes_the_patterns_own_reaching_prefix():
    """The most common operator shape: a marker plus a quantified tail.

    A probe of repeated single characters never spells the marker, so the match fails at position 0
    and the catastrophic tail is never reached. The grid has to lead with the marker itself.
    """
    assert redact._reaching_prefixes(r"CORPSECRETPREFIX-(\w+\s?)+$")[0] == "CORPSECRETPREFIX-"
    probes = [probe for _n, probe, _f in redact._probes(r"CORPSECRETPREFIX-(\w+\s?)+$")]
    assert any(p.startswith("CORPSECRETPREFIX-") and p.endswith("!") for p in probes)


@pytest.mark.parametrize(
    "source, expected",
    [
        # --- what the TEXTUAL run scraper could not spell (the third review's HIGH-1) ---
        # A 2-character marker: under the old 3-character minimum, so no prefixed probe existed.
        (r"X-(\w+\s?)+$", ["X-", "X-a"]),
        # A CHARACTER CLASS inside the marker: the scraper yielded the run `ORG-` and no filler
        # spells `1234-`. The tree knows `[0-9]{4}` is four concrete digits.
        (r"ORG-[0-9]{4}-(\w+\s?)+$", ["ORG-0000-", "ORG-0000-a"]),
        # The bisection's caught side, still caught.
        (r"XY-(\w+\s?)+$", ["XY-", "XY-a"]),
        # A marker BEHIND another ambiguous quantifier — a prefix is recorded at each of them, so a
        # second marker further right is reachable too. The old scraper found this one textually.
        (r"\d+-CORPSECRET-(\w+\s?)+$", ["0-CORPSECRET-", "0-CORPSECRET-a"]),
        # --- shapes the scraper handled, which must not regress ---
        # A class body is not literal text, but the tree turns it into concrete characters that
        # actually reach the `\d+` behind it.
        (r"[a-z]{3}\d+", ["aaa", "aaa0"]),
        # No quantifier at all: the whole pattern is one reaching prefix.
        (r"gems\.contribsys\.com", ["gems.contribsys.com"]),
        # `\w` is a class, not a literal — the tree resolves it rather than breaking the run in two.
        (r"abc\wdefg", ["abcadefg"]),
        # A marker too short for the old minimum, in front of a tail keyed on its own character.
        (r"ab(x+x+)+y", ["ab", "abx", "abxxy"]),
        # A leading LOOKBEHIND really does precede the match, so its text belongs in the prefix.
        (r"(?<=corp-auth:\s)[A-Za-z0-9]+", ["corp-auth: ", "corp-auth: a"]),
    ],
)
def test_reaching_prefix_derivation_is_the_documented_one(source, expected):
    assert redact._reaching_prefixes(source) == expected


def test_the_derived_prefix_count_is_capped_so_the_load_cost_is_bounded_in_aggregate():
    """Gate 1 bounds the cost of ONE probe; this is what bounds how many probes there are.

    The old textual scraper emitted one prefix per distinct literal run, which is unbounded in the
    pattern's length: an adversarial review's 7,919-alternative pattern produced 1,014,880 probes
    and a 16.8 s load, every individual probe cheap. Both derived families are bounded now — seeds
    by the alphabet itself, prefixes by an explicit cap.
    """
    pathological = "|".join(f"AAA{i:04d}BBB" for i in range(7919))
    assert len(redact._reaching_prefixes(pathological)) == redact._MAX_REACHING_PREFIXES
    seeds = len(redact._PROBE_SEEDS) + len(redact._literal_seeds(pathological))
    ceiling = len(redact._PROBE_LENGTHS) * len(redact._PROBE_TAILS) * (
        seeds + redact._MAX_REACHING_PREFIXES * len(redact._RUN_FILLERS)
    )
    probes = sum(1 for _ in redact._probes(pathological))
    assert probes == ceiling < 10_000, f"{probes} probes for one pattern is not a bound"


def test_a_prefix_walk_that_cannot_finish_degrades_to_what_it_already_found():
    """Never raise out of calibration: an underivable construct costs prefixes, not a crash.

    `(?(1)a|b)` is a conditional the walk has no case for. The safe direction is fewer probes — the
    seed alphabet and both timing gates still run on everything.
    """
    assert redact._reaching_prefixes(r"(a)CORP-(?(1)a|b)(\w+\s?)+$") == ["aCORP-"]
    redact._calibrate("conditional", re.compile(r"(a)CORP-(?(1)a|b)[A-Za-z]{4}"))  # must not raise


# ---------------------------------------------------------------------------------------------
# `replace_group` — a closed vocabulary, still data, never code
# ---------------------------------------------------------------------------------------------


def test_replace_group_keeps_everything_but_the_named_group(tmp_path):
    """The independently useful case the design decision names: keep the header, drop the value."""
    (rule,) = load(
        tmp_path,
        [
            {
                "label": "internal-auth",
                "regex": r"(?i)(?P<header>X-Internal-Auth:\s*)(?P<value>[A-Za-z0-9+/=]{8,})",
                "description": "Redact the value after X-Internal-Auth:, keep the header name.",
                "sample": "X-Internal-Auth: QUJDREVGR0hJSktMTU5PUFFS",
                "replace_group": "value",
            }
        ],
    )
    out = _apply_operator_rule(rule, "GET /x\nX-Internal-Auth: QUJDREVGR0hJSktMTU5PUFFS\nhost: a\n")
    assert out == "GET /x\nX-Internal-Auth: [REDACTED:internal-auth]\nhost: a\n"


def test_a_null_replace_group_replaces_the_whole_match(tmp_path):
    (rule,) = load(tmp_path, [GOOD])
    assert rule.replace_group is None
    out = _apply_operator_rule(rule, f"tok {GOOD['sample']} end")
    assert out == "tok [REDACTED:corp-internal-token] end"


def test_replace_group_is_optional_and_may_be_omitted_entirely(tmp_path):
    (rule,) = load(tmp_path, [{k: v for k, v in GOOD.items() if k != "replace_group"}])
    assert rule.replace_group is None


def test_replace_group_naming_an_undeclared_group_is_refused(tmp_path):
    rule = {**GOOD, "regex": r"\bcorp_(?P<tail>[A-Za-z0-9]{32})\b", "replace_group": "value"}
    with pytest.raises(SystemExit) as excinfo:
        load(tmp_path, [rule])
    message = str(excinfo.value)
    assert "'replace_group' names 'value'" in message
    assert "['tail']" in message, "the refusal lists what IS available"


def test_replace_group_on_a_regex_with_no_named_groups_says_so(tmp_path):
    with pytest.raises(SystemExit, match="the regex declares no named groups"):
        load(tmp_path, [{**GOOD, "replace_group": "value"}])


@pytest.mark.parametrize("value", ["", 1, ["value"], {}])
def test_a_non_string_replace_group_is_refused(tmp_path, value):
    with pytest.raises(SystemExit, match="must be null, absent, or a non-empty group NAME"):
        load(tmp_path, [{**GOOD, "replace_group": value}])


def test_an_optional_group_that_did_not_participate_leaves_that_match_alone(tmp_path):
    """Per MATCH, not per rule — a rule may fire on one line and legitimately skip another."""
    (rule,) = load(
        tmp_path,
        [
            {
                "label": "kv",
                "regex": r"corp\.(?:(?P<value>[a-z]{6,})|MISSING)",
                "description": "The group is inside an alternation, so some matches skip it.",
                "sample": "corp.abcdef",
                "replace_group": "value",
            }
        ],
    )
    assert _apply_operator_rule(rule, "corp.MISSING and corp.abcdef") == (
        "corp.MISSING and corp.[REDACTED:kv]"
    )


# ---------------------------------------------------------------------------------------------
# ADDITIVE ONLY — the deliberate divergence from `TS_TOOLSPACE`
# ---------------------------------------------------------------------------------------------


def test_the_operator_tier_cannot_disable_tier_one_or_tier_two(reload_redact, tmp_path):
    """An operator file is APPENDED to a floor it has no way to lower. There is no `disable` key."""
    module = reload_redact(write_rules(tmp_path, [GOOD]))
    assert len(module._TIER1) == 7 and len(module._TIER2) == 120 and len(module._TIER3) == 1
    text = (
        "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 and "
        "xoxb-1234567890-1234567890123-A1b2C3d4E5f6G7h8I9j0K1l2"
    )
    out = module.redact_transcript(text)
    assert "ghp_" not in out and "xoxb-" not in out
    assert "[REDACTED:github_token]" in out and "[REDACTED:slack-bot-token]" in out


def test_a_disable_key_is_not_part_of_the_schema(tmp_path):
    with pytest.raises(SystemExit, match=r"unknown key\(s\) \['disable'\]"):
        load(tmp_path, [{**GOOD, "disable": ["github_token"]}])


def test_an_operator_rule_cannot_steal_a_tier_one_labels_shape(reload_redact, tmp_path):
    """Tier three runs LAST, so by the time it sees the text tier one has already claimed its own."""
    rule = {
        "label": "my-github-token",
        "regex": r"\bghp_[A-Za-z0-9]{16,}\b",
        "description": "The same shape tier one owns, under a different label.",
        "sample": "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
        "replace_group": None,
    }
    module = reload_redact(write_rules(tmp_path, [rule]))
    out = module.redact_transcript("token ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 here")
    assert out == "token [REDACTED:github_token] here", "tier one's label survives"


def test_the_third_tier_is_wired_into_redact_transcript_and_redact_all(reload_redact, tmp_path):
    module = reload_redact(write_rules(tmp_path, [GOOD]))
    assert len(module._TIER3) == 1
    out = module.redact_all([f"a {GOOD['sample']}", "b plain"])
    assert out == ["a [REDACTED:corp-internal-token]", "b plain"]


def test_redaction_stays_idempotent_with_a_third_tier_loaded(reload_redact, tmp_path):
    module = reload_redact(EXAMPLE)
    blob = "\n".join(rule.sample for rule in load_operator_rules(EXAMPLE))
    once = module.redact_transcript(blob)
    assert module.redact_transcript(once) == once


def test_benign_prose_survives_all_three_tiers_untouched(reload_redact, tmp_path):
    module = reload_redact(EXAMPLE)
    text = (
        "user: can you refactor the parser?\n"
        "assistant: sure — I'll split tokenize() out of parse() first.\n"
        "user: also the CI job on 3.13 is red, and commit 9d09c20 looks suspicious.\n"
    )
    assert module.redact_transcript(text) == text


# ---------------------------------------------------------------------------------------------
# A malformed file fails CLOSED — loudly, at startup
# ---------------------------------------------------------------------------------------------


def test_a_missing_file_is_refused_rather_than_ignored(tmp_path):
    with pytest.raises(SystemExit, match="cannot read"):
        load_operator_rules(tmp_path / "nope.json")


def test_invalid_json_is_refused(tmp_path):
    path = tmp_path / "redactions.json"
    path.write_text("[{,]", encoding="utf-8")
    with pytest.raises(SystemExit, match="is not valid JSON"):
        load_operator_rules(path)


@pytest.mark.parametrize("document", [{}, "a string", 42, None])
def test_a_non_list_document_is_refused(tmp_path, document):
    with pytest.raises(SystemExit, match="must hold a JSON LIST of rules"):
        load(tmp_path, document)


def test_an_empty_list_is_a_legal_empty_tier(tmp_path):
    assert load(tmp_path, []) == ()


@pytest.mark.parametrize("entry", ["a string", 42, ["nested"]])
def test_a_non_object_entry_is_refused(tmp_path, entry):
    with pytest.raises(SystemExit, match="not an object"):
        load(tmp_path, [entry])


def test_an_unknown_key_is_refused_rather_than_ignored(tmp_path):
    """A mistyped `replace_group` would otherwise silently mean 'replace the whole match'."""
    with pytest.raises(SystemExit, match=r"unknown key\(s\) \['replacegroup'\]"):
        load(tmp_path, [{**GOOD, "replacegroup": "value"}])


def test_a_duplicate_label_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="duplicate label"):
        load(tmp_path, [GOOD, {**GOOD, "regex": r"\bcorp2_[A-Za-z0-9]{32}\b",
                               "sample": "corp2_abc123def456ghi789jkl012mno345pq"}])


@pytest.mark.parametrize("label", ["a[b", "a]b", "[REDACTED:x]"])
def test_a_label_containing_a_bracket_is_refused(tmp_path, label):
    with pytest.raises(SystemExit, match="may not contain a bracket"):
        load(tmp_path, [{**GOOD, "label": label}])


def test_an_uncompilable_regex_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="'regex' does not compile"):
        load(tmp_path, [{**GOOD, "regex": r"\bcorp_([A-Za-z0-9]{32}\b"}])


def test_an_unported_posix_class_is_refused_rather_than_silently_meaning_something_else(tmp_path):
    """The Airtable trap, arriving through the operator's file instead of the vendored corpus.

    `[[:alnum:]]{14}` COMPILES in Python — with nothing but a `FutureWarning` — and then parses as a
    character set plus 14 literal `]`, so it matches nothing like what the author meant. Warnings are
    promoted to errors here for exactly that reason. (The `sample` check would also have caught this
    one; both fire, and the compile message is the more useful of the two.)
    """
    rule = {**GOOD, "regex": r"\bcorp_[[:alnum:]]{32}\b"}
    with pytest.raises(SystemExit, match="'regex' does not compile"):
        load(tmp_path, [rule])


def test_the_refusal_always_names_the_file_and_the_env_var(tmp_path):
    path = write_rules(tmp_path, [{**GOOD, "regex": r"\bcorp_[A-Za-z0-9]{64}\b"}])
    with pytest.raises(SystemExit) as excinfo:
        load_operator_rules(path)
    assert str(excinfo.value).startswith("CD_REDACTIONS: ")
    assert str(path) in str(excinfo.value)


def test_a_broken_file_named_by_the_env_var_stops_the_import_dead(tmp_path):
    """Fail CLOSED, in a fresh process: `import ctx_distillery` must not survive a bad rule file.

    A redactor that silently gets weaker is the failure this module exists to avoid, so the load is
    deliberately not defensive — the same stance `_load_gitleaks_subset` takes about a missing
    vendored artifact.
    """
    path = write_rules(tmp_path, [{**GOOD, "regex": r"\bcorp_[A-Za-z0-9]{64}\b"}])
    env = {**os.environ, REDACTIONS_ENV_VAR: str(path)}
    out = subprocess.run(
        [sys.executable, "-c", "import ctx_distillery"],
        capture_output=True, text=True, check=False, env=env, cwd=str(ROOT),
    )
    assert out.returncode != 0
    assert "CD_REDACTIONS" in out.stderr
    assert "corp-internal-token" in out.stderr


def test_redact_py_is_still_inside_the_write_capability_scan():
    """Tier three reads a file; it must not have grown a way to write one (CLAUDE.md invariant 1)."""
    from tests.test_no_write_capability import SOURCES

    assert (ROOT / "ctx_distillery" / "redact.py") in SOURCES
