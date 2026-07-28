"""The vendored gitleaks subset, checked against gitleaks' OWN regression corpus.

`ctx_distillery/patterns/gitleaks_subset.json` is a mechanical port of somebody else's regexes from
Go/RE2 to Python `re` (`scripts/port_gitleaks.py`, `VENDOR.md`). A port like that fails SILENTLY:
the pattern still compiles, it just stops meaning what it meant — and in a REDACTOR a pattern that
quietly matches nothing is a live credential flowing into a language model's context with no error
anywhere. So the vendored copy is only trustworthy to the extent something re-derives upstream's
expectations against it. That is this file.

The fixture (`tests/data/gitleaks_golden.json`, written by `scripts/extract_gitleaks_golden.py`) is
scraped from the `tps`/`fps` blocks in gitleaks' own Go rule generator, so the expectations are
upstream's, not ones written to match whatever the port happened to produce.
"""

from __future__ import annotations

import importlib.util
import json
import re
import warnings
from pathlib import Path

import pytest

from ctx_distillery import redact
from ctx_distillery.redact import _TIER1, _TIER2, redact_transcript

ROOT = Path(__file__).resolve().parent.parent
SUBSET = json.loads((ROOT / "ctx_distillery" / "patterns" / "gitleaks_subset.json").read_text())
GOLDEN: dict[str, dict[str, list[str]]] = json.loads(
    (ROOT / "tests" / "data" / "gitleaks_golden.json").read_text()
)
LIVENESS: dict[str, str] = json.loads(
    (ROOT / "tests" / "data" / "liveness_samples.json").read_text()
)["samples"]
RULES = {rule.rule_id: rule for rule in _TIER2}

#: gitleaks' sliding keyword prefix — the marker of the ~101 generic rules deliberately NOT taken.
SLIDING_PREFIX = r"[\w.-]{0,50}?"


