"""`ClaudeCodeAdapter` against REAL files — including the `MEMORY.md` reachability fix.

Flagging candidate `MEMORY.md` index lines needs the planner able to READ the index, and a kind the
adapter never enumerates is unreachable through `read_memory_file`'s snapshot allowlist. So the
`kind="index"` entry is asserted here, not assumed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ctx_distillery.adapters.claude_code import (
    MEMORY_TYPES,
    ClaudeCodeAdapter,
    global_skills_root,
    memory_dir_for_project,
    project_skills_root,
    project_storage_dir,
    render_transcript_events,
    render_transcript_file,
    sanitize_project_dir,
    transcript_files,
)

from .conftest import write_skill


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


def test_list_targets_never_folds_a_symlinks_outside_target_into_the_snapshot(memory_dir, tmp_path):
    """An adversarial review reproduced a real escape here: a symlink INSIDE `memory_dir`,
    present BEFORE `ingest()` ever runs, resolves to a file outside it — and a naive enumeration
    would fold that outside path into the trusted snapshot, which `read_memory_file`'s allowlist
    then treats as legitimate (exact-match-against-a-poisoned-snapshot is not a defense). The
    fix is a containment check in `list_targets` itself: only a resolved path whose PARENT is
    still `memory_dir` may join the snapshot. This test plants the symlink BEFORE building the
    adapter/snapshot — unlike the tool-level symlink test in test_tools_memory_reader.py, which
    plants one AFTER a snapshot to test a different thing (an unlisted path being refused).
    """
    outside = tmp_path / "outside-secret.md"
    outside.write_text("SECRET OUTSIDE CONTENT\n", encoding="utf-8")
    os.symlink(outside, memory_dir / "sneaky.md")

    snapshot = ClaudeCodeAdapter(memory_dir, transcripts=[]).ingest().memory_index

    assert all(Path(ref.path) != outside.resolve() for ref in snapshot)
    assert "sneaky" not in {ref.name for ref in snapshot}


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


def test_schema_for_skill_requires_only_name_and_description_and_offers_the_two_optionals():
    """Corrected per audit: `when_to_use` / `dispatch_intent` are OPTIONAL, not required.

    Every real installed skill the research looked at carried them — but all of those were one
    author's single suite, and Anthropic's documented convention requires neither. This schema has to
    keep describing the same shape `make_skill_validator` enforces (that drift is the exact failure
    the design names), so both halves are asserted: the two required, the two merely offered.
    """
    schema = ClaudeCodeAdapter("/nope").schema_for("skill")
    assert schema["required"] == ["name", "description"]
    assert set(schema["properties"]) == {"name", "description", "when_to_use", "dispatch_intent"}


def test_the_bare_constructor_enumerates_no_skills_unless_a_root_is_given(snapshot):
    """Skill enumeration is OPT-IN on the explicit constructor.

    `apply.py`'s re-scan builds `ClaudeCodeAdapter(memory_dir)`, and a bare adapter silently reaching
    into a real `~/.claude/skills` would make that re-scan machine-dependent. `for_project` is the
    constructor that resolves real locations.
    """
    assert [ref for ref in snapshot if ref.kind == "skill"] == []


# -- skill enumeration, both scopes ---------------------------------------------------------------


def test_list_targets_enumerates_both_skill_scopes_with_the_right_scope_label(
    memory_dir, claude_home, tmp_path
):
    write_skill(claude_home / "skills", "hunt", name="hunt", description="Find root cause.")
    project_root = tmp_path / "proj"
    write_skill(project_skills_root(project_root), "release-checklist")

    refs = ClaudeCodeAdapter(
        memory_dir,
        global_skills_dir=global_skills_root(home=claude_home),
        project_skills_dir=project_skills_root(project_root),
    ).list_targets()

    skills = {ref.name: ref for ref in refs if ref.kind == "skill"}
    assert set(skills) == {"hunt", "release-checklist"}
    assert skills["hunt"].scope == "global"
    assert skills["hunt"].description == "Find root cause."
    assert skills["release-checklist"].scope == "project"
    assert Path(skills["hunt"].path).name == "SKILL.md"


def test_a_memory_or_index_ref_is_project_scoped_never_the_skill_default(snapshot):
    """The `scope` default is KIND-derived, not blanket — a memory ref defaulting to "global" would
    be flatly mislabeled (this project's memory store has no global counterpart)."""
    assert {ref.scope for ref in snapshot} == {"project"}
    assert all(ref.scope == "project" for ref in snapshot if ref.kind in ("memory", "index"))


def test_a_skill_name_falls_back_to_its_DIRECTORY_not_the_file_stem(memory_dir, claude_home):
    root = claude_home / "skills"
    (root / "no-frontmatter").mkdir(parents=True)
    (root / "no-frontmatter" / "SKILL.md").write_text("just a body\n", encoding="utf-8")

    refs = ClaudeCodeAdapter(memory_dir, global_skills_dir=root).list_targets()

    # `SKILL` (the file stem) would name every skill identically — the directory IS the skill.
    assert [ref.name for ref in refs if ref.kind == "skill"] == ["no-frontmatter"]


def test_a_symlinked_skill_directory_escaping_the_root_is_not_enumerated(
    memory_dir, claude_home, tmp_path
):
    """The nested analogue of the memory side's containment check, for the same reason: a symlink in
    the store resolves outside it, and everything downstream trusts the snapshot completely."""
    outside = tmp_path / "outside-skill"
    write_skill(tmp_path, "outside-skill", name="outside-secret")
    os.symlink(outside, claude_home / "skills" / "sneaky")

    refs = ClaudeCodeAdapter(memory_dir, global_skills_dir=claude_home / "skills").list_targets()

    assert [ref for ref in refs if ref.kind == "skill"] == []


def test_a_missing_skills_root_is_simply_no_skills(memory_dir, tmp_path):
    refs = ClaudeCodeAdapter(
        memory_dir,
        global_skills_dir=tmp_path / "nope",
        project_skills_dir=tmp_path / "also-nope",
    ).list_targets()
    assert [ref for ref in refs if ref.kind == "skill"] == []


# -- the JSONL renderer (the real observed shapes, not just the common one) ------------------------


def _event(role, content, **kw):
    payload = {"type": role, "message": {"role": role, "content": content}, "isSidechain": False}
    payload.update(kw)
    return payload


def test_a_plain_string_message_content_is_rendered_directly():
    """Confirmed to occur on real transcripts (20+ events) — `content` is not always a block list."""
    assert render_transcript_events([_event("user", "please fix the flaky test")]) == (
        "user: please fix the flaky test"
    )


def test_a_list_of_blocks_renders_text_thinking_tool_use_and_tool_result():
    rendered = render_transcript_events(
        [
            _event(
                "assistant",
                [
                    {"type": "thinking", "thinking": "weighing two options"},
                    {"type": "text", "text": "I'll patch the retry loop."},
                    {"type": "tool_use", "name": "Edit", "input": {"file": "x.py"}},
                ],
            )
        ]
    )
    assert rendered == (
        "assistant: weighing two options\nI'll patch the retry loop.\n[used tool: Edit]"
    )


def test_a_tool_result_whose_own_content_is_a_string_is_labelled_in_chars():
    rendered = render_transcript_events(
        [_event("user", [{"type": "tool_result", "content": "12345"}])]
    )
    assert rendered == "user: [tool result: 5 chars]"


def test_a_tool_result_whose_own_content_is_a_LIST_is_labelled_in_blocks():
    """The shape a first draft would get wrong: a `tool_result`'s content is not always flat text."""
    rendered = render_transcript_events(
        [
            _event(
                "user",
                [
                    {
                        "type": "tool_result",
                        "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
                    }
                ],
            )
        ]
    )
    assert rendered == "user: [tool result: 2 blocks]"


def test_an_unrecognized_block_type_is_named_rather_than_dropped_or_raised():
    rendered = render_transcript_events(
        [_event("assistant", [{"type": "redacted_thinking", "data": "opaque"}])]
    )
    assert rendered == "assistant: [unrecognized content block: redacted_thinking]"


def test_only_user_and_assistant_events_are_rendered_at_all():
    """Every OTHER event type lacks `message`/`timestamp`/`isSidechain` entirely, so the renderer
    filters FIRST rather than assuming those keys exist."""
    rendered = render_transcript_events(
        [
            {"type": "mode", "mode": "plan"},
            {"type": "file-history-snapshot", "snapshot": {}},
            {"type": "ai-title", "title": "t"},
            _event("user", "the only real line"),
            {"type": "queue-operation", "op": "x"},
        ]
    )
    assert rendered == "user: the only real line"


def test_a_sidechain_event_is_skipped():
    """A defensive no-op, stated honestly: `isSidechain` was `false` on all 1216 real events checked
    — subagent messages live in separate files and are never inlined. The filter guards a future
    version that inlines them; it is not currently removing anything."""
    rendered = render_transcript_events(
        [_event("user", "main thread"), _event("assistant", "subagent chatter", isSidechain=True)]
    )
    assert rendered == "user: main thread"


def test_an_event_with_nothing_renderable_adds_no_bare_role_line():
    assert render_transcript_events([_event("assistant", []), _event("user", None)]) == ""
    assert render_transcript_events([{"type": "user"}, "not even a dict"]) == ""


def test_render_transcript_file_skips_blank_and_torn_lines(tmp_path):
    """A session being written right now legitimately has a torn last line; one bad line must not
    lose the conversation."""
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(_event("user", "first")) + "\n"
        "\n"
        "{not json at all\n" + json.dumps(_event("assistant", "second")) + "\n"
        '{"type": "assistant", "message": {"role": "assis',
        encoding="utf-8",
    )
    assert render_transcript_file(path) == "user: first\nassistant: second"


