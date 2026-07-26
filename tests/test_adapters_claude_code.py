"""`ClaudeCodeAdapter` against REAL files — including the `MEMORY.md` reachability fix.

`docs/DESIGN.md`'s success criterion (b) needs the planner able to READ the index, and a kind the
adapter never enumerates is unreachable through `read_memory_file`'s snapshot allowlist. So the
`kind="index"` entry is asserted here, not assumed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ctx_distillery.adapters.claude_code import MEMORY_TYPES, ClaudeCodeAdapter


def test_list_targets_enumerates_memory_files_with_parsed_frontmatter(snapshot):
    memory = {ref.name: ref for ref in snapshot if ref.kind == "memory"}
    assert set(memory) == {"project-conventions", "user-preferences"}
    assert memory["project-conventions"].description == "How this project does things."


def test_memory_md_is_enumerated_as_kind_index(snapshot, memory_dir):
    index = [ref for ref in snapshot if ref.kind == "index"]
    assert len(index) == 1
    assert index[0].name == "MEMORY.md"
    assert Path(index[0].path) == (memory_dir / "MEMORY.md").resolve()
    # …and it is NOT double-counted as a memory file.
    assert "MEMORY.md" not in {ref.name for ref in snapshot if ref.kind == "memory"}


def test_every_returned_path_is_absolute_and_resolved(snapshot):
    for ref in snapshot:
        path = Path(ref.path)
        assert path.is_absolute()
        assert path == path.resolve()
        assert path.is_file()


def test_paths_are_resolved_through_a_symlinked_memory_dir(tmp_path, memory_dir):
    link = tmp_path / "linked-memory"
    os.symlink(memory_dir, link)
    refs = ClaudeCodeAdapter(link).list_targets()
    assert refs, "the symlinked directory should still enumerate"
    for ref in refs:
        # Resolved to the REAL location, not the symlink path — this is what makes the tool's
        # exact-match allowlist safe.
        assert Path(ref.path).parent == memory_dir.resolve()


def test_ingest_returns_the_caller_supplied_transcripts_verbatim(adapter):
    raw = adapter.ingest()
    assert raw.transcripts == ["user: hello\nassistant: hi\n"]
    assert raw.memory_index == adapter.list_targets()


def test_ingest_hands_out_an_independent_transcript_list(adapter):
    first = adapter.ingest()
    first.transcripts.append("mutated")
    assert adapter.ingest().transcripts == ["user: hello\nassistant: hi\n"]


def test_a_missing_memory_dir_is_an_empty_index_not_an_error(tmp_path):
    adapter = ClaudeCodeAdapter(tmp_path / "nope")
    assert adapter.list_targets() == []
    assert adapter.ingest().memory_index == []


def test_a_memory_file_without_frontmatter_falls_back_to_its_stem(memory_dir):
    (memory_dir / "bare.md").write_text("just notes, no frontmatter\n", encoding="utf-8")
    refs = {ref.name: ref for ref in ClaudeCodeAdapter(memory_dir).list_targets()}
    assert refs["bare"].kind == "memory"
    assert refs["bare"].description == ""


def test_schema_for_memory_pins_the_nested_metadata_type_enum(adapter):
    schema = adapter.schema_for("memory")
    assert schema["required"] == ["name", "description", "metadata"]
    assert schema["properties"]["metadata"]["properties"]["type"]["enum"] == list(MEMORY_TYPES)
    assert MEMORY_TYPES == ("user", "feedback", "project", "reference")
    # An index entry is governed by the same shape.
    assert adapter.schema_for("index") == schema


def test_schema_for_skill_is_the_flat_agent_skills_shape(adapter):
    schema = adapter.schema_for("skill")
    assert schema["required"] == ["name", "description"]
    assert "metadata" not in schema["properties"]


def test_schema_for_an_unknown_kind_raises(adapter):
    with pytest.raises(ValueError):
        adapter.schema_for("transcript")  # type: ignore[arg-type]


def test_the_adapter_never_enumerates_skills_yet(snapshot):
    """A stated pass-1 gap (CLAUDE.md / the module docstring), asserted so it can't drift silently."""
    assert [ref for ref in snapshot if ref.kind == "skill"] == []
