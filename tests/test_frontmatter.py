"""`frontmatter.parse` — the real, NESTED-capable YAML frontmatter reader.

The point of this module (over `rlm_kit.skills`'s flat parser) is `metadata.type`, so the nesting
case is the first thing asserted; the rest pin the degradation paths, which must return `({}, text)`
rather than raise (a malformed model draft is a validation outcome, not a REPL exception).
"""

import pytest

from ctx_distillery.frontmatter import parse

_NESTED = """\
---
name: project-conventions
description: How this project does things.
metadata:
  type: project
---
Body line one.
Body line two.
"""

_FLAT = """\
---
name: my-skill
description: A reusable procedure.
---
# Steps
"""


def test_parses_nested_metadata_type():
    meta, body = parse(_NESTED)
    assert meta["name"] == "project-conventions"
    assert meta["metadata"] == {"type": "project"}
    assert body == "Body line one.\nBody line two.\n"


def test_parses_the_flat_agent_skills_shape_as_a_degenerate_case():
    meta, body = parse(_FLAT)
    assert meta == {"name": "my-skill", "description": "A reusable procedure."}
    assert body == "# Steps\n"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no frontmatter at all\n",
        "\n---\nname: x\n---\nbody\n",                 # delimiter not on the FIRST line
        "---\nname: unterminated\nbody with no closing delimiter\n",
        "---\n\tname: [oops\n---\nbody\n",             # malformed YAML
    ],
)
def test_degrades_to_empty_frontmatter_without_raising(text):
    meta, body = parse(text)
    assert meta == {}
    # The body is returned verbatim in every degradation case except the empty-input one.
    assert body == text


def test_a_well_delimited_block_that_is_not_a_mapping_still_splits():
    """The delimiters are valid, so the split is real — only the frontmatter is empty. The caller
    (a drafting validator) then reports "no parseable frontmatter", which is the right outcome."""
    meta, body = parse("---\njust a scalar\n---\nbody\n")
    assert meta == {}
    assert body == "body\n"


def test_a_body_containing_a_horizontal_rule_is_not_mistaken_for_frontmatter():
    text = "---\nname: n\ndescription: d\n---\nintro\n\n---\n\nmore body\n"
    meta, body = parse(text)
    assert meta == {"name": "n", "description": "d"}
    assert body == "intro\n\n---\n\nmore body\n"


def test_empty_frontmatter_block_is_a_mapping_free_but_valid_split():
    meta, body = parse("---\n---\nbody\n")
    assert meta == {}
    assert body == "body\n"
