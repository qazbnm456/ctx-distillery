"""Synthesise strings a regex would match, by walking its own `re` PARSE TREE.

ONE implementation, two callers with different questions (CLAUDE.md invariant 11's "one
implementation per job" — this used to live only in `scripts/derive_liveness_samples.py`, and
`redact.py` needing the same walk is what forced the move into the package):

* `sample_for(source)` — a COMPLETE string the pattern should match. Used by
  `scripts/derive_liveness_samples.py` to generate `tests/data/liveness_samples.json`, the fixture
  that catches a vendored gitleaks rule going dead.
* `reaching_prefixes(source, ...)` — every concrete PREFIX that walks the pattern up to one of its
  ambiguous (unbounded) quantifiers. Used by `redact.py`'s ReDoS calibration: a probe made of
  repeated filler characters never spells a rule's own literal marker, so the match fails at
  position 0 and the catastrophic tail behind the marker is never timed at all. See `_probes`.

Both walk `re._parser.parse()`, which is a PRIVATE stdlib module. That is a deliberate, stated
dependency rather than a hidden one, and it is imported PLAINLY — no try/except fallback — for the
same reason `redact._load_gitleaks_subset` is deliberately not defensive about a missing artifact:
this module feeds a REDACTOR's safety check, and a safety check that silently degrades to "no
prefixes, everything passes calibration" is the exact failure mode the whole feature exists to
avoid. `pyproject.toml` pins `requires-python = ">=3.11"`, where both names exist.

Stdlib only, offline, no new dependency, and NOTHING here writes — `redact.py` imports it, so it is
inside `tests/test_no_write_capability.py`'s mutation scan.
"""

from __future__ import annotations

import re
import re._constants as sre
import re._parser as sre_parse

#: Tried in order, so a derived string prefers boring alphanumerics and only reaches for punctuation
#: when the character class demands it. The tail is every printable ASCII plus the two line breaks,
#: so a negated or exotic class still finds something.
_ALPHABET: tuple[str, ...] = tuple(
    dict.fromkeys([*"abc01AZx_-.+/=~@ \t", *(chr(code) for code in range(32, 127)), "\n", "\r"])
)

_CATEGORY = {
    sre.CATEGORY_DIGIT: lambda ch: ch.isascii() and ch.isdigit(),
    sre.CATEGORY_NOT_DIGIT: lambda ch: not (ch.isascii() and ch.isdigit()),
    sre.CATEGORY_SPACE: lambda ch: ch in " \t\n\r\f\v",
    sre.CATEGORY_NOT_SPACE: lambda ch: ch not in " \t\n\r\f\v",
    sre.CATEGORY_WORD: lambda ch: ch.isalnum() or ch == "_",
    sre.CATEGORY_NOT_WORD: lambda ch: not (ch.isalnum() or ch == "_"),
}

#: The three repeat opcodes. `re` uses a distinct one per quantifier flavour (greedy / lazy /
#: possessive) but the SHAPE both walks care about — `(low, high, subpattern)` — is identical.
_REPEATS = (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT)


class Underivable(Exception):
    """The walk cannot produce a string for this construct — the caller degrades, never guesses."""


def _in_matches(items: list, ch: str) -> bool:
    """Whether one character satisfies a parsed `[...]` class, negation included."""
    negate = False
    hit = False
    for op, av in items:
        if op is sre.NEGATE:
            negate = True
        elif op is sre.LITERAL:
            hit = hit or ord(ch) == av
        elif op is sre.RANGE:
            hit = hit or av[0] <= ord(ch) <= av[1]
        elif op is sre.CATEGORY:
            hit = hit or _CATEGORY[av](ch)
        else:
            raise Underivable(f"character-class item {op}")
    return hit != negate


def pick_in(items: list) -> str:
    """The first alphabet character satisfying a parsed `[...]` class."""
    for ch in _ALPHABET:
        if _in_matches(items, ch):
            return ch
    raise Underivable("no alphabet character satisfies the class")


