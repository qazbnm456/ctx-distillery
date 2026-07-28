"""`list_memory_files` / `read_memory_file` — REPL safety, tracing, and the allowlist invariant.

The allowlist cases are the important ones: an EXACT resolved-path match against the snapshot is
what makes "the planner can only read files this run enumerated" true, and a prefix/substring check
would pass a naive test while still letting a symlink or a same-named outsider through. Both of those
are tested explicitly, not just an absolute-nonexistent-path smoke case.
"""

from __future__ import annotations

import os
from pathlib import Path

from rlm_kit.testing import assert_repl_safe
from rlm_kit.trace import EVENT_TOOL_CALL, TraceRecorder, load_events

from ctx_distillery.tools.memory_reader import (
    MAX_READ_CHARS,
    make_list_memory_files_tool,
    make_read_memory_file_tool,
)


def _payloads(path, tool):
    return [
        e["payload"]
        for e in load_events(path)
        if e["type"] == EVENT_TOOL_CALL and e["payload"].get("tool") == tool
    ]


def test_both_tools_are_repl_safe(snapshot):
    assert_repl_safe(make_list_memory_files_tool(snapshot))
    assert_repl_safe(make_read_memory_file_tool(snapshot))


def test_list_memory_files_returns_the_snapshot_and_records_only_the_count(snapshot, tmp_path):
    tool = make_list_memory_files_tool(snapshot)
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        entries = tool()
    assert {e["name"] for e in entries} == {"project-conventions", "user-preferences", "MEMORY.md"}
    assert set(entries[0]) == {"name", "description", "kind", "path"}
    payload = _payloads(trace, "list_memory_files")[0]
    assert payload["ok"] is True and payload["count"] == 3
    assert payload["kinds"] == ["index", "memory"]
    assert "entries" not in payload  # size, not body


def test_the_tool_holds_a_snapshot_not_a_live_list(snapshot):
    tool = make_list_memory_files_tool(snapshot)
    snapshot.clear()
    assert len(tool()) == 3


def test_read_memory_file_reads_an_enumerated_path_and_records_size_not_body(snapshot, tmp_path):
    tool = make_read_memory_file_tool(snapshot)
    target = next(ref for ref in snapshot if ref.name == "project-conventions")
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        text = tool(target.path)
    assert "Run ruff before pushing." in text
    payload = _payloads(trace, "read_memory_file")[0]
    assert payload["ok"] is True and payload["name"] == "project-conventions"
    assert payload["chars"] == len(text) and payload["truncated"] is False
    assert "Run ruff" not in str(payload)


def test_the_memory_index_file_itself_is_readable(snapshot):
    """Flagging candidate index lines only works if the planner can read `MEMORY.md` itself."""
    tool = make_read_memory_file_tool(snapshot)
    index = next(ref for ref in snapshot if ref.kind == "index")
    assert "- project-conventions" in tool(index.path)


def test_refuses_a_path_outside_the_snapshot(snapshot, tmp_path):
    tool = make_read_memory_file_tool(snapshot)
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        out = tool("/etc/passwd")
    assert out.startswith("refused:")
    assert _payloads(trace, "read_memory_file")[0]["ok"] is False


def test_refuses_a_same_named_file_in_a_different_directory(snapshot, tmp_path):
    """A substring/basename check would pass this file through; an exact-path match refuses it."""
    decoy_dir = tmp_path / "elsewhere"
    decoy_dir.mkdir()
    decoy = decoy_dir / "conventions.md"
    decoy.write_text("---\nname: project-conventions\n---\nATTACKER CONTENT\n", encoding="utf-8")
    tool = make_read_memory_file_tool(snapshot)
    out = tool(str(decoy))
    assert out.startswith("refused:")
    assert "ATTACKER CONTENT" not in out


def test_refuses_a_symlink_planted_inside_the_memory_dir(snapshot, memory_dir, tmp_path):
    """A PREFIX check on the memory dir would pass this; resolution + exact match refuses it."""
    outside = tmp_path / "outside-secret.md"
    outside.write_text("SECRET OUTSIDE CONTENT\n", encoding="utf-8")
    link = memory_dir / "sneaky.md"
    os.symlink(outside, link)
    # NOTE: the snapshot was taken BEFORE the symlink appeared — which is exactly the mid-run
    # allowlist drift the snapshot design prevents.
    tool = make_read_memory_file_tool(snapshot)
    out = tool(str(link))
    assert out.startswith("refused:")
    assert "SECRET OUTSIDE CONTENT" not in out


def test_refuses_a_traversal_path_that_lands_on_a_real_but_unlisted_file(snapshot, memory_dir):
    tool = make_read_memory_file_tool(snapshot)
    traversal = str(memory_dir / ".." / "memory" / ".." / ".." / "etc" / "hosts")
    assert tool(traversal).startswith("refused:")


def test_a_traversal_path_that_resolves_INTO_the_snapshot_is_allowed(snapshot, memory_dir):
    """Exact-match-after-resolve is not path-string matching: an odd but equivalent path is fine."""
    tool = make_read_memory_file_tool(snapshot)
    equivalent = str(memory_dir / "sub" / ".." / "conventions.md")
    assert "Run ruff before pushing." in tool(equivalent)


def test_reports_a_read_error_instead_of_raising(snapshot, memory_dir, tmp_path):
    tool = make_read_memory_file_tool(snapshot)
    target = next(ref for ref in snapshot if ref.name == "user-preferences")
    Path(target.path).unlink()  # the file vanished after the snapshot was taken
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        out = tool(target.path)
    assert out.startswith("error:")
    assert _payloads(trace, "read_memory_file")[0]["ok"] is False


def test_an_oversized_file_is_truncated_not_streamed_whole(memory_dir):
    big = memory_dir / "big.md"
    big.write_text("x" * (MAX_READ_CHARS + 500), encoding="utf-8")
    from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter

    snapshot = ClaudeCodeAdapter(memory_dir).list_targets()
    tool = make_read_memory_file_tool(snapshot)
    out = tool(str(big.resolve()))
    assert out.endswith("[...truncated]")
    assert len(out) < MAX_READ_CHARS + 500


def test_the_tools_work_with_no_recorder_active(snapshot):
    """`record_tool_call` no-ops without a recorder, so a bare call must not blow up."""
    assert make_list_memory_files_tool(snapshot)()
    assert make_read_memory_file_tool(snapshot)("/nope").startswith("refused:")
