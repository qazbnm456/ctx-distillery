"""The design-mandated write-capability scan (docs/DESIGN.md: "add an explicit test").

Belt-and-suspenders on top of the sandbox itself: the `pyodide` interpreter has no host filesystem
access at all, so a planner CANNOT mutate a file — but that guarantee only holds while this package
wires zero write-capable tools. This test is the tripwire: a static scan over every `.py` file under
`ctx_distillery/` asserting none of them contains a mutation call.

It is intentionally crude (source text, not semantics) because its job is to fail LOUDLY the moment
someone adds a writer, not to prove absence of every conceivable trick. It is a guard against drift,
not against a determined author — the sandbox is the real boundary (CLAUDE.md invariant 1).

ONE module is exempt, and the exemption is itself tested: `apply.py` IS the human-gated writer
(docs/DESIGN.md, "The apply step"). What makes that safe is not the absence of a write call but its
UNREACHABILITY from the RLM — so `test_apply_is_unreachable_from_the_planner_path` asserts no
RLM-path module imports it, which is the property the scan was really protecting all along.
"""

from __future__ import annotations

import re
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
    lines: list[tuple[int, str]] = []
    in_doc = False
    delim = ""
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if in_doc:
            if delim in stripped:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            delim = stripped[:3]
            # A one-line docstring opens and closes on the same line.
            if stripped.count(delim) < 2:
                in_doc = True
            continue
        if stripped.startswith("#") or not stripped:
            continue
        lines.append((number, raw))
    return lines


def test_the_scan_actually_sees_the_package():
    assert SOURCES, "no sources found — the scan would vacuously pass"
    assert {p.name for p in SOURCES} >= {
        "task.py", "session.py", "redact.py", "frontmatter.py", "drafting.py",
        "memory_reader.py", "transcript_reader.py", "claude_code.py",
    }
    assert (PACKAGE / WRITER).is_file(), "the exempt writer must exist, or the exemption is stale"
    assert WRITER not in {p.name for p in SOURCES}


def test_apply_is_unreachable_from_the_planner_path():
    """`apply.py` may write BECAUSE nothing on the RLM path can reach it (docs/DESIGN.md).

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
        "nothing on the RLM path may import the human-gated writer (docs/DESIGN.md, "
        "'The apply step'):\n" + "\n".join(importers)
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
        "from rlm_kit.trace import record_tool_call",
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