def emit(sequence, groups: dict[int, str]) -> str:
    """A COMPLETE string for one parsed sequence — every quantifier taken its minimum number of
    times. Never validated here; `sample_for`'s callers check the result against the real pattern.
    """
    out: list[str] = []
    for op, av in sequence:
        if op is sre.LITERAL:
            out.append(chr(av))
        elif op is sre.NOT_LITERAL:
            out.append(next(ch for ch in _ALPHABET if ord(ch) != av))
        elif op is sre.ANY:
            # A literal dot, not a filler letter. An unescaped `.` in a ported vendor regex is
            # overwhelmingly a hostname separator gitleaks never escaped (`hooks.slack.com`,
            # `gems.contribsys.com`, `xoxe.xoxb-`), and `.` satisfies `.` either way — so this keeps
            # the derived sample carrying the rule's own keyword instead of `hooksxslackxcom`.
            out.append(".")
        elif op is sre.IN:
            out.append(pick_in(av))
        elif op in _REPEATS:
            low, _high, sub = av
            out.append(emit(sub, groups) * low)
        elif op is sre.SUBPATTERN:
            group_id, _add, _del, sub = av
            body = emit(sub, groups)
            if group_id is not None:
                groups[group_id] = body
            out.append(body)
        elif op is sre.ATOMIC_GROUP:
            out.append(emit(av, groups))
        elif op is sre.BRANCH:
            _none, alternatives = av
            for alternative in alternatives:
                try:
                    out.append(emit(alternative, groups))
                    break
                except Underivable:
                    continue
            else:
                raise Underivable("no alternative of a branch could be derived")
        elif op is sre.GROUPREF:
            out.append(groups.get(av, ""))
        elif op is sre.AT:
            pass  # `^`, `$`, `\b`: zero-width, and the caller VALIDATES the result against the regex
        else:
            raise Underivable(str(op))
    return "".join(out)


def sample_for(source: str) -> str:
    """A string the pattern SHOULD match, built by walking its parse tree. Never validated here."""
    return emit(sre_parse.parse(source), {})


# --------------------------------------------------------------------------------------------
# Reaching prefixes — the ReDoS calibration's half of the walk.
# --------------------------------------------------------------------------------------------

#: Default cap on how many prefixes one pattern may contribute. UNBOUNDED prefix derivation is a
#: load-cost bug, not a theoretical one: an adversarial review's 7,919-alternative pattern made the
#: old textual run-scraper emit 1,014,880 probes and a 16.8 s import. Every prefix costs
#: `len(_PROBE_LENGTHS) * len(_RUN_FILLERS) * len(_PROBE_TAILS)` probes downstream, so this is the
#: constant that makes the AGGREGATE load cost bounded rather than only the per-probe one.
DEFAULT_PREFIX_LIMIT = 32

#: Default cap on one prefix's length. A pattern whose fixed head is longer than this is not a
#: "marker plus a quantified tail" shape at all, and a probe is only 32 characters of filler.
DEFAULT_PREFIX_CHARS = 256


