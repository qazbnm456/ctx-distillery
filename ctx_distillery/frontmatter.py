"""YAML-frontmatter parsing for memory/skill files — the real, nested-capable parser.

`rlm_harness.skills`'s own frontmatter reader is deliberately minimal: it handles FLAT lowercase
`key: value` lines and falls back to the filename when `name` is absent. That is fine for the
Agent-Skills shape it governs (`name`/`description` only), but it CANNOT express the Claude Code
memory-file schema this project validates against, whose `metadata.type` is a NESTED key. Reusing
it for memory-file validation would silently read `metadata: {type: user}` as an opaque string and
pass a malformed draft.

So ctx-distillery owns this one small module (dspy-free, rlm-harness-free, `pyyaml`-backed) and uses it
in BOTH places frontmatter is read: the Claude Code adapter (parsing files that already exist) and
the drafting tools' validators (parsing a candidate draft the model just produced). The flat skill
shape is handled as a degenerate case — no nesting needed there, same parser.
"""

from __future__ import annotations

from typing import Any

import yaml

_DELIM = "---"


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Split `text` into `(frontmatter_dict, body)`.

    Frontmatter is a leading `---` line, a YAML block, and a closing `---` line. Returns
    `({}, text)` verbatim when there is no frontmatter at all, when the block never closes, when
    the YAML fails to parse, or when it parses to something other than a mapping — a malformed
    draft is a *validation* outcome for the caller to report, never an exception thrown into the
    REPL from here.

    **Leading BLANK lines are skipped, and that is a deliberate loosening found by a live run.** The
    opening delimiter used to have to be the very first line, on the reasoning that tolerating blank
    lines would let a body's own `---` horizontal rule look like frontmatter. The cost of that
    strictness turned out to be wildly asymmetric: a real drafting call returned
    `'\\n\\n---\\nname: ...'` — a complete, correct memory file — and it was rejected with "no
    parseable YAML frontmatter" for two leading newlines, which is a shape language models emit
    constantly. The risk it was guarding against needs a document whose first non-blank line is a
    horizontal rule, AND a second `---` later, AND the text between them to parse as a YAML mapping;
    a body that opens with a horizontal rule is already pathological. Only genuinely EMPTY lines are
    skipped: a line carrying any content still means there is no frontmatter, so a body that merely
    CONTAINS a `---` cannot be reinterpreted as one. (An INDENTED delimiter has always been accepted,
    because the comparison strips the line — that is pre-existing behaviour, unchanged here, and
    stated only because an earlier draft of this docstring claimed the opposite and a test caught it.)
    """
    if not text:
        return {}, ""
    lines = text.splitlines(keepends=True)
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != _DELIM:
        return {}, text
    for index in range(start + 1, len(lines)):
        if lines[index].strip() != _DELIM:
            continue
        block = "".join(lines[start + 1:index])
        body = "".join(lines[index + 1:])
        try:
            loaded = yaml.safe_load(block)
        except yaml.YAMLError:
            return {}, text
        return (loaded if isinstance(loaded, dict) else {}), body
    # Unterminated frontmatter — treat the whole thing as body rather than guessing where it ends.
    return {}, text
