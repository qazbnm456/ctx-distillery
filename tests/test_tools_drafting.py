"""`draft_memory_file` / `draft_skill_file` — validation, collision refusal, and the trace contract.

Two things this file exists to pin down beyond the happy path:

* the validators return `FormatCheck` (`.ok`/`.errors` ATTRIBUTES). A bare dict would make
  `make_model_tool`'s `getattr(validated, "ok", False)` read every draft as invalid and trip the
  circuit breaker after three calls — silently. There is a direct test for that below.
* the FULL drafted text lands on the `tool_call` event, because that event is what
  `session.assemble` re-sources the verbatim bytes from.
"""

from __future__ import annotations

from rlm_kit.testing import assert_repl_safe
from rlm_kit.trace import EVENT_TOOL_CALL, TraceRecorder, load_events

from ctx_distillery.adapters.base import ArtifactRef
from ctx_distillery.tools.drafting import (
    MAX_CONSECUTIVE_INVALID,
    FormatCheck,
    make_draft_memory_file_tool,
    make_draft_skill_file_tool,
    make_memory_validator,
    make_skill_validator,
)

_GOOD_MEMORY = (
    "---\n"
    "name: merge-freeze\n"
    "description: The project froze merges for the 0.2 release.\n"
    "metadata:\n"
    "  type: project\n"
    "---\n"
    "Merges are frozen until the 0.2 release ships.\n"
)

_GOOD_SKILL = (
    "---\n"
    "name: rerun-flaky-ci\n"
    "description: How to re-run only the flaky CI job.\n"
    "---\n"
    "1. Open the failed run.\n2. Re-run the single job, not the whole matrix.\n"
)


def _payloads(path, tool):
    return [
        e["payload"]
        for e in load_events(path)
        if e["type"] == EVENT_TOOL_CALL and e["payload"].get("tool") == tool
    ]


def _chat(text):
    return lambda spec: text


# -- validators ------------------------------------------------------------------------------


def test_both_validators_return_FormatCheck_never_a_dict(snapshot):
    memory = make_memory_validator(snapshot, lambda: "project")(_GOOD_MEMORY)
    skill = make_skill_validator(snapshot)(_GOOD_SKILL)
    for check in (memory, skill):
        assert isinstance(check, FormatCheck)
        assert not isinstance(check, dict)
        # exactly the two attributes make_model_tool reads
        assert check.ok is True and check.errors == []
        assert getattr(check, "ok", False) is True


def test_memory_validator_rejects_a_missing_or_bad_metadata_type(snapshot):
    validate = make_memory_validator(snapshot)
    no_meta = validate("---\nname: n\ndescription: d\n---\nbody\n")
    assert no_meta.ok is False and any("metadata" in e for e in no_meta.errors)
    bad = validate("---\nname: n\ndescription: d\nmetadata:\n  type: wat\n---\nbody\n")
    assert bad.ok is False and any("metadata.type" in e for e in bad.errors)


def test_memory_validator_cross_checks_the_requested_memory_type(snapshot):
    validate = make_memory_validator(snapshot, lambda: "user")
    check = validate(_GOOD_MEMORY)  # frontmatter says project, the planner asked for user
    assert check.ok is False
    assert any("does not match the requested memory_type" in e for e in check.errors)


def test_memory_validator_refuses_a_name_that_collides_with_an_existing_file(snapshot):
    colliding = _GOOD_MEMORY.replace("name: merge-freeze", "name: Project-Conventions")
    check = make_memory_validator(snapshot, lambda: "project")(colliding)
    assert check.ok is False
    assert any("collides with an existing memory file" in e for e in check.errors)


def test_memory_validator_rejects_an_empty_body_and_empty_draft(snapshot):
    validate = make_memory_validator(snapshot, lambda: "project")
    assert validate("").ok is False
    header_only = validate("---\nname: n\ndescription: d\nmetadata:\n  type: project\n---\n")
    assert header_only.ok is False and any("empty body" in e for e in header_only.errors)


def test_memory_validator_rejects_unparseable_frontmatter(snapshot):
    check = make_memory_validator(snapshot)("no frontmatter, just prose\n")
    assert check.ok is False and any("frontmatter" in e for e in check.errors)


def test_skill_validator_needs_name_and_description_only(snapshot):
    validate = make_skill_validator(snapshot)
    assert validate(_GOOD_SKILL).ok is True
    assert validate("---\nname: only-a-name\n---\nbody\n").ok is False


def test_skill_collision_is_checked_against_kind_skill_refs():
    existing = [ArtifactRef(name="rerun-flaky-ci", description="d", kind="skill", path="/x/s.md")]
    check = make_skill_validator(existing)(_GOOD_SKILL)
    assert check.ok is False and any("collides with an existing skill" in e for e in check.errors)


