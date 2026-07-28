"""Scrape gitleaks' OWN true/false-positive cases out of its Go rule generator.

This produces `tests/data/gitleaks_golden.json`, the fixture `tests/test_redact_golden.py` runs the
ported patterns against. It is what makes the vendored subset TRUSTWORTHY rather than hopeful: the
expectations come from upstream's own regression corpus, not from cases written to match whatever
the port happened to produce.

    python scripts/extract_gitleaks_golden.py path/to/gitleaks/cmd/generate/config/rules

Each rule's generator function carries `tps := []string{...}` / `fps := []string{...}` blocks. Only
elements that are a PURE literal (a Go interpreted or raw string, possibly `+`-concatenated with
other literals) are kept — gitleaks also builds cases by calling `secrets.NewSecret(...)` to make a
random token, and re-implementing its generator here would mean testing this script's idea of a
secret rather than upstream's hand-written regression cases. Those are dropped, loudly counted.

The false-positive side is partitioned, and the partition is the POINT:

* `fps_rejected` — the ported pattern does NOT match. Assert it stays that way: this is what
  catches a port transformation that silently WIDENS a rule into meaninglessness.
* `fps_over_redacted` — the ported pattern DOES match. gitleaks rejects these via machinery this
  project deliberately does not port: its global allowlists and its per-rule entropy floors (see
  `redact.py` for why a redactor drops the floors). Recording them keeps the divergence explicit
  and reviewable — a case MOVING between the two buckets turns the test red and forces a human to
  look, which is the only guarantee worth having here.

Written by a human-run script, never at test time; the fixture is checked in. See VENDOR.md.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_RULE_ID = re.compile(r'RuleID:\s*"([^"]+)"')
_BLOCK = re.compile(r"\b(tps|fps)\s*:?=\s*\[\]string\{(.*?)\n\t\}", re.DOTALL)
_LITERAL = re.compile(r'^"((?:[^"\\]|\\.)*)"$|^`([^`]*)`$', re.DOTALL)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'"}


def go_unquote(text: str) -> str:
    """Decode a Go interpreted-string literal body."""
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                i += 2
                continue
            if nxt == "x" and i + 3 < len(text):
                out.append(chr(int(text[i + 2 : i + 4], 16)))
                i += 4
                continue
            if nxt == "u" and i + 5 < len(text):
                out.append(chr(int(text[i + 2 : i + 6], 16)))
                i += 6
                continue
        out.append(char)
        i += 1
    return "".join(out)


def split_elements(body: str) -> list[str]:
    """Split a Go composite-literal body into its top-level, comma-separated elements."""
    elements: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    while i < len(body):
        char = body[i]
        if char == '"':
            j = i + 1
            while j < len(body):
                if body[j] == "\\":
                    j += 2
                    continue
                if body[j] == '"':
                    break
                j += 1
            current.append(body[i : j + 1])
            i = j + 1
            continue
        if char == "`":
            j = body.index("`", i + 1)
            current.append(body[i : j + 1])
            i = j + 1
            continue
        if body[i : i + 2] == "//":
            newline = body.find("\n", i)
            i = len(body) if newline < 0 else newline
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            elements.append("".join(current))
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    elements.append("".join(current))
    return [e.strip() for e in elements if e.strip()]


def literal_value(element: str) -> str | None:
    """Return the element's value if it is a literal (or a concatenation of literals), else None."""
    parts: list[str] = []
    for piece in re.split(r"\s*\+\s*", element.strip()):
        match = _LITERAL.match(piece.strip())
        if match is None:
            return None
        parts.append(go_unquote(match.group(1)) if match.group(1) is not None else match.group(2))
    return "".join(parts)


def parse_go_source(source: str) -> dict[str, dict[str, list[str]]]:
    cases: dict[str, dict[str, list[str]]] = {}
    for chunk in re.split(r"\nfunc ", source):
        found = _RULE_ID.search(chunk)
        if found is None:
            continue
        rule_id = found.group(1)
        for kind, body in _BLOCK.findall(chunk):
            values = [v for v in (literal_value(e) for e in split_elements(body)) if v is not None]
            if values:
                cases.setdefault(rule_id, {}).setdefault(kind, []).extend(values)
    return cases


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    rules_dir = Path(argv[1]).resolve()
    root = Path(__file__).resolve().parent.parent
    subset = json.loads((root / "ctx_distillery" / "patterns" / "gitleaks_subset.json").read_text())
    ported = {rule["id"]: re.compile(rule["regex"]) for rule in subset["rules"]}

    scraped: dict[str, dict[str, list[str]]] = {}
    for path in sorted(rules_dir.glob("*.go")):
        for rule_id, kinds in parse_go_source(path.read_text(errors="replace")).items():
            for kind, values in kinds.items():
                scraped.setdefault(rule_id, {}).setdefault(kind, []).extend(values)

    fixture: dict[str, dict[str, list[str]]] = {}
    for rule_id, pattern in sorted(ported.items()):
        kinds = scraped.get(rule_id)
        if not kinds:
            continue
        entry: dict[str, list[str]] = {}
        if kinds.get("tps"):
            entry["tps"] = kinds["tps"]
        rejected = [f for f in kinds.get("fps", []) if not pattern.search(f)]
        over = [f for f in kinds.get("fps", []) if pattern.search(f)]
        if rejected:
            entry["fps_rejected"] = rejected
        if over:
            entry["fps_over_redacted"] = over
        if entry:
            fixture[rule_id] = entry

    out_path = root / "tests" / "data" / "gitleaks_golden.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    counts = {k: sum(len(v.get(k, [])) for v in fixture.values()) for k in
              ("tps", "fps_rejected", "fps_over_redacted")}
    print(f"{rules_dir}: scraped literal cases for {len(scraped)} rule ids")
    print(f"  kept (rule id is in the ported shape-A subset): {len(fixture)}")
    for key, total in counts.items():
        print(f"      {key:18s} {total}")
    print(f"  wrote {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - a developer-run generator
    raise SystemExit(main(sys.argv))