def _load_porter():
    """Import `scripts/port_gitleaks.py` by path — `scripts/` is not an importable package."""
    path = ROOT / "scripts" / "port_gitleaks.py"
    spec = importlib.util.spec_from_file_location("port_gitleaks_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PORTER = _load_porter()


# ---------------------------------------------------------------------------------------------
# The generated artifact
# ---------------------------------------------------------------------------------------------


def test_the_subset_is_the_shape_the_porter_promises():
    assert SUBSET["_generated_by"].startswith("scripts/port_gitleaks.py")
    assert "gitleaks" in SUBSET["_source"]
    assert "Zachary Rice" in SUBSET["_licence"]
    assert SUBSET["_upstream_rules"] == 222
    assert len(SUBSET["rules"]) == 120
    assert len(_TIER2) == 120
    ids = [rule["id"] for rule in SUBSET["rules"]]
    assert ids == sorted(ids), "the artifact is sorted so a refresh diffs cleanly"
    assert len(set(ids)) == len(ids)
    for entry in SUBSET["rules"]:
        assert set(entry) <= {"id", "regex", "keywords", "entropy"}
        assert entry["regex"]
        assert isinstance(entry["keywords"], list)


def test_no_generic_keyword_near_assignment_rule_slipped_in():
    """Shape B is excluded ON PURPOSE — see VENDOR.md. It needs a 1,446-entry stopword allowlist."""
    offenders = [e["id"] for e in SUBSET["rules"] if SLIDING_PREFIX in e["regex"]]
    assert not offenders, offenders


def test_no_posix_class_survives_in_the_artifact():
    offenders = [e["id"] for e in SUBSET["rules"] if re.search(r"\[:\w+:\]", e["regex"])]
    assert not offenders, offenders


@pytest.mark.parametrize("entry", SUBSET["rules"], ids=lambda e: e["id"])
def test_every_ported_pattern_recompiles_with_warnings_as_errors(entry):
    """The tripwire for a silent mis-port — see this module's docstring and `port_gitleaks.py`."""
    re.purge()  # `re.compile` caches; a cache hit would skip the parse that emits the warning
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        re.compile(entry["regex"])


def test_the_LOADER_refuses_a_posix_class_even_with_the_compile_cache_primed(tmp_path):
    """Pins the guard in `redact._load_gitleaks_subset`, which nothing used to pin.

    The test above compiles the ARTIFACT's regexes itself, so it stays green even if the loader's own
    `warnings.simplefilter("error")` is deleted — an adversarial review replaced it with `if True:`
    and turned nothing red. This drives the real loader against a hand-made artifact instead.

    Priming the cache first is the other half. `re.compile` caches by `(pattern, flags)` and a cache
    HIT never re-parses, so it never re-emits the `FutureWarning` the strict compile exists to catch;
    without the loader's `re.purge()` this exact artifact imported CLEANLY and shipped a dead rule.

    `simplefilter("always")` around the call is what makes this pin the LOADER rather than whatever
    warning filter happened to be ambient: under it, nothing but the loader's own
    `simplefilter("error")` can turn the `FutureWarning` into the exception below.
    """
    artifact = tmp_path / "gitleaks_subset.json"
    artifact.write_text(
        json.dumps({"rules": [{"id": "posix-trap", "regex": AIRTABLE_UPSTREAM, "keywords": ["pat"]}]}),
        encoding="utf-8",
    )
    re.compile(AIRTABLE_UPSTREAM)  # prime the cache with the very pattern the loader must refuse
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        with pytest.raises(Warning):
            redact._load_gitleaks_subset(artifact)


def test_the_loader_accepts_the_ported_form_of_the_same_rule(tmp_path):
    """The other half of the check above: it refuses the TRAP, not every hand-made artifact."""
    artifact = tmp_path / "gitleaks_subset.json"
    ported = PORTER.port_regex(AIRTABLE_UPSTREAM)
    artifact.write_text(
        json.dumps({"rules": [{"id": "posix-ported", "regex": ported, "keywords": ["pat"]}]}),
        encoding="utf-8",
    )
    (rule,) = redact._load_gitleaks_subset(artifact)
    assert rule.rule_id == "posix-ported"
    assert rule.pattern.search(AIRTABLE_PAT)


# ---------------------------------------------------------------------------------------------
# LIVENESS — every one of the 120, not just the 45 upstream happened to write a case for
# ---------------------------------------------------------------------------------------------
#
# `tests/data/liveness_samples.json` (written by `scripts/derive_liveness_samples.py`) pins one
# string per vendored rule, derived from that rule's own `re` parse tree. It exists because the
# golden corpus below only reaches the rules upstream hand-wrote cases for: 45 of 120 had ANY test
# anywhere asserting they still match something, and an adversarial review rewrote
# `adobe-client-secret`'s regex to the literal `ZZZ_MATCHES_NOTHING` — and separately widened its
# `{32}` to `{288}` — with the whole suite staying green both times.
#
# This is SHALLOW on purpose. It pins that a rule still matches a shape it was ported for; it says
# nothing about whether that shape is really what the vendor issues. Upstream's own true positives
# stay the primary gate (`VENDOR.md`), and the two are complementary rather than alternatives.


def test_the_liveness_fixture_covers_every_vendored_rule_and_nothing_else():
    """A refresh that ADDS a rule without a sample is a hole, and must be red rather than silent."""
    assert sorted(LIVENESS) == sorted(RULES)
    assert len(LIVENESS) == 120


@pytest.mark.parametrize("rule_id", sorted(LIVENESS))
def test_every_vendored_rule_still_matches_its_pinned_liveness_sample(rule_id):
    """THE dead-rule tripwire: red means this rule now matches nothing it used to.

    Killed outright, narrowed by an edited quantifier, or mis-ported into something unsatisfiable —
    all three land here, and all three are otherwise invisible: a redactor that stops matching
    removes nothing and errors nowhere.
    """
    assert RULES[rule_id].pattern.search(LIVENESS[rule_id]), (
        f"{rule_id} no longer matches the string it was ported to match. If a corpus refresh really "
        f"did change this rule, re-run scripts/derive_liveness_samples.py and READ the diff."
    )


#: `{rule_id: the label that ACTUALLY covers its liveness sample}` — the 12 of 120 samples whose
#: bytes are consumed by an EARLIER pattern before their own rule ever runs, so `[REDACTED:<rule_id>]`
#: never appears for them. Every entry is a documented overlap, not an accident:
#:
#: * the four `github-*` rules and `gcp-api-key` are among the four TIER-ONE redundancies `redact.py`
#:   names on purpose (tier one runs first and wins, keeping this project's label stable across a
#:   corpus refresh — that is the whole point of the redundancy);
#: * `anthropic-*` and `openai-api-key` are `sk-…` shapes, which is exactly what tier one's generic
#:   `api_key` was written to catch;
#: * `kubernetes-secret-yaml`'s hand-written sample is a `password: …` line, which tier one's
#:   `secret_assignment` reaches first;
#: * the three tier-TWO pairs (`flutterwave`, and the two `*-routable` GitLab rules) are upstream
#:   rules whose shapes genuinely nest, resolved by corpus order.
#:
#: Pinned as a TABLE because the assertion below used to be a bare `"[REDACTED:" in out`, which
#: passes for any label at all — so a rule silently falling behind a broader one looked identical to
#: a rule doing its own job. A NEW cross-label now goes red and someone has to decide it is fine.
_COVERED_BY_ANOTHER_RULE = {
    "anthropic-admin-api-key": "api_key",
    "anthropic-api-key": "api_key",
    "flutterwave-secret-key": "flutterwave-encryption-key",
    "gcp-api-key": "google_api_key",
    "github-app-token": "github_token",
    "github-oauth": "github_token",
    "github-pat": "github_token",
    "github-refresh-token": "github_token",
    "gitlab-pat-routable": "gitlab-pat",
    "gitlab-runner-authentication-token-routable": "gitlab-runner-authentication-token",
    "kubernetes-secret-yaml": "secret_assignment",
    "openai-api-key": "api_key",
}


@pytest.mark.parametrize("rule_id", sorted(LIVENESS))
def test_every_liveness_sample_is_redacted_end_to_end(rule_id):
    """Not just "the regex matches" — the rule is WIRED IN and the secret bytes leave the output.

    The keyword gate has to be open for that, which is what makes the two vendor-name-gated rules
    below need their vendor's name prepended. Everything else carries its own keyword already.

    The LABEL is asserted, not merely that something was redacted: `_COVERED_BY_ANOTHER_RULE` says
    which of the 12 overlapping samples is expected to come out under a different rule's name, and
    everything else must carry its own.
    """
    rule = RULES[rule_id]
    sample = LIVENESS[rule_id]
    text = sample if _gate_is_open(rule, sample) else f"{rule.gate[0]} {sample}"
    match = rule.pattern.search(sample)
    assert match is not None
    secret = match.group(1) if rule.secret_is_group_one and match.group(1) else match.group(0)
    out = redact_transcript(text)
    assert secret not in out, f"{rule_id} matched but nothing was redacted"
    expected = _COVERED_BY_ANOTHER_RULE.get(rule_id, rule_id)
    assert f"[REDACTED:{expected}]" in out, (
        f"{rule_id}'s sample came out under neither its own label nor the documented "
        f"{expected!r} overlap: {out!r}"
    )


def test_the_cross_labelled_overlap_table_names_only_real_overlaps():
    """The table is a list of EXCEPTIONS, so a stale entry has to be as loud as a missing one.

    Without this, deleting a rule (or fixing an overlap) would leave a row claiming an overlap that
    no longer exists, and the parametrized assertion above would keep passing on the fallback.
    """
    assert set(_COVERED_BY_ANOTHER_RULE) <= set(LIVENESS)
    for rule_id, covering in _COVERED_BY_ANOTHER_RULE.items():
        rule = RULES[rule_id]
        sample = LIVENESS[rule_id]
        text = sample if _gate_is_open(rule, sample) else f"{rule.gate[0]} {sample}"
        assert f"[REDACTED:{rule_id}]" not in redact_transcript(text), (
            f"{rule_id} now carries its OWN label — delete its row from _COVERED_BY_ANOTHER_RULE "
            f"(it claims {covering!r} covers it)"
        )


def _gate_is_open(rule, text: str) -> bool:
    return not rule.gate or any(keyword in text.lower() for keyword in rule.gate)


def test_exactly_two_rules_are_gated_on_the_vendors_own_name():
    """`redact.py`'s stated false-negative direction, ENUMERATED mechanically instead of by hand.

    A handful of upstream rules are keyed on the vendor's NAME rather than on anything the token's
    own shape implies, so a bare token in a transcript that never names the vendor is never reached.
    That direction was already documented and accepted — but as "a few rules ... `airtable`", which
    is how the second one stayed invisible until an adversarial review found it. A derived sample
    that fails its own gate IS the definition of the class, so deriving the list beats keeping it.
    """
    blind = sorted(rid for rid, sample in LIVENESS.items() if not _gate_is_open(RULES[rid], sample))
    assert blind == ["airtable-personnal-access-token", "facebook-access-token"], (
        "the set of rules reachable only when the transcript names the vendor changed — update "
        "redact.py's keyword-gate commentary, which says it is exactly these two"
    )


# ---------------------------------------------------------------------------------------------
# gitleaks' own true positives
# ---------------------------------------------------------------------------------------------

_TP_CASES = [(rid, tp) for rid, e in sorted(GOLDEN.items()) for tp in e.get("tps", [])]
_FP_REJECTED = [(rid, fp) for rid, e in sorted(GOLDEN.items()) for fp in e.get("fps_rejected", [])]
_FP_OVER = [(rid, fp) for rid, e in sorted(GOLDEN.items()) for fp in e.get("fps_over_redacted", [])]


def test_the_golden_corpus_actually_covers_something():
    assert len(GOLDEN) == 54, "rule ids with hand-written literal cases upstream"
    assert len(_TP_CASES) == 85
    assert len(_FP_REJECTED) == 109
    assert len(_FP_OVER) == 71
    assert set(GOLDEN) <= set(RULES), "the fixture must not name a rule that was never ported"


@pytest.mark.parametrize("rule_id, case", _TP_CASES, ids=[f"{r}-{i}" for i, (r, _c) in enumerate(_TP_CASES)])
def test_every_golden_true_positive_still_matches_its_ported_pattern(rule_id, case):
    assert RULES[rule_id].pattern.search(case), f"{rule_id} stopped matching upstream's own sample"


@pytest.mark.parametrize("rule_id, case", _TP_CASES, ids=[f"{r}-{i}" for i, (r, _c) in enumerate(_TP_CASES)])
def test_every_golden_true_positive_survives_its_own_keyword_gate(rule_id, case):
    """The gate is upstream's; this pins that it never gates away a case upstream calls a hit.

    It is also the honest bound on `redact.py`'s stated false-negative direction: a rule keyed on the
    vendor's NAME (`airtable`) rather than the token's prefix is only reached when the transcript
    names the vendor, and upstream's corpus — whose generated samples embed the identifier — cannot
    show that. What it CAN show is that no case upstream wrote is lost to the gate.
    """
    gate = RULES[rule_id].gate
    assert not gate or any(keyword in case.lower() for keyword in gate)


@pytest.mark.parametrize("rule_id, case", _TP_CASES, ids=[f"{r}-{i}" for i, (r, _c) in enumerate(_TP_CASES)])
def test_every_golden_true_positive_is_redacted_end_to_end(rule_id, case):
    """Not just "the regex matches" — the secret bytes are gone from `redact_transcript`'s output."""
    rule = RULES[rule_id]
    match = rule.pattern.search(case)
    assert match is not None
    secret = match.group(1) if rule.secret_is_group_one and match.group(1) else match.group(0)
    out = redact_transcript(case)
    assert secret not in out
    assert "[REDACTED:" in out


def test_redaction_stays_idempotent_across_both_tiers_over_the_whole_corpus():
    """A placeholder must not itself look like a secret to any of the 127 patterns."""
    blob = "\n".join(case for _rid, case in _TP_CASES)
    once = redact_transcript(blob)
    assert redact_transcript(once) == once


def test_no_tier_two_pattern_matches_a_placeholder():
    placeholders = " ".join(f"[REDACTED:{rule.rule_id}]" for rule in _TIER2)
    placeholders += " " + " ".join(f"[REDACTED:{label}]" for label, _p in _TIER1)
    for rule in _TIER2:
        assert not rule.pattern.search(placeholders), rule.rule_id


# ---------------------------------------------------------------------------------------------
# gitleaks' own false positives — the two buckets, and why they are two
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule_id, case", _FP_REJECTED, ids=[f"{r}-{i}" for i, (r, _c) in enumerate(_FP_REJECTED)]
)
def test_structurally_rejected_false_positives_stay_rejected(rule_id, case):
    """This is what catches a port transformation that WIDENED a rule into meaninglessness."""
    assert not RULES[rule_id].pattern.search(case)


