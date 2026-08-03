"""`CodexAdapter` against REAL files — the second `HarnessAdapter`, read-only ingestion only.

Every fixture here hand-builds a fake `~/.codex` (and a fake project directory) under `tmp_path` —
this module is deliberately NEVER run against a real installed Codex CLI's actual output, the same
hermeticity stance `test_adapters_claude_code.py` takes for `~/.claude`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ctx_distillery.adapters.codex import (
    _MAX_META_SCAN_LINES,
    AGENTS_MD_FILENAME,
    AGENTS_OVERRIDE_FILENAME,
    CodexAdapter,
    _project_root_and_search_dirs,
    _render_rollout_file,
    _rollout_cwd,
    codex_home,
    global_skills_root,
)


def _rollout_line(type_: str, payload: dict, *, timestamp: str = "2026-01-01T00:00:00Z") -> str:
    return json.dumps({"timestamp": timestamp, "type": type_, "payload": payload})


def _session_meta(cwd) -> str:
    return _rollout_line(
        "session_meta",
        {"session_id": "s1", "id": "s1", "cwd": str(cwd), "timestamp": "t", "git": {}},
    )


def _message(role: str, text: str, *, block_type: str | None = None) -> str:
    block_type = block_type or ("input_text" if role == "user" else "output_text")
    return _rollout_line(
        "response_item", {"type": "message", "role": role, "content": [{"type": block_type, "text": text}]}
    )


def _tool_call(item_type: str, *, name: str | None = None) -> str:
    payload = {"type": item_type, "call_id": "c1"}
    if name is not None:
        payload["name"] = name
    return _rollout_line("response_item", payload)


def _write_rollout(sessions_root: Path, date: str, name: str, lines: list[str]) -> Path:
    day_dir = sessions_root / date.replace("-", "/")
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-{name}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# -- _render_rollout_file: the deliberately lossy renderer --------------------------------------


def test_message_items_render_as_role_colon_text(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(
        "\n".join([_message("user", "hello"), _message("assistant", "hi there")]) + "\n",
        encoding="utf-8",
    )
    assert _render_rollout_file(path) == "user: hello\nassistant: hi there"


@pytest.mark.parametrize(
    "item_type,name,expected",
    [
        ("function_call", "shell", "[used tool: shell]"),
        ("local_shell_call", None, "[used tool: local_shell_call]"),
        ("tool_search_call", "search", "[used tool: search]"),
    ],
)
def test_tool_call_items_render_as_a_label_never_dropped_silently(tmp_path, item_type, name, expected):
    """Codex's shell/patch calls are a coding agent's actual substance — the first draft of this
    renderer dropped them to nothing, a real regression against this project's own
    `[used tool: X]` precedent for Claude Code's `tool_use` blocks."""
    path = tmp_path / "r.jsonl"
    path.write_text(_tool_call(item_type, name=name) + "\n", encoding="utf-8")
    assert _render_rollout_file(path) == expected


@pytest.mark.parametrize(
    "type_,payload",
    [
        ("session_meta", {"cwd": "/x", "git": {}}),
        ("event_msg", {"foo": "bar"}),
        ("turn_context", {}),
        ("world_state", {"full": True, "state": {}}),
        ("compacted", {}),
    ],
)
def test_non_message_non_tool_lines_are_silently_absent_never_an_error(tmp_path, type_, payload):
    path = tmp_path / "r.jsonl"
    path.write_text(_rollout_line(type_, payload) + "\n", encoding="utf-8")
    assert _render_rollout_file(path) == ""


