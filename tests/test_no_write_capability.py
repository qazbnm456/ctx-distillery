"""The design-mandated write-capability scan — an explicit TEST, never just a documented rule.

Belt-and-suspenders on top of the sandbox itself: the `pyodide` interpreter has no host filesystem
access at all, so a planner CANNOT mutate a file — but that guarantee only holds while this package
wires zero write-capable tools. This test is the tripwire: a static scan over every `.py` file under
`ctx_distillery/` asserting none of them contains a mutation call.

It is intentionally crude (source text, not semantics) because its job is to fail LOUDLY the moment
someone adds a writer, not to prove absence of every conceivable trick. It is a guard against drift,
not against a determined author — the sandbox is the real boundary (CLAUDE.md invariant 1).

ONE module is exempt, and the exemption is itself tested: `apply.py` IS the human-gated writer
(CLAUDE.md invariant 8). What makes that safe is not the absence of a write call but its
UNREACHABILITY from the RLM — so `test_apply_is_unreachable_from_the_planner_path` asserts no
RLM-path module imports it, which is the property the scan was really protecting all along.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "ctx_distillery"

# Each pattern is a MUTATION call this package must never contain.
FORBIDDEN: dict[str, re.Pattern[str]] = {
    "open(..., 'w'/'a'/'x'/'+') — a write-mode open": re.compile(
        r"open\s*\([^)]*['\"][rwax+bt]*[wax+][rwax+bt]*['\"]"
    ),
    "Path.open(mode=w/a/x)": re.compile(r"\.open\s*\(\s*['\"][rwax+bt]*[wax+]"),
    "Path.write_text / Path.write_bytes": re.compile(r"\.write_(?:text|bytes)\s*\("),
    "file .write() / .writelines()": re.compile(r"\.write(?:lines)?\s*\("),
    "os.remove / os.unlink / os.rmdir / os.removedirs": re.compile(
        r"\bos\.(?:remove|unlink|rmdir|removedirs)\s*\("
    ),
    "Path.unlink / Path.rmdir": re.compile(r"\.(?:unlink|rmdir)\s*\("),
    "os.rename / os.replace / os.truncate": re.compile(r"\bos\.(?:rename|replace|truncate)\s*\("),
    "shutil mutation (rmtree/move/copy*)": re.compile(r"\bshutil\.(?:rmtree|move|copy\w*)\s*\("),
    "os.mkdir / os.makedirs / Path.mkdir": re.compile(r"(?:\bos\.makedirs|\bos\.mkdir|\.mkdir)\s*\("),
    "tempfile writer (NamedTemporaryFile/mkstemp/TemporaryFile)": re.compile(
        r"\btempfile\.(?:NamedTemporaryFile|TemporaryFile|mkstemp|mkdtemp)\s*\("
    ),
    "subprocess (a shell is a write path)": re.compile(r"\bsubprocess\.\w+\s*\("),
    "os.system / os.exec* / os.popen": re.compile(r"\bos\.(?:system|exec\w*|popen)\s*\("),
}

#: The one deliberate, human-gated writer — see the module docstring. Exempt from the mutation scan,
#: NOT from the reachability test below.
WRITER = "apply.py"

SOURCES = sorted(p for p in PACKAGE.rglob("*.py") if p.name != WRITER)

#: Anything that imports `apply` (or `ctx_distillery.apply`) — the exemption's real guard rail.
IMPORTS_APPLY = re.compile(r"^\s*(?:from\s+[\w.]*\.?apply\s+import|(?:from\s+\S+\s+)?import\s+.*\bapply\b)")


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """Source lines with full-line comments and docstring-ish prose lines dropped.

    Only whole-line `#` comments are stripped: a pattern hiding behind a trailing comment on a real
    statement should still be caught, and prose lines that merely NAME a forbidden call (this
    package's docstrings discuss `open(..., "w")` by design) are excluded by the docstring pass.
    """
    source = path.read_text(encoding="utf-8")

    # Tokenize rather than match `"""` at the start of a stripped line. THAT is what the first
    # version did, and a review proved it left this tripwire blind over 81% of `cli.py` and 36%
    # of `task.py` — a planted `open(..., "w")`, `shutil.rmtree`, `subprocess.run(["rm","-rf","/"])`
    # AND `from .apply import apply_plan` all sailed through with the suite fully green.
    #
    # The bug: a module-level `NAME = """` assignment (`task._INSTRUCTIONS`, `cli._CLI_DESCRIPTION`)
    # does not START with `"""`, so the opener was missed; the bare closing `"""` was then read as
    # an OPENER and the in-docstring parity stayed INVERTED for the whole rest of the file. From
    # there real code was skipped as "docstring body" — silently, since a scan that reads nothing
    # reports no offences and looks exactly like a scan that reads everything and finds none.
    #
    # A line counts as CODE when it carries at least one token that is not a string, a comment, or
    # layout. So: a docstring's body is excluded (string tokens only), the `NAME = """` line itself
    # is kept (it has NAME and OP tokens), and a real statement with a trailing comment is kept —
    # which the module docstring above promises and a naive line-based strip would also get wrong.
    code_lines: set[int] = set()
    layout = {
        tokenize.STRING,
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
        tokenize.ENCODING,
    }
    fstring_start = getattr(tokenize, "FSTRING_START", None)  # py3.12+ splits f-strings into parts
    if fstring_start is not None:
        layout |= {fstring_start, tokenize.FSTRING_MIDDLE, tokenize.FSTRING_END}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in layout:
            code_lines.update(range(token.start[0], token.end[0] + 1))

    return [
        (number, raw)
        for number, raw in enumerate(source.splitlines(), start=1)
        if number in code_lines
    ]


def test_the_scan_actually_sees_the_package():
    assert SOURCES, "no sources found — the scan would vacuously pass"
    assert {p.name for p in SOURCES} >= {
        "task.py", "session.py", "redact.py", "frontmatter.py", "drafting.py",
        "memory_reader.py", "transcript_reader.py", "claude_code.py",
        # The CLI's own modules are named explicitly: `ctx-distillery distill`/`show`/`export` runs
        # as a host-side process with no sandbox around it, so "the planner-side CLI cannot write"
        # is a property worth pinning rather than one that merely happens to hold. It is also why
        # neither `show` nor `export` has an `--out` and why `distill` refuses (rather than deletes)
        # an existing trace file.
        "cli.py", "config.py", "render.py", "__main__.py",
        # `rl_export.py` is named for the same reason, and it is the module where the constraint bit
        # hardest: all three sibling projects' exporters end in `open(out, "w")` inside a `main()`.
        # This module has neither — the bundle is printed to stdout. If someone ports the sibling
        # `main()` verbatim, the parametrised scan below is what catches it.
        "rl_export.py",
    }
    assert (PACKAGE / WRITER).is_file(), "the exempt writer must exist, or the exemption is stale"
    assert WRITER not in {p.name for p in SOURCES}


def test_apply_is_unreachable_from_the_planner_path():
    """`apply.py` may write BECAUSE nothing on the RLM path can reach it (CLAUDE.md invariant 8).

    `apply_plan` is called by a human, never by `run_distillation` or a tool — so no module the
    planner's execution path touches (including `__init__.py`, whose imports would make it eagerly
    loaded) may import it. This is the property that makes the mutation-scan exemption safe; if it
    goes red, the writer just became reachable from the trajectory.
    """
    importers = [
        f"{source.relative_to(PACKAGE)}:{number}: {line.strip()}"
        for source in SOURCES
        for number, line in _code_lines(source)
        if IMPORTS_APPLY.search(line)
    ]
    assert not importers, (
        "nothing on the RLM path may import the human-gated writer (CLAUDE.md "
        "invariant 8):\n" + "\n".join(importers)
    )


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_no_module_contains_a_write_or_delete_call(source):
    offences = [
        f"{source.relative_to(PACKAGE)}:{number}: {label} -> {line.strip()}"
        for number, line in _code_lines(source)
        for label, pattern in FORBIDDEN.items()
        if pattern.search(line)
    ]
    assert not offences, (
        "ctx-distillery must contain NO write/delete capability (CLAUDE.md invariant 1):\n"
        + "\n".join(offences)
    )


@pytest.mark.parametrize(
    "line",
    [
        "from .apply import apply_plan",
        "from ctx_distillery.apply import apply_plan",
        "    from .apply import apply_plan",
        "import ctx_distillery.apply",
        "from ctx_distillery import apply",
    ],
)
def test_the_reachability_check_would_catch_a_real_importer(line):
    """Guard the guard: every plausible way to reach the writer must match."""
    assert IMPORTS_APPLY.search(line)


@pytest.mark.parametrize(
    "line",
    [
        "from .redact import redact_transcript",
        "from rlm_harness.trace import record_tool_call",
        "    outcome = applied(candidate)",
    ],
)
def test_the_reachability_check_does_not_fire_on_unrelated_lines(line):
    assert not IMPORTS_APPLY.search(line)


def test_the_scan_would_catch_a_real_writer(tmp_path):
    """Guard the guard: a planted writer must actually match, or this test file is theatre."""
    planted = tmp_path / "bad.py"
    planted.write_text(
        "from pathlib import Path\n"
        "def leak(p, text):\n"
        "    Path(p).write_text(text)\n"
        "    with open(p, 'w') as fh:\n"
        "        fh.write(text)\n",
        encoding="utf-8",
    )
    hits = [
        label
        for _number, line in _code_lines(planted)
        for label, pattern in FORBIDDEN.items()
        if pattern.search(line)
    ]
    assert "Path.write_text / Path.write_bytes" in hits
    assert any("write-mode open" in h for h in hits)
    assert "file .write() / .writelines()" in hits


def test_the_scan_still_sees_code_after_a_module_level_triple_quoted_assignment(tmp_path):
    """Guard the guard, second edge: a `NAME = \"\"\"` assignment must not blind the rest of the file.

    This is a REGRESSION TEST for a real defect an adversarial review found, not a hypothetical.
    `_code_lines` used to enter docstring-skip mode only when a stripped line STARTED with `\"\"\"`.
    Two modules assign a triple-quoted string to a name at module level — `task._INSTRUCTIONS` and
    `cli._CLI_DESCRIPTION` — and those lines start with the NAME. So the opener was missed, the
    bare closing `\"\"\"` was then read as an OPENER, and the parity stayed INVERTED for the entire
    rest of the file: real code was skipped as "docstring body".

    The damage was total where it landed: 81% of `cli.py` and 36% of `task.py` went unscanned, and
    a planted `open(..., "w")`, `.write()`, `shutil.rmtree`, `subprocess.run(["rm","-rf","/"])` AND
    `from .apply import apply_plan` ALL passed with the suite green. A scan that reads nothing
    reports no offences and is indistinguishable, from the outside, from one that reads everything.

    The fixture below reproduces the exact shape: prose that NAMES a forbidden call (so a scan that
    wrongly treats it as code would produce a false POSITIVE) followed by real code after the
    closing delimiter (so a scan with inverted parity produces a false NEGATIVE). Both must hold.
    """
    planted = tmp_path / "instructions.py"
    planted.write_text(
        '"""A real module docstring, mentioning open(p, "w") as prose."""\n'
        "\n"
        '_INSTRUCTIONS = """\n'
        "Prose the planner reads. It discusses open(p, 'w') and Path(p).write_text(t)\n"
        "on purpose, exactly as this package's own instruction blocks do.\n"
        '"""\n'
        "\n"
        "def leak(p, text):\n"
        "    with open(p, 'w') as fh:\n"
        "        fh.write(text)\n",
        encoding="utf-8",
    )
    scanned = _code_lines(planted)
    hits = [
        label
        for _number, line in scanned
        for label, pattern in FORBIDDEN.items()
        if pattern.search(line)
    ]

    # The false-NEGATIVE half: code after the closing delimiter is still read. Assert on the LINE,
    # not just on a matching label — under the old algorithm a `write-mode open` label WAS produced,
    # but by matching the PROSE's `open(p, 'w')`, while the real `open` two lines further down went
    # unread. A label-only assertion passes on that accident; a line assertion cannot.
    scanned_text = [line for _number, line in scanned]
    assert any("with open(p, 'w') as fh:" in line for line in scanned_text), (
        "code after a module-level triple-quoted assignment was skipped — the parity bug is back"
    )
    assert "file .write() / .writelines()" in hits

    # The false-POSITIVE half: the assigned prose is still excluded, which is why the strip exists.
    assert not any("write_text" in line for _number, line in scanned), (
        "prose inside the assigned string leaked into the scan — it would fail on its own docs"
    )