@pytest.mark.parametrize(
    "rule_id, case", _FP_OVER, ids=[f"{r}-{i}" for i, (r, _c) in enumerate(_FP_OVER)]
)
def test_accepted_over_redaction_is_recorded_rather_than_discovered(rule_id, case):
    """gitleaks rejects these via allowlists + entropy floors this project deliberately drops.

    Recording them as a fixed set is the point: a case moving BETWEEN this bucket and
    `fps_rejected` turns a test red and forces a human to look, which is the only guarantee worth
    having over 120 vendored patterns.
    """
    assert RULES[rule_id].pattern.search(case)


def test_entropy_is_carried_for_provenance_and_never_enforced():
    """`redact.py`'s deliberate divergence: a redactor's cost asymmetry is the scanner's, inverted."""
    with_floor = [rule for rule in _TIER2 if rule.entropy is not None]
    assert len(with_floor) > 50, "the floors are loaded"
    # A GitHub PAT of 36 identical characters: shaped exactly right, entropy 0.31 — far below
    # `github-pat`'s upstream floor of 3.0. gitleaks would drop it. A redactor must not.
    low_entropy = "ghp_" + "a" * 36
    pattern = RULES["github-pat"].pattern
    assert RULES["github-pat"].entropy is not None
    assert pattern.search(low_entropy), "the shape is right"
    assert low_entropy not in redact_transcript(f"the token is {low_entropy} ok")