def test_an_unrecognized_response_item_type_degrades_the_same_way(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(_rollout_line("response_item", {"type": "some_future_variant"}) + "\n", encoding="utf-8")
    assert _render_rollout_file(path) == ""


def test_a_developer_role_message_is_not_rendered(tmp_path):
    """Only user/assistant are conversational text this project's rendering represents — matching
    the same role-filter Claude Code's own renderer already applies."""
    path = tmp_path / "r.jsonl"
    path.write_text(_message("developer", "system stuff") + "\n", encoding="utf-8")
    assert _render_rollout_file(path) == ""


def test_a_missing_rollout_file_renders_empty_not_an_error(tmp_path):
    assert _render_rollout_file(tmp_path / "does-not-exist.jsonl") == ""


# -- _rollout_cwd: the field-path fix + the scan bound ------------------------------------------


def test_rollout_cwd_reads_cwd_as_a_direct_payload_key(tmp_path):
    """Pins the corrected field path: `payload["cwd"]` directly, NEVER `payload["meta"]["cwd"]` —
    the bug that would have made `for_project` match zero sessions for every project, forever."""
    path = tmp_path / "r.jsonl"
    path.write_text(_session_meta("/some/project") + "\n", encoding="utf-8")
    assert _rollout_cwd(path) == Path("/some/project").resolve()


def test_rollout_cwd_tolerates_leading_non_session_meta_lines(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(
        "\n".join([_rollout_line("turn_context", {}), _session_meta("/some/project")]) + "\n",
        encoding="utf-8",
    )
    assert _rollout_cwd(path) == Path("/some/project").resolve()


def test_rollout_cwd_is_none_when_no_session_meta_exists_at_all(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(_message("user", "hello") + "\n", encoding="utf-8")
    assert _rollout_cwd(path) is None


def test_rollout_cwd_is_none_for_a_malformed_line(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text("not json at all\n", encoding="utf-8")
    assert _rollout_cwd(path) is None


def test_rollout_cwd_is_none_for_a_missing_file(tmp_path):
    assert _rollout_cwd(tmp_path / "nope.jsonl") is None


def test_rollout_cwd_gives_up_after_the_scan_cap_never_an_unbounded_read(tmp_path):
    """A corrupted/hostile file that never yields a session_meta line must not force an unbounded
    per-file read — this scan runs once per file across EVERY session on the machine."""
    path = tmp_path / "r.jsonl"
    junk_lines = [_rollout_line("event_msg", {"n": i}) for i in range(_MAX_META_SCAN_LINES + 10)]
    junk_lines.append(_session_meta("/some/project"))  # past the cap — must never be reached
    path.write_text("\n".join(junk_lines) + "\n", encoding="utf-8")
    assert _rollout_cwd(path) is None


# -- _project_root_and_search_dirs: the shared walk ----------------------------------------------


def test_search_dirs_is_just_project_dir_when_no_git_is_found(tmp_path):
    project = tmp_path / "no-git-anywhere"
    project.mkdir()
    assert _project_root_and_search_dirs(project) == [project.resolve()]


def test_search_dirs_walks_up_to_the_nearest_git_root_to_leaf(tmp_path):
    root = tmp_path / "repo"
    nested = root / "packages" / "app"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    assert _project_root_and_search_dirs(nested) == [
        root.resolve(),
        (root / "packages").resolve(),
        nested.resolve(),
    ]


# -- CodexAdapter.for_project: the machine-wide scan, filtered by cwd ---------------------------


def test_for_project_includes_only_rollouts_whose_cwd_matches(tmp_path):
    """THE test that most needs to exist — the entire point of the machine-wide-scan design: a
    DIFFERENT project's session recorded in the same global sessions/ tree must be excluded."""
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir()
    other_project = tmp_path / "other-proj"
    other_project.mkdir()
    sessions_root = codex_home(home=home) / "sessions"

    _write_rollout(
        sessions_root, "2026-01-01",
        "2026-01-01T00-00-00-aaa",
        [_session_meta(project), _message("user", "about this project")],
    )
    _write_rollout(
        sessions_root, "2026-01-02",
        "2026-01-02T00-00-00-bbb",
        [_session_meta(other_project), _message("user", "about a DIFFERENT project")],
    )

    adapter = CodexAdapter.for_project(project, home=home)
    transcripts = adapter.ingest().transcripts
    assert transcripts == ["user: about this project"]


def test_for_project_on_a_project_with_no_codex_storage_at_all_is_empty_not_an_error(tmp_path):
    adapter = CodexAdapter.for_project(tmp_path / "never-used", home=tmp_path / "home")
    raw = adapter.ingest()
    assert raw.transcripts == []
    assert raw.memory_index == []
    assert raw.project_instructions == ""


def test_for_project_orders_transcripts_chronologically_by_filename(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir()
    sessions_root = codex_home(home=home) / "sessions"
    _write_rollout(
        sessions_root, "2026-01-02", "2026-01-02T00-00-00-second",
        [_session_meta(project), _message("user", "second")],
    )
    _write_rollout(
        sessions_root, "2026-01-01", "2026-01-01T00-00-00-first",
        [_session_meta(project), _message("user", "first")],
    )
    transcripts = CodexAdapter.for_project(project, home=home).ingest().transcripts
    assert transcripts == ["user: first", "user: second"]


# -- AGENTS.md discovery -------------------------------------------------------------------------


def test_agents_md_is_project_dir_only_when_no_git_root_is_found(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / AGENTS_MD_FILENAME).write_text("root rules\n", encoding="utf-8")
    raw = CodexAdapter.for_project(project, home=tmp_path / "home").ingest()
    assert raw.project_instructions == "root rules\n"


def test_agents_md_walks_up_to_git_root_and_concatenates_root_to_leaf(tmp_path):
    root = tmp_path / "repo"
    nested = root / "packages" / "app"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / AGENTS_MD_FILENAME).write_text("root rules", encoding="utf-8")
    (nested / AGENTS_MD_FILENAME).write_text("leaf rules", encoding="utf-8")
    raw = CodexAdapter.for_project(nested, home=tmp_path / "home").ingest()
    assert raw.project_instructions == "root rules\n\nleaf rules"


def test_agents_override_md_wins_over_agents_md_at_the_same_directory(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / AGENTS_MD_FILENAME).write_text("plain\n", encoding="utf-8")
    (project / AGENTS_OVERRIDE_FILENAME).write_text("override\n", encoding="utf-8")
    raw = CodexAdapter.for_project(project, home=tmp_path / "home").ingest()
    assert raw.project_instructions == "override\n"


def test_agents_md_defaults_to_empty_when_none_exists(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    raw = CodexAdapter.for_project(project, home=tmp_path / "home").ingest()
    assert raw.project_instructions == ""


def test_agents_md_refuses_a_symlink_escaping_its_own_directory(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside-secret.md"
    outside.write_text("SECRET\n", encoding="utf-8")
    os.symlink(outside, project / AGENTS_MD_FILENAME)
    raw = CodexAdapter.for_project(project, home=tmp_path / "home").ingest()
    assert raw.project_instructions == ""


# -- skills: project (multi-directory walk) + global --------------------------------------------


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


def test_project_skills_enumerate_at_every_directory_in_the_walk_not_just_the_leaf(tmp_path):
    """Proves the corrected multi-directory walk — the exact gap a single-fixed-location design
    would miss."""
    root = tmp_path / "repo"
    nested = root / "packages" / "app"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    _write_skill(root / ".agents" / "skills", "root-skill")
    _write_skill(nested / ".agents" / "skills", "leaf-skill")

    refs = CodexAdapter.for_project(nested, home=tmp_path / "home").ingest().memory_index
    assert {(r.name, r.scope) for r in refs} == {("root-skill", "project"), ("leaf-skill", "project")}


def test_global_skills_enumerate_from_a_sibling_of_codex_home_not_inside_it(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "home"
    _write_skill(global_skills_root(home=home), "global-skill")

    refs = CodexAdapter.for_project(project, home=home).ingest().memory_index
    assert [(r.name, r.scope, r.kind) for r in refs] == [("global-skill", "global", "skill")]


def test_a_real_memory_md_present_on_the_fake_home_never_appears_in_list_targets(tmp_path):
    """The correctness decision, not a simplification: Codex's memory system is machine-wide, not
    per-project — asserting the OPPOSITE of enumeration so a future "helpful" re-add is caught."""
    home = tmp_path / "home"
    memories_dir = codex_home(home=home) / "memories"
    memories_dir.mkdir(parents=True)
    (memories_dir / "MEMORY.md").write_text("some consolidated memory\n", encoding="utf-8")

    refs = CodexAdapter.for_project(tmp_path / "proj", home=home).ingest().memory_index
    assert refs == []


def test_schema_for_skill_requires_name_and_description():
    adapter = CodexAdapter()
    schema = adapter.schema_for("skill")
    assert schema["required"] == ["name", "description"]


@pytest.mark.parametrize("kind", ["memory", "index"])
def test_schema_for_memory_and_index_is_empty_codex_has_no_such_concept(kind):
    adapter = CodexAdapter()
    assert adapter.schema_for(kind) == {}


def test_bare_constructor_never_reaches_into_a_real_home(tmp_path, monkeypatch):
    """Skill enumeration is OPT-IN — a bare CodexAdapter() must never silently glob a real
    ~/.codex or ~/.agents, mirroring ClaudeCodeAdapter's own bare-constructor discipline."""
    adapter = CodexAdapter()
    assert adapter.ingest() == adapter.ingest()  # both calls degrade the same, no live reach-out
    assert adapter.list_targets() == []


def test_harness_name_is_codex_and_distinct_from_claude_code():
    from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter

    assert CodexAdapter.harness_name == "codex"
    assert CodexAdapter.harness_name != ClaudeCodeAdapter.harness_name
