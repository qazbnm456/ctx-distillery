"""YAML-frontmatter parsing for memory/skill files — the real, nested-capable parser.

`rlm_kit.skills`'s own frontmatter reader is deliberately minimal: it handles FLAT lowercase
`key: value` lines and falls back to the filename when `name` is absent. That is fine for the
Agent-Skills shape it governs (`name`/`description` only), but it CANNOT express the Claude Code
memory-file schema this project validates against, whose `metadata.type` is a NESTED key. Reusing
it for memory-file validation would silently read `metadata: {type: user}` as an opaque string and
pass a malformed draft.

So ctx-distillery owns this one small module (dspy-free, rlm-kit-free, `pyyaml`-backed) and uses it
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
    """
    if not text:
        return {}, ""
    lines = text.splitlines(keepends=True)
    # The opening delimiter must be the very first line (leading blank lines included would make
    # a body's own `---` horizontal rule look like frontmatter).
    if lines[0].strip() != _DELIM:
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() != _DELIM:
            continue
        block = "".join(lines[1:index])
        body = "".join(lines[index + 1:])
        try:
            loaded = yaml.safe_load(block)
        except yaml.YAMLError:
            return {}, text
        return (loaded if isinstance(loaded, dict) else {}), body
    # Unterminated frontmatter — treat the whole thing as body rather than guessing where it ends.
    return {}, text