# ---------------------------------------------------------------------------------------------
# The POSIX class: the specific silent mis-port this whole apparatus exists for
# ---------------------------------------------------------------------------------------------

#: gitleaks' `airtable-personnal-access-token` regex, verbatim from `config/gitleaks.toml`.
AIRTABLE_UPSTREAM = r"\b(pat[[:alnum:]]{14}\.[a-f0-9]{64})\b"
AIRTABLE_PAT = "patAb3xY9zQ1mN7kL." + "a1b2c3d4" * 8


def test_the_unported_airtable_regex_compiles_but_means_something_else():
    """The failure mode in the flesh: Python accepts it, with a warning, and it matches nothing."""
    re.purge()
    naive = re.compile(AIRTABLE_UPSTREAM)  # a FutureWarning, not an error
    assert naive.search(AIRTABLE_PAT) is None, "a real Airtable PAT does not match the unported form"


def test_compiling_the_unported_airtable_regex_strictly_is_an_error():
    re.purge()
    with pytest.raises(Warning):
        PORTER.compile_strict(AIRTABLE_UPSTREAM)


def test_the_ported_airtable_rule_matches_a_real_pat():
    assert PORTER.port_regex(AIRTABLE_UPSTREAM) == r"\b(pat[a-zA-Z0-9]{14}\.[a-f0-9]{64})\b"
    assert RULES["airtable-personnal-access-token"].pattern.search(AIRTABLE_PAT)