# -- for_project: the sanitization rule + real-layout discovery ------------------------------------


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        ("/Users/me/proj", "-Users-me-proj"),
        ("/a", "-a"),
        ("/Users/me/nested/deep/proj", "-Users-me-nested-deep-proj"),
    ],
)
def test_sanitize_replaces_every_slash_with_a_hyphen_and_nothing_else(project, expected):
    """The one CONFIRMED half of the storage layout: `/` -> `-`, no other transformation."""
    assert sanitize_project_dir(project) == expected


def test_sanitize_resolves_a_relative_path_to_one_absolute_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert sanitize_project_dir(".") == str(tmp_path.resolve()).replace("/", "-")


def seed_project(claude_home, project_dir, sessions):
    """Build the REAL layout: `<home>/projects/<sanitized>/{memory/, <session-id>.jsonl}`."""
    storage = project_storage_dir(project_dir, home=claude_home)
    (storage / "memory").mkdir(parents=True)
    for session_id, events in sessions.items():
        (storage / f"{session_id}.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )
    return storage


def test_for_project_discovers_memory_transcripts_and_both_skill_roots(tmp_path, claude_home):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage = seed_project(
        claude_home,
        project_dir,
        {
            "0aaa-session": [_event("user", "first conversation")],
            "1bbb-session": [_event("assistant", [{"type": "text", "text": "second conversation"}])],
        },
    )
    (storage / "memory" / "notes.md").write_text(
        "---\nname: notes\ndescription: d\nmetadata:\n  type: project\n---\nbody\n", encoding="utf-8"
    )
    write_skill(claude_home / "skills", "portable-trick")
    write_skill(project_skills_root(project_dir), "this-repos-release")

    adapter = ClaudeCodeAdapter.for_project(project_dir, home=claude_home)
    raw = adapter.ingest()

    assert adapter.memory_dir == (storage / "memory").resolve()
    # One transcript per session file, sorted, each RENDERED (never the raw JSONL).
    assert raw.transcripts == ["user: first conversation", "assistant: second conversation"]
    assert {(r.kind, r.name, r.scope) for r in raw.memory_index} == {
        ("memory", "notes", "project"),
        ("skill", "portable-trick", "global"),
        ("skill", "this-repos-release", "project"),
    }


def test_for_project_on_a_project_with_no_storage_at_all_is_empty_not_an_error(tmp_path, claude_home):
    """A project Claude Code has never stored anything for is a normal input — everything in it
    would be a promotion."""
    adapter = ClaudeCodeAdapter.for_project(tmp_path / "never-used", home=claude_home)
    raw = adapter.ingest()
    assert raw.transcripts == []
    assert raw.memory_index == []


def test_the_derivation_helpers_agree_with_for_project(tmp_path, claude_home):
    """`apply.py` derives the skills roots with these same helpers — one derivation, two consumers.
    If they drifted, the adapter would read one location and the writer would write another."""
    project_dir = tmp_path / "proj"
    assert memory_dir_for_project(project_dir, home=claude_home) == (
        project_storage_dir(project_dir, home=claude_home) / "memory"
    )
    assert global_skills_root(home=claude_home) == claude_home.resolve() / "skills"
    assert project_skills_root(project_dir) == project_dir.resolve() / ".claude" / "skills"

    adapter = ClaudeCodeAdapter.for_project(project_dir, home=claude_home)
    assert adapter.memory_dir == memory_dir_for_project(project_dir, home=claude_home).resolve()
    assert adapter.global_skills_dir == global_skills_root(home=claude_home)
    assert adapter.project_skills_dir == project_skills_root(project_dir)


def test_transcript_discovery_ignores_non_jsonl_files_and_escaping_symlinks(tmp_path, claude_home):
    project_dir = tmp_path / "proj"
    storage = seed_project(claude_home, project_dir, {"real-session": [_event("user", "kept")]})
    (storage / "notes.txt").write_text("not a transcript\n", encoding="utf-8")
    outside = tmp_path / "outside-session.jsonl"
    outside.write_text(json.dumps(_event("user", "SECRET OUTSIDE")) + "\n", encoding="utf-8")
    os.symlink(outside, storage / "sneaky-session.jsonl")

    found = transcript_files(project_dir, home=claude_home)

    assert [p.name for p in found] == ["real-session.jsonl"]
    rendered = ClaudeCodeAdapter.for_project(project_dir, home=claude_home).ingest().transcripts
    assert rendered == ["user: kept"]


def test_for_project_never_reads_this_machines_real_claude_home(tmp_path, claude_home):
    """Hermeticity, asserted rather than assumed: with `home=` given, nothing resolves under `~`."""
    adapter = ClaudeCodeAdapter.for_project(tmp_path / "proj", home=claude_home)
    real_home = Path.home().resolve()
    for path in (adapter.memory_dir, adapter.global_skills_dir):
        assert not Path(path).is_relative_to(real_home / ".claude")
