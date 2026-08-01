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
    CLAUDE_DIRNAME,
    CLAUDE_MD_FILENAME,
    INDEX_LINE_MAX,
    MEMORY_TYPES,
    ClaudeCodeAdapter,
    SubagentTranscript,
    global_skills_root,
    memory_dir_for_project,
    parent_ref,
    project_claude_md_path,
    project_skills_root,
    project_storage_dir,
    render_transcript_events,
    render_transcript_file,
    sanitize_project_dir,
    subagent_files,
    subagent_header,
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
    """`isSidechain` is what separates the two transcript STORES — and on a MAIN-THREAD file it
    really does filter nothing (measured: `False` on 0 of 57,928 user/assistant events across 883
    session files), which is why these assertions are unchanged and pin the DEFAULT.

    The docstring is what changed, and it was wrong in an interesting way: it called the filter a
    no-op that "is not currently removing anything", generalising from the one population where
    that is true. On a SUBAGENT file the same filter removes EVERYTHING (72,126 of 72,126, across
    874 files), which is why reading one requires `include_sidechain=True` rather than deleting the
    filter — see the sibling test below.
    """
    rendered = render_transcript_events(
        [_event("user", "main thread"), _event("assistant", "subagent chatter", isSidechain=True)]
    )
    assert rendered == "user: main thread"


def test_include_sidechain_true_is_what_makes_a_subagent_file_render_at_all():
    """The sibling to the default test above, and the whole reason the parameter exists.

    A real subagent transcript is 100% sidechain events, so the shipped renderer returns exactly 0
    characters on one — measured across all 874 real files. The parameter is explicit rather than
    path-sniffed or auto-detected: this function takes an ITERABLE and knows nothing about the
    filesystem, and an input-dependent filter ("if everything is sidechain, keep it") would behave
    unpredictably on a partially-sidechain file.
    """
    events = [_event("user", "sub task"), _event("assistant", "sub finding", isSidechain=True)]
    events[0]["isSidechain"] = True

    assert render_transcript_events(events) == ""
    assert render_transcript_events(events, include_sidechain=True) == (
        "user: sub task\nassistant: sub finding"
    )


def test_render_transcript_file_defaults_to_dropping_sidechain_too(tmp_path):
    """The FILE-level default, pinned separately from the events-level one — they are two defaults.

    Found by mutation testing during review: flipping `render_transcript_file`'s default to True, and
    hard-coding it to True, BOTH left the whole suite green, while the same mutation one layer down
    (`render_transcript_events`) was caught immediately. The events-level test above renders an
    in-memory list; the only file-level coverage passed `include_sidechain=True` explicitly, so
    nothing exercised the wrapper's own default at all.

    This is the layer where the property has to hold: `for_project(include_subagents=False)` and the
    SESSION entries of `_render_with_subagents` both route through this wrapper, so a True default
    here would silently start folding subagent chatter into main-thread text — the byte-identity
    claim §4.2 makes corpus-wide, undefended.
    """
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(_event("user", "main thread"))
        + "\n"
        + json.dumps(_event("assistant", "subagent chatter", isSidechain=True))
        + "\n",
        encoding="utf-8",
    )

    assert render_transcript_file(path) == "user: main thread"
    assert render_transcript_file(path, include_sidechain=True) == (
        "user: main thread\nassistant: subagent chatter"
    )


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