def test_an_airtable_pat_is_redacted_end_to_end():
    # The rule is gated on the vendor's name, not the token's prefix — `redact.py` documents that
    # known false-negative direction, and this is the realistic case where the gate opens.
    out = redact_transcript(f"my airtable token is {AIRTABLE_PAT} please rotate it")
    assert AIRTABLE_PAT not in out
    assert "[REDACTED:airtable-personnal-access-token]" in out


# ---------------------------------------------------------------------------------------------
# The porter itself
# ---------------------------------------------------------------------------------------------


def test_the_porter_hard_errors_on_an_unknown_posix_class():
    with pytest.raises(ValueError, match=r"unknown POSIX class \[\[:bogus:\]\]"):
        PORTER.port_regex(r"\btok[[:bogus:]]{10}\b")


@pytest.mark.parametrize(
    "source, expected",
    [
        (r"[[:alnum:]]{3}", "[a-zA-Z0-9]{3}"),
        (r"[[:digit:]][[:upper:]]", "[0-9][A-Z]"),
        (r"^abc\z", r"^abc\Z"),
        # Mid-pattern `(?i)` is hoisted — Python 3.11+ refuses a non-leading global flag outright.
        (r"p8e-(?i)[a-z0-9]{32}", "(?i)p8e-[a-z0-9]{32}"),
        (r"(?i)a(?i)b", "(?i)ab"),
    ],
)
def test_the_porters_transformations_are_the_documented_ones(source, expected):
    assert PORTER.port_regex(source) == expected


def test_hoisting_the_inline_flag_widens_rather_than_narrows():
    """The safe direction for a redactor, and the porter's docstring says so — pin it."""
    ported = PORTER.port_regex(r"p8e-(?i)[a-z0-9]{32}")
    assert re.compile(ported).search("P8E-" + "a" * 32), "the prefix became case-insensitive too"


