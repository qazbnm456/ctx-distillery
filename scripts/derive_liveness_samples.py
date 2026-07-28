"""Derive one MATCHING string per vendored gitleaks rule, from the rule's own `re` parse tree.

This script generates `tests/data/liveness_samples.json` — the fixture behind
`tests/test_redact_golden.py`'s liveness tests. Run it after a corpus refresh:

    python scripts/derive_liveness_samples.py

WHY IT EXISTS. `tests/data/gitleaks_golden.json` is scraped from upstream's own hand-written
`tps`/`fps` blocks, and upstream only writes those for the rules it felt like writing them for. Of
the 120 rules ported into `ctx_distillery/patterns/gitleaks_subset.json`, the corpus carries a case
of ANY kind for 54, and only 40 of those cases include a string the rule actually MATCHES (a `tps`
entry or an `fps_over_redacted` one) — just 17 rules have an upstream TRUE POSITIVE. So 80 of the
120 have no test anywhere asserting that they still match something. (An earlier version of this
paragraph said "only 45", which corresponds to none of those counts; a third adversarial review
found the number matched nothing computable.) An adversarial review demonstrated what that
costs — rewriting `adobe-client-secret`'s regex to the literal `ZZZ_MATCHES_NOTHING`, and separately
widening its `{32}` to `{288}`, each left the whole suite green. A vendored redaction rule that
quietly stops matching is invisible by construction: nothing is redacted, and nothing errors.

WHAT IT PROVES, AND WHAT IT DOES NOT. A pinned sample catches the DEAD-RULE class — "this pattern
no longer matches the shape it was ported for" — whether the rule was killed outright, narrowed, or
mis-ported into something unsatisfiable. It proves NOTHING about semantic fidelity to the vendor's
real credential format; only upstream's own true positives do that, and they stay the primary gate
(`VENDOR.md`). The two are complementary: the golden corpus is deep over the 54 rules it covers,
this is shallow over all 120.

WHY THE SAMPLES ARE CHECKED IN rather than derived inside the test. A sample derived at test time
from the rule's OWN pattern moves with the rule: narrow `{32}` to `{288}` and the derived sample
grows to 288 characters and still matches, which is exactly the mutation that has to go red. Pinning
the sample makes the rule's behaviour a fixed target. It also means a corpus REFRESH that changes a
rule shows up as a fixture diff a human has to look at — the same stance `tests/test_redact_golden.py`
takes for upstream's false positives, and the only guarantee worth having over 120 vendored patterns.

The derivation walks `re._parser.parse()`, which is a PRIVATE stdlib module. The walk itself lives
in `ctx_distillery/regex_walk.py`, NOT here: `redact.py`'s ReDoS calibration needs the same tree to
derive a probe's reaching prefix, and one implementation per job (CLAUDE.md invariant 11) beats two
copies of a parse-tree walker. This script keeps only what is its own — the hand-written exceptions,
the validation loop, and the fixture it writes. Stdlib only, offline, no new dependency — the same
constraints `redact.py` works under.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctx_distillery.regex_walk import Underivable, sample_for

#: The rules the walk cannot do, with a sample written by hand instead. `kubernetes-secret-yaml`
#: needs real YAML: its `\bsecret\b` and `\bdata:` anchors sit either side of a lazy `(?s:.){0,200}?`
#: that the walk expands to nothing, which glues `secret` to `data` and kills the word boundary.
_HAND_WRITTEN = {
    "kubernetes-secret-yaml": "kind: Secret\ndata:\n  password: cGFzc3dvcmQxMjM=\n",
}


def derive(entries: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Return `(samples, underivable_ids)`, validating every sample against its own pattern."""
    samples: dict[str, str] = {}
    stuck: list[str] = []
    for entry in entries:
        rule_id = entry["id"]
        candidate = _HAND_WRITTEN.get(rule_id)
        if candidate is None:
            try:
                candidate = sample_for(entry["regex"])
            except Underivable:
                candidate = None
        if candidate is None or not re.compile(entry["regex"]).search(candidate):
            stuck.append(rule_id)
            continue
        samples[rule_id] = candidate
    return samples, stuck


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    subset = json.loads((root / "ctx_distillery" / "patterns" / "gitleaks_subset.json").read_text())
    out_path = root / "tests" / "data" / "liveness_samples.json"

    samples, stuck = derive(subset["rules"])
    if stuck:
        print(f"NO SAMPLE for {len(stuck)} rule(s); add them to _HAND_WRITTEN:", file=sys.stderr)
        for rule_id in stuck:
            print(f"    {rule_id}", file=sys.stderr)
        return 1

    document = {
        "_generated_by": "scripts/derive_liveness_samples.py — DO NOT HAND-EDIT",
        "_what_it_pins": (
            "each vendored rule still MATCHES a string it was ported to match. Not semantic "
            "fidelity to the vendor's format — upstream's own true positives are that gate."
        ),
        "_hand_written": sorted(_HAND_WRITTEN),
        "samples": {rule_id: samples[rule_id] for rule_id in sorted(samples)},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=1, sort_keys=False) + "\n", encoding="utf-8")

    print(f"{len(samples)} samples ({len(_HAND_WRITTEN)} hand-written) -> {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - a developer-run generator
    raise SystemExit(main(sys.argv))
