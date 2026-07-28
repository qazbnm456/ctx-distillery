"""Shared fixtures: a REAL on-disk fake memory store, and the snapshot the tools take from it.

Everything here builds actual files under `tmp_path` rather than mocking the adapter — the whole
point of the allowlist/exact-path invariants is behaviour against a real filesystem (symlinks,
resolved paths), which a mock cannot exercise.
"""

from __future__ import annotations

import os

# HERMETICITY, and it has to happen HERE, before the first `ctx_distillery` import below.
# `redact._TIER3` is resolved from `CD_REDACTIONS` at IMPORT time (fail-closed: a broken operator
# file must stop the process, not weaken the redactor silently), so a developer with that variable
# exported would otherwise run the whole suite against their own private rule file — non-hermetic in
# exactly the way `claude_home`'s docstring below refuses for `~/.claude`. The tier-three tests set
# it explicitly and reload the module; nothing else may inherit it from the ambient environment.
os.environ.pop("CD_REDACTIONS", None)

import pytest

from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter

MEMORY_FILES = {
    "conventions.md": (
        "---\n"
        "name: project-conventions\n"
        "description: How this project does things.\n"
        "metadata:\n"
        "  type: project\n"
        "---\n"
        "Run ruff before pushing.\n"
    ),
    "user-prefs.md": (
        "---\n"
        "name: user-preferences\n"
        "description: What the user likes.\n"
        "metadata:\n"
        "  type: user\n"
        "---\n"
        "Prefers concise diffs.\n"
    ),
}

INDEX_TEXT = "# Memory index\n\n- project-conventions\n- user-preferences\n"


@pytest.fixture
def memory_dir(tmp_path):
    """A real `memory/` directory: two frontmatter memory files plus a real `MEMORY.md`."""
    directory = tmp_path / "memory"
    directory.mkdir()
    for filename, text in MEMORY_FILES.items():
        (directory / filename).write_text(text, encoding="utf-8")
    (directory / "MEMORY.md").write_text(INDEX_TEXT, encoding="utf-8")
    return directory


@pytest.fixture
def claude_home(tmp_path):
    """A FAKE `~/.claude`, with a real `skills/` directory.

    Every discovery helper takes a `home=` override precisely so no test ever reads this machine's
    actual `~/.claude` — that would be non-hermetic (it varies per developer and per CI runner) and
    would pull real user content into a fixture. Nothing here touches the real home directory.
    """
    home = tmp_path / "fake-home" / ".claude"
    (home / "skills").mkdir(parents=True)
    return home


def write_skill(root, slug, *, name=None, description="A reusable procedure.", extra=""):
    """Create `<root>/<slug>/SKILL.md` the way Claude Code really stores a skill (a DIRECTORY)."""
    directory = root / slug
    directory.mkdir(parents=True, exist_ok=True)
    text = (
        f"---\nname: {name or slug}\ndescription: {description}\n{extra}---\nDo the thing.\n"
    )
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return directory / "SKILL.md"


@pytest.fixture
def adapter(memory_dir):
    return ClaudeCodeAdapter(memory_dir, transcripts=["user: hello\nassistant: hi\n"])


@pytest.fixture
def snapshot(adapter):
    """The immutable `list[ArtifactRef]` every tool closes over — taken from ONE ingest()."""
    return adapter.ingest().memory_index