@pytest.mark.parametrize(
    "script", ["port_gitleaks.py", "extract_gitleaks_golden.py", "derive_liveness_samples.py"]
)
def test_every_generator_is_outside_the_write_capability_scan(script):
    """They write files, so they MUST live outside `ctx_distillery/` (CLAUDE.md invariant 1)."""
    from tests.test_no_write_capability import PACKAGE, SOURCES

    generator = ROOT / "scripts" / script
    assert generator.is_file()
    assert generator not in SOURCES
    assert not generator.is_relative_to(PACKAGE)
    assert (ROOT / "ctx_distillery" / "redact.py") in SOURCES, "but redact.py IS scanned"


# ---------------------------------------------------------------------------------------------
# Two tiers, in that order
# ---------------------------------------------------------------------------------------------


def test_tier_one_runs_first_and_owns_the_label_on_an_overlap():
    """`ghp_…` is matched by BOTH tiers; the hand-written label is the one that ships."""
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    assert RULES["github-pat"].pattern.search(token), "tier two would match it too"
    out = redact_transcript(f"token {token} here")
    assert out == "token [REDACTED:github_token] here"


def test_the_hand_written_tier_catches_a_private_proxy_key_the_vendored_corpus_cannot():
    """The measured reason tier one is not redundant (see `redact.py`'s docstring).

    A private-proxy API key is not a known provider format, so an anchored corpus of vendor
    prefixes is structurally blind to it. This exact shape was the operator's own live key on the
    real transcript the two-tier design was measured against.
    """
    key = "sk-1f4c2b7e-9a3d-4f21-8c5e-6b0d7a3e19f2"
    gitleaks_only = key
    for rule in _TIER2:
        gitleaks_only = redact._apply_gitleaks_rule(rule, gitleaks_only)
    assert gitleaks_only == key, "no gitleaks rule knows this shape"
    assert key not in redact_transcript(f"CD_ROOT_LM key is {key}")


