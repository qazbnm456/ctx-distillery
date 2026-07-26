"""Shared fixtures: a REAL on-disk fake memory store, and the snapshot the tools take from it.

Everything here builds actual files under `tmp_path` rather than mocking the adapter — the whole
point of the allowlist/exact-path invariants is behaviour against a real filesystem (symlinks,
resolved paths), which a mock cannot exercise.
"""

from __future__ import annotations

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
def adapter(memory_dir):
    return ClaudeCodeAdapter(memory_dir, transcripts=["user: hello\nassistant: hi\n"])


@pytest.fixture
def snapshot(adapter):
    """The immutable `list[ArtifactRef]` every tool closes over — taken from ONE ingest()."""
    return adapter.ingest().memory_index