def test_project_claude_md_path_finds_the_root_file(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    expected = project_dir / CLAUDE_MD_FILENAME
    expected.write_text("# root instructions\n", encoding="utf-8")
    assert project_claude_md_path(project_dir) == expected.resolve()


def test_project_claude_md_path_falls_back_to_the_nested_location(tmp_path):
    project_dir = tmp_path / "proj"
    (project_dir / CLAUDE_DIRNAME).mkdir(parents=True)
    nested = project_dir / CLAUDE_DIRNAME / CLAUDE_MD_FILENAME
    nested.write_text("# nested instructions\n", encoding="utf-8")
    assert project_claude_md_path(project_dir) == nested.resolve()


def test_project_claude_md_path_is_none_when_neither_exists(tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    assert project_claude_md_path(project_dir) is None


def test_project_claude_md_path_prefers_root_when_both_exist(tmp_path):
    project_dir = tmp_path / "proj"
    (project_dir / CLAUDE_DIRNAME).mkdir(parents=True)
    root = project_dir / CLAUDE_MD_FILENAME
    root.write_text("# root\n", encoding="utf-8")
    (project_dir / CLAUDE_DIRNAME / CLAUDE_MD_FILENAME).write_text("# nested\n", encoding="utf-8")
    assert project_claude_md_path(project_dir) == root.resolve()


def test_project_claude_md_path_follows_a_SAME_DIRECTORY_symlink(tmp_path):
    """The `ln -s AGENTS.md CLAUDE.md` workaround this project deliberately does not special-case —
    it works for free because the symlink's target still resolves inside `project_dir`."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    agents_md = project_dir / "AGENTS.md"
    agents_md.write_text("# agents instructions\n", encoding="utf-8")
    os.symlink(agents_md, project_dir / CLAUDE_MD_FILENAME)
    assert project_claude_md_path(project_dir) == agents_md.resolve()


def test_project_claude_md_path_refuses_a_symlink_escaping_the_project(tmp_path):
    """A git-tracked `CLAUDE.md -> /outside/secret` in a CLONED, untrusted repo must not have its
    target's bytes read as LM context — the same enumeration-side containment `_memory_refs`
    already applies to `memory_dir`, found missing here by adversarial review."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    outside = tmp_path / "outside-secret.md"
    outside.write_text("SECRET OUTSIDE CONTENT\n", encoding="utf-8")
    os.symlink(outside, project_dir / CLAUDE_MD_FILENAME)
    assert project_claude_md_path(project_dir) is None


def test_project_claude_md_path_refuses_when_the_claude_directory_itself_is_a_symlink(tmp_path):
    """Not just `CLAUDE.md` itself — a symlinked `.claude` DIRECTORY must not validate itself by
    having its own resolution echoed back as the 'expected parent' (the exact hole a naive
    re-resolve-the-parent check would reopen)."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    outside_claude_dir = tmp_path / "outside-claude-dir"
    outside_claude_dir.mkdir()
    (outside_claude_dir / CLAUDE_MD_FILENAME).write_text("SECRET\n", encoding="utf-8")
    os.symlink(outside_claude_dir, project_dir / CLAUDE_DIRNAME)
    assert project_claude_md_path(project_dir) is None


def test_for_project_populates_project_instructions_from_a_seeded_CLAUDE_md(tmp_path, claude_home):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / CLAUDE_MD_FILENAME).write_text("# this project's own rules\n", encoding="utf-8")

    adapter = ClaudeCodeAdapter.for_project(project_dir, home=claude_home)
    assert adapter.ingest().project_instructions == "# this project's own rules\n"


def test_for_project_defaults_project_instructions_to_empty_when_none_exists(tmp_path, claude_home):
    adapter = ClaudeCodeAdapter.for_project(tmp_path / "no-claude-md", home=claude_home)
    assert adapter.ingest().project_instructions == ""


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


def test_a_symlinked_dotclaude_still_finds_transcripts(tmp_path, monkeypatch):
    """A regression test for silent data loss, reproduced before it was fixed.

    `claude_home` used to be `Path.home().resolve() / CLAUDE_DIRNAME` — the home component resolved,
    `.claude` itself NOT. Anyone who symlinks `~/.claude` into a dotfiles repo or an external volume
    (a common arrangement) then got ZERO transcripts with no error at all: `transcript_files` compares
    a RESOLVED session path's parent against the storage directory, the parents never match, every
    file is filtered out, and `storage.is_dir()` stays True so no "no storage here" branch fires.

    The control is what makes this a test rather than an assertion about one arrangement: identical
    content behind a real directory and behind a symlink must yield the SAME transcripts. Measured on
    the pre-fix code, they yielded 1 and 0.

    This must patch `Path.home` rather than pass `home=`, because the `home=` override was always
    fully resolved — the bug lived only on the branch no other test can reach. Invariant 6 still
    holds: `Path.home` is redirected into `tmp_path`, so nothing reads this machine's real `~/.claude`.
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    session = json.dumps(_event("user", "hello")) + "\n"

    real_home = tmp_path / "real-home"
    seed_project(real_home / ".claude", project_dir, {"sess": [_event("user", "hello")]})

    linked_home = tmp_path / "linked-home"
    linked_home.mkdir()
    store = tmp_path / "dotfiles-claude"
    storage = project_storage_dir(project_dir, home=store)
    storage.mkdir(parents=True)
    (storage / "sess.jsonl").write_text(session, encoding="utf-8")
    os.symlink(store, linked_home / ".claude")

    def transcripts_for(home: Path) -> list[str]:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        return ClaudeCodeAdapter.for_project(project_dir).ingest().transcripts

    assert transcripts_for(real_home) == ["user: hello"]
    assert transcripts_for(linked_home) == ["user: hello"]


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


# -- subagent transcripts: discovery, containment, headers ----------------------------------------
#
# The fixture below is MANDATORY-SHAPED, not illustrative, and that is the single most important
# thing in this section. A flat-only fixture passes against a non-recursive implementation and
# would pin the wrong shape permanently — which is exactly how a design that missed 34% of a real
# corpus survived being "measured". So the seeder always builds the nested run directory, always
# gives its agent a KEYS-ONLY sidecar, and always plants the `journal.jsonl` that must not be
# ingested. Delete any of the three and this file certifies the bug.


def _sub_event(role, content, **kw):
    """A SUBAGENT event: the same wire shape as `_event`, but `isSidechain: True` — which is what
    all 72,126 user/assistant events across 874 real subagent files carried, without exception."""
    return _event(role, content, isSidechain=True, **kw)


#: FULL sidecar — every key the flat population really carries.
FLAT_META = {
    "agentType": "general-purpose",
    "description": "hunt the flaky test",
    "spawnDepth": 1,
    "toolUseId": "toolu_01flat",
}
#: A depth-2 agent: the only population that carries `parentAgentId` (44 of 874 files).
DEEP_META = {
    "agentType": "Explore",
    "description": "read the adapter",
    "spawnDepth": 2,
    "toolUseId": "toolu_02deep",
    "parentAgentId": "flat1",
}
#: KEYS-ONLY — `agentType` + `spawnDepth` and NOTHING else. This is the live degradation path, not
#: a hypothetical: 299 of 874 real files (every nested one) carry exactly these two keys.
NESTED_META = {"agentType": "workflow-subagent", "spawnDepth": 1}

WORKFLOW_RUN = "wf_test0000001"


def write_subagent(directory, agent_id, meta, events):
    """`<directory>/agent-<id>.jsonl` + its `agent-<id>.meta.json` sidecar."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"agent-{agent_id}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    if meta is not None:
        (directory / f"agent-{agent_id}.meta.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )


def seed_subagents(storage, session_id):
    """The real layout, ALL of it: flat agents, a nested workflow run, and the two non-transcripts.

        <storage>/<session-id>/
        ├── subagents/
        │   ├── agent-flat1.jsonl     + .meta.json   (FULL sidecar)
        │   ├── agent-flat2.jsonl     + .meta.json   (spawnDepth 2 + parentAgentId)
        │   └── workflows/wf_test0000001/
        │       ├── agent-nested1.jsonl + .meta.json (KEYS-ONLY sidecar)
        │       └── journal.jsonl                    (must NOT be ingested)
        └── tool-results/x.txt                       (must NOT be ingested)
    """
    subagents = storage / session_id / "subagents"
    write_subagent(subagents, "flat1", FLAT_META, [_sub_event("user", "flat one speaking")])
    write_subagent(subagents, "flat2", DEEP_META, [_sub_event("assistant", "flat two speaking")])
    write_subagent(
        subagents / "workflows" / WORKFLOW_RUN,
        "nested1",
        NESTED_META,
        [_sub_event("user", "nested one speaking")],
    )
    # NOT a transcript: one per workflow run directory, and the `agent-` filename filter is the
    # only thing that excludes it. Copying the SDK's store-mirroring helper would ingest all nine.
    (subagents / "workflows" / WORKFLOW_RUN / "journal.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "JOURNAL LINE"}}) + "\n",
        encoding="utf-8",
    )
    # NOT a transcript either: offloaded tool-result bodies, out of the render path entirely.
    (storage / session_id / "tool-results").mkdir(parents=True)
    (storage / session_id / "tool-results" / "x.txt").write_text("OFFLOADED", encoding="utf-8")
    return subagents


@pytest.fixture
def subagent_project(tmp_path, claude_home):
    """A project with one session that really has all three subagent shapes beside it."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage = seed_project(
        claude_home, project_dir, {"sess-1": [_event("user", "the main thread")]}
    )
    seed_subagents(storage, "sess-1")
    return project_dir, storage


def test_subagent_discovery_is_recursive_and_excludes_the_journal(subagent_project, claude_home):
    """The two halves of the discovery rule, each pinned by a case that would otherwise pass.

    The NESTED file fails against a flat glob — the shape that missed 34% of a real corpus. The
    JOURNAL fails against `rglob("*.jsonl")`, i.e. against copying the SDK's store-MIRRORING helper
    instead of its transcript-reading one.
    """
    project_dir, _storage = subagent_project
    found = subagent_files(project_dir, home=claude_home)

    assert [t.agent_id for t in found] == ["flat1", "flat2", "nested1"]
    assert all(t.session_id == "sess-1" for t in found)
    assert not any("journal" in t.path.name for t in found)
    assert not any("tool-results" in str(t.path) for t in found)
    # Every returned path is resolved — which is what closes the swap-after-check race on the file.
    assert all(t.path == t.path.resolve() and t.path.is_file() for t in found)


def test_the_subpath_is_the_sdk_shaped_relative_locator(subagent_project, claude_home):
    """Same string, same construction as the SDK's own `SessionKey["subpath"]` — extension dropped,
    POSIX separators, relative to the session directory, so it identifies nothing about the machine."""
    project_dir, _storage = subagent_project
    by_id = {t.agent_id: t for t in subagent_files(project_dir, home=claude_home)}

    assert by_id["flat1"].subpath == "subagents/agent-flat1"
    assert by_id["nested1"].subpath == f"subagents/workflows/{WORKFLOW_RUN}/agent-nested1"
    assert by_id["nested1"].workflow_run == WORKFLOW_RUN
    assert by_id["flat1"].workflow_run is None


def test_the_parent_link_has_three_cases_and_the_nested_one_is_the_workflow_run(
    subagent_project, claude_home
):
    """The correction that matters. A rule reading "depth 1 means the session owns it" is
    confidently WRONG on every nested file: all 299 real ones are `spawnDepth: 1` with NO
    `parentAgentId`, and their parent is a workflow RUN. So the path is tested FIRST."""
    project_dir, _storage = subagent_project
    by_id = {t.agent_id: t for t in subagent_files(project_dir, home=claude_home)}

    assert parent_ref(by_id["flat1"]) == "session:sess-1"
    assert parent_ref(by_id["flat2"]) == "agent:flat1"
    assert parent_ref(by_id["nested1"]) == f"workflow:{WORKFLOW_RUN}"


def test_a_keys_only_sidecar_degrades_PER_FIELD_never_per_file(subagent_project, claude_home):
    """The live path, 299 of 874 files: a sidecar that is PRESENT and simply lacks keys.

    `agentType` and `spawnDepth` survive; `description` and `parentAgentId` come back None; nothing
    raises and no other field is affected. An implementation treating `description` as required
    fails here, and one degrading per-FILE loses the two keys that are always there.
    """
    project_dir, _storage = subagent_project
    nested = {t.agent_id: t for t in subagent_files(project_dir, home=claude_home)}["nested1"]

    assert nested.agent_type == "workflow-subagent" and nested.spawn_depth == 1
    assert nested.description is None and nested.parent_agent_id is None
    # `toolUseId` is absent here and deliberately never reaches a header even when present — it is
    # an opaque id a model cannot act on. It stays reachable on `meta` for a human or an audit.
    assert nested.meta == NESTED_META
    assert "toolUseId" not in nested.meta


@pytest.mark.parametrize("sidecar", [None, "{not json at all", '["a", "list"]'])
def test_a_missing_or_malformed_sidecar_still_renders_the_transcript(
    tmp_path, claude_home, sidecar
):
    """Defensive, and labelled as such: 0 of 874 real files were missing or malformed. The rule is
    still that a sidecar problem costs the METADATA, never the transcript."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage = seed_project(claude_home, project_dir, {"sess-1": [_event("user", "main")]})
    write_subagent(storage / "sess-1" / "subagents", "bare", None, [_sub_event("user", "body")])
    if sidecar is not None:
        (storage / "sess-1" / "subagents" / "agent-bare.meta.json").write_text(
            sidecar, encoding="utf-8"
        )

    found = subagent_files(project_dir, home=claude_home)

    assert [t.agent_id for t in found] == ["bare"]
    assert found[0].agent_type is None and found[0].spawn_depth is None and found[0].meta == {}
    assert render_transcript_file(found[0].path, include_sidechain=True) == "user: body"


# -- containment: the arrangements that actually escape -------------------------------------------


def _session_with_storage(tmp_path, claude_home, session_id="sess-1"):
    project_dir = tmp_path / "proj"
    project_dir.mkdir(exist_ok=True)
    storage = seed_project(claude_home, project_dir, {session_id: [_event("user", "main")]})
    return project_dir, storage


def test_a_symlinked_subagents_directory_pointing_outside_yields_nothing(tmp_path, claude_home):
    """Escape 1 of 3, reproduced against a real implementation before this guard existed.

    Anchoring on a RESOLVED root and prefix-testing against it looks airtight ("both operands are
    resolved, so nothing can fool it") and is not: resolving the root makes it MOVE WITH the
    symlink, after which everything under the symlink's target is trivially `is_relative_to` it.
    """
    project_dir, storage = _session_with_storage(tmp_path, claude_home)
    outside = tmp_path / "outside-subagents"
    write_subagent(outside, "secret", FLAT_META, [_sub_event("user", "SECRET OUTSIDE")])
    (storage / "sess-1").mkdir()
    os.symlink(outside, storage / "sess-1" / "subagents")

    assert subagent_files(project_dir, home=claude_home) == []


def test_a_symlinked_session_directory_pointing_outside_yields_nothing(tmp_path, claude_home):
    """Escape 2 of 3, and the one a check placed ONE LEVEL LOWER never sees: the escape happens
    above `subagents/`, so pinning only `subagents/` looks below where it occurs."""
    project_dir, storage = _session_with_storage(tmp_path, claude_home)
    outside = tmp_path / "outside-session"
    write_subagent(
        outside / "subagents", "elsewhere", FLAT_META, [_sub_event("user", "SECRET OUTSIDE")]
    )
    os.symlink(outside, storage / "sess-1")

    assert subagent_files(project_dir, home=claude_home) == []


def test_subagents_symlinked_to_the_memory_store_yields_nothing(tmp_path, claude_home):
    """Escape 3 of 3, and the nastiest: the target is INSIDE storage, so every check phrased as
    "did we stay under the project's own directory" passes — while the MEMORY STORE gets read as if
    it were a transcript. Keep this case explicitly; it is the one that defeats the plausible fix.
    """
    project_dir, storage = _session_with_storage(tmp_path, claude_home)
    write_subagent(storage / "memory", "mem", FLAT_META, [_sub_event("user", "MEMORY CONTENT")])
    (storage / "sess-1").mkdir()
    os.symlink(storage / "memory", storage / "sess-1" / "subagents")

    assert subagent_files(project_dir, home=claude_home) == []


def test_a_symlinked_file_inside_a_legitimate_subagents_directory_is_skipped(tmp_path, claude_home):
    """The leaf case: the directory hops are fine, one FILE in it resolves out of the tree."""
    project_dir, storage = _session_with_storage(tmp_path, claude_home)
    subagents = storage / "sess-1" / "subagents"
    write_subagent(subagents, "real", FLAT_META, [_sub_event("user", "kept")])
    outside = tmp_path / "outside-agent.jsonl"
    outside.write_text(json.dumps(_sub_event("user", "SECRET OUTSIDE")) + "\n", encoding="utf-8")
    os.symlink(outside, subagents / "agent-sneaky.jsonl")

    assert [t.agent_id for t in subagent_files(project_dir, home=claude_home)] == ["real"]


def test_a_symlinked_SUBDIRECTORY_is_not_descended_into(tmp_path, claude_home):
    """**This pins `pathlib`'s own refusal to descend directory symlinks, NOT a guard in this
    module.** `rglob` on 3.11+ already declines, so this case passes with or WITHOUT any check of
    ours — which is precisely why it is labelled. The escapes that needed real work are the three
    root-symlink tests above; treating this one as evidence of containment is how false confidence
    gets inherited.
    """
    project_dir, storage = _session_with_storage(tmp_path, claude_home)
    subagents = storage / "sess-1" / "subagents"
    write_subagent(subagents, "ok", FLAT_META, [_sub_event("user", "kept")])
    outside = tmp_path / "outside-dir"
    write_subagent(outside, "deep", FLAT_META, [_sub_event("user", "SECRET OUTSIDE")])
    os.symlink(outside, subagents / "linked-sub")

    assert [t.agent_id for t in subagent_files(project_dir, home=claude_home)] == ["ok"]


def test_discovery_still_returns_files_when_an_ANCESTOR_is_a_symlink(tmp_path):
    """The macOS regression guard: `/var` -> `/private/var` is the real shape of this, and the
    failure it guards is SILENT — an unresolved ancestor mismatches every resolved child, so a
    "simplified" containment check returns ZERO files and looks like a project with no subagents.

    Stated exactly, because overclaiming a guard is the failure this section exists to avoid: what
    keeps this green is that BOTH sides of each `==` are resolved, `claude_home()` included. The
    test reproduces the platform arrangement and asserts the feature still works through it; it is
    not by itself proof that an unresolved variant would go red on this machine.
    """
    real_root = tmp_path / "real"
    (real_root / "fake-home" / ".claude").mkdir(parents=True)
    linked_root = tmp_path / "linked"
    os.symlink(real_root, linked_root)
    home = linked_root / "fake-home" / ".claude"

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage = seed_project(home, project_dir, {"sess-1": [_event("user", "main")]})
    seed_subagents(storage, "sess-1")

    found = subagent_files(project_dir, home=home)

    assert [t.agent_id for t in found] == ["flat1", "flat2", "nested1"]
    assert all(t.path.is_relative_to(real_root.resolve()) for t in found)


def test_a_project_with_no_subagents_at_all_is_empty_not_an_error(tmp_path, claude_home):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    seed_project(claude_home, project_dir, {"sess-1": [_event("user", "main")]})
    assert subagent_files(project_dir, home=claude_home) == []
    assert subagent_files(tmp_path / "never-used", home=claude_home) == []


# -- for_project(include_subagents=True): entries, headers, identities -----------------------------


def test_the_default_path_ignores_subagents_entirely_and_renders_no_header(
    subagent_project, claude_home
):
    """Opt-in, default OFF — and the default rendering is BYTE-IDENTICAL to what it always was.

    Two independent reasons, both decisive: `transcripts` is positional, so flipping the default
    silently renumbers every entry and `read_transcript_chunk(3, ...)` would name a different
    conversation before and after; and shipping ~1.5x more text (up to 18.5x more ENTRIES) to a
    remote model is an operator's call, because redaction admits false negatives by construction.
    """
    project_dir, _storage = subagent_project
    raw = ClaudeCodeAdapter.for_project(project_dir, home=claude_home).ingest()

    assert raw.transcripts == ["user: the main thread"]
    assert [t.kind for t in raw.transcript_ids] == ["session"]


def test_include_subagents_adds_one_labelled_entry_per_subagent_in_a_stable_order(
    subagent_project, claude_home
):
    """Separate entries, session-then-its-own-subagents, each with its own synthesized header.

    Separate entries are what make redaction, auditability and judgeability STRUCTURAL: the text
    lands in `RawSession.transcripts`, which is the ONE list `run_distillation_artifacts` redacts,
    the list `read_transcript_chunk` closes over, and the list `eval/`'s judge reads.
    """
    project_dir, _storage = subagent_project
    raw = ClaudeCodeAdapter.for_project(
        project_dir, home=claude_home, include_subagents=True
    ).ingest()

    assert len(raw.transcripts) == 4
    first_lines = [entry.split("\n")[0] for entry in raw.transcripts]
    assert first_lines[0] == "[0] session sess-1"
    assert first_lines[1].startswith("[1] subagent flat1 parent=session:sess-1 ")
    assert first_lines[2].startswith("[2] subagent flat2 parent=agent:flat1 ")
    # A workflow run's agents sort AFTER the flat ones and stay contiguous.
    assert first_lines[3] == (
        f"[3] subagent nested1 parent=workflow:{WORKFLOW_RUN} type=workflow-subagent depth=1"
    )
    # The body really is there — which only happens because the renderer was asked to keep
    # sidechain events for these files.
    assert raw.transcripts[3].endswith("user: nested one speaking")
    assert "JOURNAL LINE" not in "\n".join(raw.transcripts)


def test_the_nested_entrys_header_says_task_not_recorded_and_does_not_raise(
    subagent_project, claude_home
):
    """`description` is absent on 299 of 874 real files, so this line is the live degradation — and
    the header must still render all three lines rather than raising or silently dropping one."""
    project_dir, _storage = subagent_project
    raw = ClaudeCodeAdapter.for_project(
        project_dir, home=claude_home, include_subagents=True
    ).ingest()

    lines = raw.transcripts[3].split("\n")
    assert lines[1] == (
        f"session=sess-1 agent=nested1 subpath=subagents/workflows/{WORKFLOW_RUN}/agent-nested1"
    )
    assert lines[2] == "task: (not recorded)"
    # …and a sidecar that HAS one renders it verbatim (it is model-authored text, which is exactly
    # why it lives inside the redacted string rather than beside it as trusted metadata).
    assert raw.transcripts[1].split("\n")[2] == "task: hunt the flaky test"


def test_every_index_line_fits_the_budget_and_the_ceiling_it_implies(subagent_project, claude_home):
    """The bound AND the ceiling it implies, asserted in one place from ONE named constant.

    They must not be able to drift: a bound of 90 would imply a ceiling of 439, so quoting "under
    90 characters" beside a stated ceiling of 459 is two numbers in one document that cannot both
    be true. `INDEX_LINE_MAX` is the only place either is written down.

    The fixture's ids are short, so the second half constructs the WORST case measured over the real
    corpus — a workflow-nested entry with the longest observed `agentType` — and pins it at exactly
    the bound. Without that, a fixture-only assertion is vacuous.
    """
    project_dir, _storage = subagent_project
    raw = ClaudeCodeAdapter.for_project(
        project_dir, home=claude_home, include_subagents=True
    ).ingest()
    for entry in raw.transcripts:
        assert len(entry.split("\n")[0]) <= INDEX_LINE_MAX

    worst = SubagentTranscript(
        path=Path("/x/agent-a.jsonl"),
        session_id="s" * 36,                       # a real session id is 36 characters
        agent_id="a" * 17,                         # a real agent id is 17
        subpath=f"subagents/workflows/{'w' * 15}/agent-{'a' * 17}",
        workflow_run="w" * 15,                     # `wf_` + 12, and NEVER shortened
        agent_type="workflow-subagent",            # the longest observed value
        spawn_depth=1,
    )
    assert len(subagent_header(999, worst).split("\n")[0]) == INDEX_LINE_MAX
    # ...and the entry ceiling that bound implies at the default CD_MAX_OUTPUT_CHARS.
    assert 40_000 // (INDEX_LINE_MAX + 1) == 459


def test_transcript_ids_name_every_entry_with_identifiers_only(subagent_project, claude_home):
    """The ordered identity list: it is what lets a reviewer holding a trace answer "what was
    transcript 7?", which nothing anywhere could do before. Four `str` fields, every one derived
    from a filename or a directory name — no description, no agentType, no body, nothing for
    redaction to do."""
    project_dir, _storage = subagent_project
    raw = ClaudeCodeAdapter.for_project(
        project_dir, home=claude_home, include_subagents=True
    ).ingest()

    assert len(raw.transcript_ids) == len(raw.transcripts)
    assert [(t.kind, t.id, t.session, t.parent) for t in raw.transcript_ids] == [
        ("session", "sess-1", "sess-1", "session:sess-1"),
        ("subagent", "flat1", "sess-1", "session:sess-1"),
        ("subagent", "flat2", "sess-1", "agent:flat1"),
        ("subagent", "nested1", "sess-1", f"workflow:{WORKFLOW_RUN}"),
    ]
    # The mode is INFERABLE from the list itself — no separate boolean asserting it.
    kinds = [t.kind for t in raw.transcript_ids]
    assert kinds.count("session") == 1 and kinds.count("subagent") == 3


def test_ordering_is_session_then_that_sessions_own_subagents_then_the_next(tmp_path, claude_home):
    """Both halves of the ordering contract, on a TWO-session fixture — which is the whole point.

    Review's mutation pass found both halves undefended, because every other fixture here has ONE
    session and agent ids that already sort the way the real key does. Two mutations survived the
    entire suite: sorting subagents by `agent_id` alone (dropping workflow grouping AND depth), and
    appending all subagents after all sessions. So this fixture is built to make the correct order
    DIFFER from both:

    * ids are chosen so alphabetical order is wrong three ways — `zzz-late` (flat, depth 1) must
      precede `aaa-deep` (flat, depth 2) must precede `mmm-nested` (in a workflow run);
    * two sessions interleave, so "all sessions, then all subagents" is a different sequence.

    Contract: a session, then that session's OWN subagents, then the next session — related material
    adjacent, a workflow run's agents contiguous, and deterministic across runs.
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage = seed_project(
        claude_home,
        project_dir,
        {"aaa-sess": [_event("user", "first")], "bbb-sess": [_event("user", "second")]},
    )
    subagents = storage / "aaa-sess" / "subagents"
    write_subagent(
        subagents, "zzz-late", {"agentType": "t", "spawnDepth": 1}, [_sub_event("user", "z")]
    )
    write_subagent(
        subagents,
        "aaa-deep",
        {"agentType": "t", "spawnDepth": 2, "parentAgentId": "zzz-late"},
        [_sub_event("user", "a")],
    )
    write_subagent(
        subagents / "workflows" / WORKFLOW_RUN,
        "mmm-nested",
        NESTED_META,
        [_sub_event("user", "m")],
    )
    write_subagent(
        storage / "bbb-sess" / "subagents",
        "bbb-solo",
        {"agentType": "t", "spawnDepth": 1},
        [_sub_event("user", "b")],
    )

    raw = ClaudeCodeAdapter.for_project(
        project_dir, home=claude_home, include_subagents=True
    ).ingest()

    assert [(t.kind, t.id) for t in raw.transcript_ids] == [
        ("session", "aaa-sess"),
        ("subagent", "zzz-late"),
        ("subagent", "aaa-deep"),
        ("subagent", "mmm-nested"),
        ("session", "bbb-sess"),
        ("subagent", "bbb-solo"),
    ]


def test_the_bare_constructor_reports_no_identities_rather_than_a_partial_list(memory_dir):
    """All-or-nothing: an adapter handed plain strings knows nothing about where they came from, and
    a partial list would renumber nothing while looking authoritative. `()` is what makes the
    driver's stamp CONDITIONAL and keeps present-and-empty from meaning absent."""
    assert ClaudeCodeAdapter(memory_dir, transcripts=["a", "b"]).ingest().transcript_ids == ()


def test_a_subagent_whose_render_is_empty_is_dropped_not_emitted_as_a_bare_header(
    tmp_path, claude_home
):
    """A header with no body is worse than nothing: it claims a transcript exists and gives the
    planner an index that reads back empty. Dropping it is also why the index is assigned in a
    second pass — the surviving set has to be known first."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    storage = seed_project(claude_home, project_dir, {"sess-1": [_event("user", "main")]})
    write_subagent(storage / "sess-1" / "subagents", "empty", FLAT_META, [{"type": "mode"}])
    write_subagent(storage / "sess-1" / "subagents", "real", FLAT_META, [_sub_event("user", "hi")])

    raw = ClaudeCodeAdapter.for_project(
        project_dir, home=claude_home, include_subagents=True
    ).ingest()

    assert [t.id for t in raw.transcript_ids] == ["sess-1", "real"]
    assert [entry.split("\n")[0].split()[0] for entry in raw.transcripts] == ["[0]", "[1]"]