class _PrefixWalk:
    """Accumulates `(prefix reaching an ambiguous quantifier)` snapshots over one parse tree.

    `current` is a LIST of prefixes rather than one string because a `BRANCH` forks: `foo|BAR-`
    has to contribute both `foo` and `BAR-`, since either could be the marker in front of the
    quantified tail. Every list is capped at `limit` for the reason `DEFAULT_PREFIX_LIMIT` gives.
    """

    def __init__(self, limit: int, max_chars: int) -> None:
        self.limit = limit
        self.max_chars = max_chars
        self.found: list[str] = []
        self.groups: dict[int, str] = {}

    def record(self, texts: list[str]) -> None:
        """Snapshot prefixes as probe candidates. The empty string is skipped — it would only
        duplicate the plain seed probes the grid already emits."""
        for text in texts:
            if text and text not in self.found and len(self.found) < self.limit:
                self.found.append(text)

    def extend(self, current: list[str], text: str) -> list[str]:
        if not text:
            return current
        out: list[str] = []
        for prefix in current:
            grown = prefix + text
            if len(grown) > self.max_chars:
                raise Underivable(f"prefix would exceed {self.max_chars} characters")
            out.append(grown)
        return out

    def walk(self, sequence, current: list[str]) -> list[str]:
        """Advance `current` through `sequence`, recording a snapshot at every ambiguous repeat.

        A construct the walk cannot derive records THE PREFIX REACHED SO FAR before it propagates:
        `(a)CORP-(?(1)a|b)(\\w+\\s?)+$` still contributes `aCORP-`, which is the whole point — the
        marker in front of the underivable part is exactly what a probe needs to spell.
        """
        for op, av in sequence:
            if not current:
                return current
            try:
                current = self._step(op, av, current)
            except Underivable:
                self.record(current)
                raise
        return current

    def _step(self, op, av, current: list[str]) -> list[str]:
        if op is sre.LITERAL:
            return self.extend(current, chr(av))
        if op is sre.NOT_LITERAL:
            return self.extend(current, next(ch for ch in _ALPHABET if ord(ch) != av))
        if op is sre.ANY:
            return self.extend(current, ".")
        if op is sre.IN:
            return self.extend(current, pick_in(av))
        if op in _REPEATS:
            return self._repeat(av, current)
        if op is sre.SUBPATTERN:
            group_id, _add, _del, sub = av
            before = current
            current = self.walk(sub, current)
            # The group's OWN text is the delta this sub-walk appended — recovered rather than
            # tracked separately, and only when the walk really did extend the first candidate
            # (a BRANCH inside can reorder, in which case a backreference just derives empty).
            if group_id is not None and current and current[0].startswith(before[0]):
                self.groups[group_id] = current[0][len(before[0]) :]
            return current
        if op is sre.ATOMIC_GROUP:
            return self.walk(av, current)
        if op is sre.BRANCH:
            return self._branch(av, current)
        if op is sre.GROUPREF:
            return self.extend(current, self.groups.get(av, ""))
        if op is sre.AT:
            return current  # `^`, `$`, `\b` — zero-width
        if op is sre.ASSERT:
            direction, sub = av
            # A LOOKBEHIND's text genuinely does precede the match, so emitting it is what makes
            # `(?<=authorization:\s)…` reachable at all. A LOOKAHEAD is re-consumed by whatever
            # follows, so emitting it would double the text — skipped, and the honest cost is that
            # a pattern gated purely on a lookahead gets a prefix that does not match.
            return self.walk(sub, current) if direction < 0 else current
        if op is sre.ASSERT_NOT:
            return current
        raise Underivable(str(op))

    def _repeat(self, av, current: list[str]) -> list[str]:
        low, high, sub = av
        if high >= sre.MAXREPEAT:
            # THE AMBIGUOUS TAIL — precisely what a probe's filler ladder has to feed. Snapshot the
            # prefix that reaches it, then carry on with the MINIMUM number of body copies so a
            # second marker further right (`\d+-CORP-(\w+\s?)+$`) is still reachable too.
            self.record(current)
        if low > self.max_chars:
            raise Underivable(f"a repeat of {low} is longer than a prefix may be")
        # Walked at least once even when `low` is 0, so an OPTIONAL group's own nested ambiguity is
        # still recorded; the advance is then discarded, because the minimal match skips it.
        out = current
        for _ in range(max(low, 1)):
            out = self.walk(sub, out)
        return out if low else current

    def _branch(self, av, current: list[str]) -> list[str]:
        _none, alternatives = av
        merged: list[str] = []
        for alternative in alternatives:
            try:
                produced = self.walk(alternative, current)
            except Underivable:
                continue
            for text in produced:
                if text not in merged:
                    merged.append(text)
            if len(merged) >= self.limit:
                break
        if not merged:
            raise Underivable("no alternative of a branch could be derived")
        return merged[: self.limit]


def reaching_prefixes(
    source: str,
    *,
    limit: int = DEFAULT_PREFIX_LIMIT,
    max_chars: int = DEFAULT_PREFIX_CHARS,
) -> list[str]:
    """Concrete prefixes that walk `source` up to one of its ambiguous quantifiers.

    The point is REACHABILITY, not matching: `redact._probes` appends its own filler ladder to each
    of these, so what matters is that the scanner gets past the pattern's fixed head and starts
    backtracking in the quantified tail behind it.

    Derived from the PARSE TREE rather than by scraping literal runs out of the pattern text, which
    is what closes three evasions the textual scraper could not spell — reproduced through the real
    loader before this was written:

    * a marker SHORTER than the old 3-character minimum (`X-(\\w+\\s?)+$` passed in 0.4 ms, then
      hung for >15 s on a 46-character line);
    * a marker containing a CHARACTER CLASS (`ORG-[0-9]{4}-(\\w+\\s?)+$` — the scraped run was
      `ORG-`, and no filler spells `1234-`);
    * a marker built from anything else the tree knows and text does not (a backreference, a
      bounded repeat of a group, a lookbehind).

    Degrades to the prefixes found SO FAR — never raises — when the walk meets a construct it
    cannot derive (`(?(1)a|b)`, a class no alphabet character satisfies, a pattern too deep to
    recurse through). That is the safe direction for a calibration heuristic: fewer probes, and the
    two timing gates still run on everything the seed alphabet produces.
    """
    try:
        parsed = sre_parse.parse(source)
    except (re.error, RecursionError):  # pragma: no cover — callers compile before calibrating
        return []
    walk = _PrefixWalk(limit, max_chars)
    try:
        tail = walk.walk(parsed, [""])
    except (Underivable, RecursionError):
        return walk.found
    # The END of the pattern is a reaching prefix too: for a fully BOUNDED pattern (`AIza[\w-]{35}`)
    # it is the only one there is, and it is the string a real match would consume.
    walk.record(tail)
    return walk.found