def test_a_memory_name_does_not_collide_with_a_skill_of_the_same_name(snapshot):
    """The two namespaces are separate — a memory file and a skill may share a name."""
    refs = list(snapshot) + [
        ArtifactRef(name="merge-freeze", description="d", kind="skill", path="/x/s.md")
    ]
    assert make_memory_validator(refs, lambda: "project")(_GOOD_MEMORY).ok is True


# -- tools -----------------------------------------------------------------------------------


def test_both_tools_are_repl_safe(snapshot):
    assert_repl_safe(make_draft_memory_file_tool(_chat(_GOOD_MEMORY), snapshot))
    assert_repl_safe(make_draft_skill_file_tool(_chat(_GOOD_SKILL), snapshot))


def test_draft_memory_file_records_the_full_text_keyed_by_artifact_id(snapshot, tmp_path):
    tool = make_draft_memory_file_tool(_chat(_GOOD_MEMORY), snapshot)
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        out = tool("the merge freeze", "project", "the user said merges are frozen")
    assert out["ok"] is True and out["errors"] == [] and out["draft"] == _GOOD_MEMORY
    assert set(out) == {"artifact_id", "ok", "errors", "draft"}
    payload = _payloads(trace, "draft_memory_file")[0]
    assert payload["artifact_id"] == out["artifact_id"]
    assert payload["kind"] == "memory" and payload["draft"] == _GOOD_MEMORY
    assert payload["args"]["memory_type"] == "project"


def test_draft_skill_file_records_the_full_text_keyed_by_artifact_id(snapshot, tmp_path):
    tool = make_draft_skill_file_tool(_chat(_GOOD_SKILL), snapshot)
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        out = tool("re-running one flaky CI job", "the session did this twice")
    payload = _payloads(trace, "draft_skill_file")[0]
    assert out["ok"] is True and payload["kind"] == "skill"
    assert payload["draft"] == _GOOD_SKILL and payload["artifact_id"] == out["artifact_id"]


def test_each_call_mints_a_distinct_artifact_id(snapshot):
    tool = make_draft_memory_file_tool(_chat(_GOOD_MEMORY), snapshot)
    first = tool("a", "project", "e")["artifact_id"]
    second = tool("b", "project", "e")["artifact_id"]
    assert first != second


def test_an_invalid_draft_comes_back_ok_false_with_errors_and_is_still_recorded(snapshot, tmp_path):
    tool = make_draft_memory_file_tool(_chat("just prose, no frontmatter\n"), snapshot)
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        out = tool("t", "project", "e")
    assert out["ok"] is False and out["errors"]
    payload = _payloads(trace, "draft_memory_file")[0]
    assert payload["ok"] is False and payload["draft"] == "just prose, no frontmatter\n"


def test_the_circuit_breaker_trips_after_repeated_invalid_drafts(snapshot):
    calls = {"n": 0}

    def chat(spec):
        calls["n"] += 1
        return "invalid\n"

    tool = make_draft_memory_file_tool(chat, snapshot)
    for _ in range(MAX_CONSECUTIVE_INVALID):
        assert tool("t", "project", "e")["ok"] is False
    broken = tool("t", "project", "e")
    assert broken["ok"] is False
    assert any("circuit breaker" in e for e in broken["errors"])
    assert calls["n"] == MAX_CONSECUTIVE_INVALID   # the model was NOT called again


def test_a_valid_draft_keeps_the_breaker_from_tripping(snapshot):
    """Regression guard for issue #4: a dict-returning validator would trip here on call 4."""
    tool = make_draft_memory_file_tool(_chat(_GOOD_MEMORY), snapshot)
    for _ in range(MAX_CONSECUTIVE_INVALID + 2):
        assert tool("t", "project", "e")["ok"] is True


def test_an_endpoint_error_is_surfaced_not_raised(snapshot, tmp_path):
    def boom(spec):
        raise RuntimeError("endpoint down")

    tool = make_draft_skill_file_tool(boom, snapshot)
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        out = tool("p", "e")
    assert out["ok"] is False and any("endpoint down" in e for e in out["errors"])
    assert _payloads(trace, "draft_skill_file")[0]["endpoint_error"] == "endpoint down"


def test_the_tools_never_write_a_file(snapshot, memory_dir):
    """Write boundary: drafting returns TEXT ONLY — the memory dir is untouched."""
    before = sorted(p.name for p in memory_dir.iterdir())
    make_draft_memory_file_tool(_chat(_GOOD_MEMORY), snapshot)("t", "project", "e")
    make_draft_skill_file_tool(_chat(_GOOD_SKILL), snapshot)("p", "e")
    assert sorted(p.name for p in memory_dir.iterdir()) == before
