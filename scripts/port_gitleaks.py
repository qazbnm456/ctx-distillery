"""Mechanically port gitleaks' ANCHORED rules from Go/RE2 to Python `re`.

This script generates `ctx_distillery/patterns/gitleaks_subset.json` — tier two of
`ctx_distillery/redact.py`. It is checked in so the generated artifact stays REVIEWABLE: every
transformation below is mechanical and readable, and a reader can re-run it against the pinned
upstream config and diff the result. See `VENDOR.md` for the pin, the licence, and the refresh
recipe.

    python scripts/port_gitleaks.py path/to/gitleaks.toml

Four transformations, all mechanical:

1. POSIX bracket classes (`[[:alnum:]]`) -> explicit ranges. Python's `re` has no POSIX classes.
   An UNKNOWN class name is a HARD ERROR, never a pass-through — see `compile_strict` for why.
2. `\\z` (Go: absolute end of text) -> `\\Z` (Python: same meaning).
3. Mid-pattern `(?i)` -> hoisted to a leading global flag. Python 3.11+ REFUSES a non-leading
   global flag outright, so this is required, not cosmetic. It WIDENS the match (the part of the
   pattern before the original `(?i)` becomes case-insensitive too). For a REDACTOR that is the
   safe direction: a wider pattern over-redacts, a narrower one leaks.
4. Filter to "shape A" — drop every rule containing gitleaks' sliding keyword prefix
   `[\\w.-]{0,50}?`. Those are the ~101 generic "keyword near an assignment" rules; they are 92%
   of the naive scan cost and they need gitleaks' 1,446-entry stopword allowlist to avoid
   shredding a transcript that is literally made of the words `key`, `token` and `secret`.
   VENDOR.md records that exclusion. What survives is anchored on the credential's OWN literal
   shape (`sk-ant-api03-`, `ghp_`, `AKIA`, ...).

The single most important line in this file is `warnings.simplefilter("error")` in
`compile_strict`. gitleaks' `airtable-personnal-access-token` rule is
`\\b(pat[[:alnum:]]{14}\\.[a-f0-9]{64})\\b`. Fed to Python unported it COMPILES — with nothing but
a `FutureWarning: Possible nested set` — and then means something entirely different:
`[[:alnum:]]{14}` parses as the character set `[[:alnum]]` followed by 14 literal `]`, so a real
Airtable personal-access token never matches it. In a scanner that is a missed finding. In a
REDACTOR it is a live credential flowing into a language model's context with no error anywhere.
Compiling under `error` turns that class of silent mis-port into a build failure.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
import warnings
from pathlib import Path

#: gitleaks' sliding keyword prefix. Its presence is what makes a rule "shape B" (generic,
#: keyword-near-assignment) rather than "shape A" (anchored on the token's own literal shape).
SLIDING_PREFIX = r"[\w.-]{0,50}?"

#: POSIX bracket-class expansions. Deliberately explicit and deliberately CLOSED — an unlisted
#: name raises rather than being passed through as a literal.
POSIX_CLASSES: dict[str, str] = {
    "alnum": "a-zA-Z0-9",
    "alpha": "a-zA-Z",
    "ascii": r"\x00-\x7f",
    "blank": r" \t",
    "cntrl": r"\x00-\x1f\x7f",
    "digit": "0-9",
    "graph": r"\x21-\x7e",
    "lower": "a-z",
    "print": r"\x20-\x7e",
    "punct": r"!-/:-@\[-`{-~",
    "space": r"\s",
    "upper": "A-Z",
    "word": r"\w",
    "xdigit": "0-9a-fA-F",
}

_POSIX_RE = re.compile(r"\[:(\w+):\]")


def port_regex(source: str) -> str:
    """Return the Python-`re` equivalent of a Go/RE2 gitleaks pattern.

    Raises `ValueError` on an unknown POSIX class — the one construct where guessing would produce
    a pattern that compiles and silently matches the wrong thing.
    """

    def _expand(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in POSIX_CLASSES:
            raise ValueError(f"unknown POSIX class [[:{name}:]] — refusing to guess")
        return POSIX_CLASSES[name]

    ported = _POSIX_RE.sub(_expand, source)
    ported = ported.replace(r"\z", r"\Z")
    if "(?i)" in ported:
        # Hoist every occurrence to a single leading flag (Python 3.11+ rejects a non-leading one).
        ported = "(?i)" + ported.replace("(?i)", "")
    return ported


def compile_strict(pattern: str) -> re.Pattern[str]:
    """Compile with every warning promoted to an error — see this module's docstring."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return re.compile(pattern)


def port_rules(config: dict) -> tuple[list[dict], list[tuple[str, str]]]:
    """Port every shape-A rule in a parsed `gitleaks.toml`. Returns `(ported, skipped)`."""
    ported: list[dict] = []
    skipped: list[tuple[str, str]] = []
    re.purge()  # `re.compile` caches by (pattern, flags); a cache hit would skip the strict parse
    for rule in config["rules"]:
        rule_id = rule["id"]
        source = rule.get("regex")
        if source is None:
            skipped.append((rule_id, "path-only rule, no regex"))
            continue
        try:
            pattern = port_regex(source)
        except ValueError as exc:
            skipped.append((rule_id, str(exc)))
            continue
        try:
            compile_strict(pattern)
        except (re.error, Warning) as exc:
            skipped.append((rule_id, f"{type(exc).__name__}: {exc}"))
            continue
        if SLIDING_PREFIX in pattern:
            skipped.append((rule_id, "shape B (generic keyword-near-assignment rule)"))
            continue
        entry: dict = {
            "id": rule_id,
            "regex": pattern,
            "keywords": [str(k) for k in rule.get("keywords", [])],
        }
        # Carried for PROVENANCE only. `redact.py` deliberately does NOT enforce gitleaks' entropy
        # floors — the argument is in that module, next to the code that ignores them.
        if rule.get("entropy") is not None:
            entry["entropy"] = rule["entropy"]
        ported.append(entry)
    ported.sort(key=lambda e: e["id"])
    return ported, skipped


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    toml_path = Path(argv[1]).resolve()
    out_path = Path(__file__).resolve().parent.parent / "ctx_distillery" / "patterns" / "gitleaks_subset.json"

    with toml_path.open("rb") as handle:
        raw = handle.read()
    config = tomllib.loads(raw.decode("utf-8"))
    ported, skipped = port_rules(config)

    document = {
        "_source": "https://github.com/gitleaks/gitleaks — config/gitleaks.toml",
        "_licence": "MIT, Copyright (c) 2019 Zachary Rice (see VENDOR.md)",
        "_generated_by": "scripts/port_gitleaks.py — DO NOT HAND-EDIT",
        "_upstream_rules": len(config["rules"]),
        "rules": ported,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=1, sort_keys=False) + "\n", encoding="utf-8")

    print(f"{toml_path}: {len(config['rules'])} upstream rules")
    print(f"  ported (shape A) : {len(ported)}")
    print(f"  skipped          : {len(skipped)}")
    for reason in sorted({r for _id, r in skipped}):
        print(f"      {sum(1 for _i, r in skipped if r == reason):4d}  {reason}")
    print(f"  wrote {out_path.relative_to(Path.cwd()) if out_path.is_relative_to(Path.cwd()) else out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - a developer-run generator
    raise SystemExit(main(sys.argv))