@pytest.mark.parametrize(
    "token, label",
    [
        ("sk_live_" + "A1b2C3d4E5f6G7h8I9j0K1l2", "stripe-access-token"),
        ("xoxb-1234567890-1234567890123-" + "A1b2C3d4E5f6G7h8I9j0K1l2", "slack-bot-token"),
        ("npm_" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8", "npm-access-token"),
        ("dapi" + "0123456789abcdef0123456789abcdef", "databricks-api-token"),
        ("glpat-" + "A1b2C3d4E5f6G7h8I9j0", "gitlab-pat"),
        ("hf_" + "abcdefghijklmnopqrstuvwxyzabcdefgh", "huggingface-access-token"),
    ],
)
def test_tier_two_adds_shapes_the_hand_written_tier_never_had(token, label):
    line = f"export TOKEN={token}\n"
    tier_one_only = line
    for tier_one_label, pattern in _TIER1:
        if tier_one_label == redact._ASSIGNMENT_LABEL:
            tier_one_only = pattern.sub(redact._replace_assignment, tier_one_only)
        else:
            tier_one_only = pattern.sub(redact._placeholder(tier_one_label), tier_one_only)
    assert tier_one_only == line, "tier one alone is blind to this shape"

    out = redact_transcript(line)
    assert token not in out
    assert f"[REDACTED:{label}]" in out


def test_a_single_group_rule_keeps_the_delimiter_that_proved_the_token_ended():
    """Upstream runs the match one char past the secret; eating that would corrupt the line."""
    token = "glpat-" + "A1b2C3d4E5f6G7h8I9j0"
    out = redact_transcript(f'gitlab_token = "{token}";')
    assert out == 'gitlab_token = "[REDACTED:gitlab-pat]";'


def test_benign_prose_survives_both_tiers_untouched():
    text = (
        "user: can you refactor the parser?\n"
        "assistant: sure — I'll split tokenize() out of parse() first.\n"
        "user: also the CI job on 3.13 is red, and commit 9d09c20 looks suspicious.\n"
    )
    assert redact_transcript(text) == text


# ---------------------------------------------------------------------------------------------
# The tier-one/tier-two OVERLAP, measured and pinned
# ---------------------------------------------------------------------------------------------
#
# All seven hand-written patterns stay, and four of them are genuinely REDUNDANT with the vendored
# corpus today. That redundancy is DELIBERATE and it is the reason this section exists rather than a
# cleanup someone forgot: tier two is REGENERATED from a moving upstream (`VENDOR.md`'s refresh
# recipe), so a future refresh that renames, narrows or drops `github-pat` would silently reduce
# coverage. Tier one is the floor that survives it.
#
# The table is the tripwire. If a refresh drops one of these rule ids, the corresponding row goes red
# and the finding is stated in the words a reviewer needs — "this shape is now TIER-ONE-ONLY" —
# rather than as nothing at all, which is what an unpinned redundancy produces.

#: `(tier-one label, a sample it matches, the tier-two rule id that ALSO matches it — or None)`.
_OVERLAP: list[tuple[str, str, str | None]] = [
    (
        "private_key",
        "-----BEGIN RSA PRIVATE KEY-----\n"
        + "MIIEowIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyz\n" * 3
        + "-----END RSA PRIVATE KEY-----",
        "private-key",
    ),
    ("aws_access_key_id", "AKIAIOSFODNN7EXAMPLE", "aws-access-token"),
    ("github_token", "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "github-pat"),
    ("google_api_key", "AIza" + "SyD_ExampleNotARealKey-000000000000", "gcp-api-key"),
    # RE2 has no lookbehind, so no gitleaks rule can ever be anchored the way this one is.
    ("bearer_token", "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", None),
    # A private-proxy key is nobody's published format — an anchored vendor corpus is blind to it.
    ("api_key", "sk-1f4c2b7e-9a3d-4f21-8c5e-6b0d7a3e19f2", None),
    # Replacement SEMANTICS, not a shape: every gitleaks rule replaces a token, never a bound value.
    ("secret_assignment", 'password = "hunter2000"', None),
]


def test_the_overlap_table_covers_every_hand_written_pattern():
    # Sorted, not positional: the table is grouped by REDUNDANT-then-tier-one-only, which reads
    # better than `_TIER1`'s own order (which is grouped by what must run before what).
    assert sorted(label for label, _s, _r in _OVERLAP) == sorted(label for label, _p in _TIER1)
    assert sum(1 for _l, _s, rule_id in _OVERLAP if rule_id) == 4, "4 of 7 are redundant today"


@pytest.mark.parametrize("label, sample, rule_id", _OVERLAP, ids=[row[0] for row in _OVERLAP])
def test_each_hand_written_pattern_still_matches_its_own_sample(label, sample, rule_id):
    assert dict(_TIER1)[label].search(sample), f"tier one's {label} stopped matching its own shape"


@pytest.mark.parametrize(
    "label, sample, rule_id",
    [row for row in _OVERLAP if row[2]],
    ids=[row[0] for row in _OVERLAP if row[2]],
)
def test_a_deliberately_redundant_shape_is_still_covered_by_the_vendored_corpus(label, sample, rule_id):
    """Red here means a refresh DROPPED the vendored half — the shape is now tier-one-only.

    That is a finding to record (in `VENDOR.md` and this table), not a test to delete: tier one still
    catches the shape, which is precisely why it was kept.
    """
    assert rule_id in RULES, f"{rule_id} vanished from the vendored corpus — {label} is now alone"
    assert RULES[rule_id].pattern.search(sample), f"{rule_id} no longer matches what {label} matches"


@pytest.mark.parametrize(
    "label, sample, rule_id",
    [row for row in _OVERLAP if not row[2]],
    ids=[row[0] for row in _OVERLAP if not row[2]],
)
def test_a_tier_one_only_shape_is_matched_by_no_vendored_rule_at_all(label, sample, rule_id):
    """The three tier one owns outright. If one of these goes red, tier two GREW into it — also fine,
    also worth knowing, and the row should then move to the redundant half of the table."""
    hits = [rule.rule_id for rule in _TIER2 if rule.pattern.search(sample)]
    assert hits == [], f"{label} is no longer tier-one-only: {hits}"


def test_google_api_key_is_not_fully_redundant_even_where_it_overlaps():
    """The overlap is partial, and the difference is the TERMINATOR — worth pinning explicitly.

    gitleaks' `gcp-api-key` requires the key to be followed by one of a fixed set (backtick, quote,
    whitespace, `;`, `\\n`/`\\r`, or end of text). Tier one ends on `\\b`, so a key followed by a
    bracket is tier-one-only. In a redactor the wider terminator is the correct direction.
    """
    probe = "AIza" + "B" * 35 + "]"
    assert dict(_TIER1)["google_api_key"].search(probe)
    assert not RULES["gcp-api-key"].pattern.search(probe)
    assert "AIza" not in redact_transcript(f"key {probe} here")
