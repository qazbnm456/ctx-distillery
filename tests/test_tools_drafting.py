"""`draft_memory_file` / `draft_skill_file` — validation, collision refusal, and the trace contract.

Two things this file exists to pin down beyond the happy path:

* the validators return `FormatCheck` (`.ok`/`.errors` ATTRIBUTES). A bare dict would make
  `make_model_tool`'s `getattr(validated, "ok", False)` read every draft as invalid and trip the
  circuit breaker after three calls — silently. There is a direct test for that below.
* the FULL drafted text lands on the `tool_call` event, because that event is what
  `session.assemble` re-sources the verbatim bytes from.
"""

from __future__ import annotations

from rlm_harness.testing import assert_repl_safe
from rlm_harness.trace import EVENT_TOOL_CALL, TraceRecorder, load_events

from ctx_distillery.adapters.base import ArtifactRef
from ctx_distillery.tools.drafting import (
    MAX_CONSECUTIVE_INVALID,
    FormatCheck,
    make_draft_memory_file_tool,
    make_draft_skill_extra_file_tool,
    make_draft_skill_file_tool,
    make_memory_validator,
    make_skill_extra_file_validator,
    make_skill_validator,
)
from ctx_distillery.trace_io import draft_cause

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


def _renamed(draft: str, old_name: str, new_name: str) -> str:
    """`draft` with its frontmatter `name:` swapped — asserting the ANCHOR was actually there.

    A bare `draft.replace("name: x", "name: y")` is a HOLLOW-GREEN GENERATOR, and the failure is
    silent in exactly the tests that matter least loudly. If someone renames `_GOOD_SKILL`'s own
    `name:` field, every `.replace()` keyed to the old value becomes a NO-OP: the vector never
    reaches the thing under test. A test asserting `ok is False` then fails and you find out — but a
    test asserting `ok is True` KEEPS PASSING while checking nothing at all. Two of the tests below
    are that shape, and one of them
    (`test_a_global_draft_is_never_flagged_as_shadowed_by_a_project_skill`) guards a real safety
    rule: a project skill silently shadowed by a global one of the same name is unreachable forever.

    So the anchor's existence is asserted, not assumed. A vanished anchor becomes a loud failure
    naming the fixture, instead of a green test that stopped testing years earlier.
    """
    return _spliced(draft, f"name: {old_name}", f"name: {new_name}")


def _spliced(text: str, anchor: str, replacement: str) -> str:
    """`text` with `anchor` -> `replacement`, asserting the anchor existed. See `_renamed`."""
    assert anchor in text, (
        f"the fixture no longer contains {anchor!r} — this splice silently became a no-op, so the "
        f"test below is asserting against UNMODIFIED text. Update the anchor, don't delete it."
    )
    return text.replace(anchor, replacement)


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
    colliding = _renamed(_GOOD_MEMORY, "merge-freeze", "Project-Conventions")
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


def test_when_to_use_and_dispatch_intent_are_OPTIONAL_never_required(snapshot):
    """Corrected per audit: requiring them would generalize from ONE author's skill pack.

    All 9 real installed skills the research inspected carry both — but they are a single homogeneous
    suite, and Anthropic's own documented Agent-Skills convention requires only `name` +
    `description`. So a draft omitting them is valid, and a draft supplying them is valid too
    (they pass through verbatim — `apply.py` writes the drafted bytes, it never re-authors them).
    """
    validate = make_skill_validator(snapshot)
    assert validate(_GOOD_SKILL).ok is True, "omitting the two optionals must never fail a draft"

    with_extras = _spliced(
        _GOOD_SKILL,
        "description: How to re-run only the flaky CI job.\n",
        "description: How to re-run only the flaky CI job.\n"
        "when_to_use: When CI fails on one job only.\n"
        "dispatch_intent: rerun-one-ci-job\n",
    )
    check = validate(with_extras)
    assert check.ok is True and check.errors == []
    # Parsed and carried through, not stripped or rejected.
    assert check.meta["when_to_use"] == "When CI fails on one job only."
    assert check.meta["dispatch_intent"] == "rerun-one-ci-job"


def test_skill_collision_is_checked_against_kind_skill_refs():
    existing = [ArtifactRef(name="rerun-flaky-ci", description="d", kind="skill", path="/x/s.md")]
    check = make_skill_validator(existing)(_GOOD_SKILL)
    assert check.ok is False and any("collides with an existing skill" in e for e in check.errors)


# -- scope-aware collisions (the two skill stores are separate namespaces) ------------------------


def _skill_refs():
    return [
        ArtifactRef(name="rerun-flaky-ci", description="d", kind="skill", path="/g/s.md", scope="global"),
        ArtifactRef(name="this-repo-only", description="d", kind="skill", path="/p/s.md", scope="project"),
    ]


def test_a_global_skill_name_is_not_a_FILE_collision_for_a_project_scoped_draft():
    """`~/.claude/skills/` and `<project>/.claude/skills/` are independent stores, so the SAME name
    at the other scope is not a file collision — but for a "project" draft it is now refused for the
    DIFFERENT reason a same-name global skill would shadow it (below); a name unique to neither store
    is the case that is actually clean."""
    validate = make_skill_validator(_skill_refs(), lambda: "project")
    unique = _renamed(_GOOD_SKILL, "rerun-flaky-ci", "totally-unique-name")
    assert validate(unique).ok is True


def test_a_same_scope_name_still_collides():
    validate = make_skill_validator(_skill_refs(), lambda: "global")
    check = validate(_GOOD_SKILL)
    assert check.ok is False
    assert any("collides with an existing skill in the global scope" in e for e in check.errors)


def test_the_project_scoped_namespace_is_checked_on_its_own_terms():
    draft = _renamed(_GOOD_SKILL, "rerun-flaky-ci", "This-Repo-Only")
    assert make_skill_validator(_skill_refs(), lambda: "global")(draft).ok is True
    project = make_skill_validator(_skill_refs(), lambda: "project")(draft)
    assert project.ok is False and any("project scope" in e for e in project.errors)


def test_no_stated_scope_falls_back_to_the_conservative_superset():
    """A caller with no scope to check against gets EVERY scope's names treated as taken — weaker for
    the drafter, never wrong for the store."""
    for name in ("rerun-flaky-ci", "this-repo-only"):
        draft = _renamed(_GOOD_SKILL, "rerun-flaky-ci", name)
        assert make_skill_validator(_skill_refs())(draft).ok is False


def test_an_unrecognized_scope_is_reported_and_falls_back_to_the_superset():
    check = make_skill_validator(_skill_refs(), lambda: "gobal")(_GOOD_SKILL)
    assert check.ok is False
    assert any("scope must be one of ['global', 'project']" in e for e in check.errors)
    assert any("collides" in e for e in check.errors)


# -- precedence: a project skill sharing a name with a GLOBAL one is shadowed, not colliding ------


def test_a_project_draft_matching_an_existing_global_skill_name_is_refused_as_shadowed():
    """Not a file collision (the two stores are separate directories) but a real usability trap:
    Claude Code's confirmed precedence means the personal/global skill would always win, so the
    project skill drafted here would install and then never be reachable."""
    check = make_skill_validator(_skill_refs(), lambda: "project")(_GOOD_SKILL)
    assert check.ok is False
    assert any("shadowed" in e and "precedence" in e for e in check.errors)


def test_a_global_draft_is_never_flagged_as_shadowed_by_a_project_skill():
    """Precedence runs one way only: nothing shadows a global skill, so a global-scoped draft is
    checked for same-scope collisions only, exactly as before."""
    draft = _renamed(_GOOD_SKILL, "rerun-flaky-ci", "this-repo-only")
    check = make_skill_validator(_skill_refs(), lambda: "global")(draft)
    assert check.ok is True


def test_a_memory_name_is_scope_filtered_without_changing_anything(snapshot):
    """A memory/index ref is inherently project-scoped, so scope filtering is a no-op for memory."""
    assert {ref.scope for ref in snapshot} == {"project"}
    colliding = _renamed(_GOOD_MEMORY, "merge-freeze", "project-conventions")
    assert make_memory_validator(snapshot, lambda: "project")(colliding).ok is False


def test_a_memory_name_does_not_collide_with_a_skill_of_the_same_name(snapshot):
    """The two namespaces are separate — a memory file and a skill may share a name."""
    refs = list(snapshot) + [
        ArtifactRef(name="merge-freeze", description="d", kind="skill", path="/x/s.md")
    ]
    assert make_memory_validator(refs, lambda: "project")(_GOOD_MEMORY).ok is True


# -- make_skill_extra_file_validator: the sixth tool's format check --------------------------


def _extra_validator(kind, relative_path):
    return make_skill_extra_file_validator(lambda: kind, lambda: relative_path)


def test_a_good_reference_and_a_good_script_both_pass():
    assert _extra_validator("reference", "references/setup.md")("Some reference text.\n").ok is True
    assert _extra_validator("script", "scripts/build.sh")("#!/bin/sh\necho hi\n").ok is True


def test_an_unrecognized_kind_is_refused():
    check = _extra_validator("gibberish", "references/x.md")("text")
    assert check.ok is False
    assert any("kind must be one of" in e for e in check.errors)


def test_a_relative_path_not_matching_its_declared_kind_is_refused():
    """A planner mismatch (says kind="reference" but gives a scripts/ path, or vice versa) is caught
    at DRAFT time rather than silently writing to the wrong directory later."""
    mismatched_reference = _extra_validator("reference", "scripts/x.md")("text")
    assert mismatched_reference.ok is False
    assert any("must start with" in e for e in mismatched_reference.errors)

    mismatched_script = _extra_validator("script", "references/x.md")("text")
    assert mismatched_script.ok is False
    assert any("must start with" in e for e in mismatched_script.errors)


def test_a_reference_relative_path_must_end_in_md():
    check = _extra_validator("reference", "references/notes.txt")("text")
    assert check.ok is False
    assert any("must end in '.md'" in e for e in check.errors)


def test_a_script_relative_path_may_have_any_extension():
    for path in ("scripts/build.sh", "scripts/run.py", "scripts/tool", "scripts/lib/util.js"):
        assert _extra_validator("script", path)("content").ok is True


def test_an_empty_extra_draft_is_refused():
    check = _extra_validator("reference", "references/x.md")("   ")
    assert check.ok is False
    assert any("empty draft" in e for e in check.errors)


def test_the_extra_file_validator_needs_no_frontmatter():
    """Unlike SKILL.md/memory files, a supplementary file is plain content — no `---` block."""
    assert _extra_validator("reference", "references/x.md")("Just prose, no frontmatter.\n").ok is True


# -- tools -----------------------------------------------------------------------------------


def test_all_three_drafting_tools_are_repl_safe(snapshot):
    assert_repl_safe(make_draft_memory_file_tool(_chat(_GOOD_MEMORY), snapshot))
    assert_repl_safe(make_draft_skill_file_tool(_chat(_GOOD_SKILL), snapshot))
    assert_repl_safe(make_draft_skill_extra_file_tool(_chat("text")))


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
        out = tool("re-running one flaky CI job", "global", "the session did this twice")
    payload = _payloads(trace, "draft_skill_file")[0]
    assert out["ok"] is True and payload["kind"] == "skill"
    assert payload["draft"] == _GOOD_SKILL and payload["artifact_id"] == out["artifact_id"]
    # The declared scope is part of the audit record: `key_fields["scope"]` on the candidate must
    # match what was actually drafted against, and the trace is where a reviewer can see it.
    assert payload["args"]["scope"] == "global"


def test_the_skill_tool_passes_its_scope_to_the_validators_collision_check(tmp_path):
    """The tool-level half of the scope-aware check: a "global" request against an existing global
    name refuses as a same-scope collision; a "project" request against that SAME name refuses too,
    but for the shadowing reason (below) rather than a file collision — the tool routes `scope`
    through to the validator either way."""
    index = [
        ArtifactRef(
            name="rerun-flaky-ci", description="d", kind="skill", path="/g/s.md", scope="global"
        )
    ]
    tool = make_draft_skill_file_tool(_chat(_GOOD_SKILL), index)
    with TraceRecorder(str(tmp_path / "t.jsonl"), run_id="r0"):
        assert tool("p", "global", "e")["ok"] is False
        project_result = tool("p", "project", "e")
        assert project_result["ok"] is False
        assert any("shadowed" in e for e in project_result["errors"])


def test_draft_skill_extra_file_records_the_full_text_keyed_by_the_GIVEN_artifact_id(tmp_path):
    """Unlike the other two drafting tools, this one does NOT mint its own artifact_id — it takes
    the caller-supplied one from an earlier draft_skill_file call, because it attaches a file to an
    ALREADY-DRAFTED skill rather than authoring a new artifact."""
    tool = make_draft_skill_extra_file_tool(_chat("Some reference body.\n"))
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        out = tool("a1", "references/setup.md", "reference", "the session did this")
    assert out == {
        "artifact_id": "a1",
        "relative_path": "references/setup.md",
        "ok": True,
        "errors": [],
        "draft": "Some reference body.\n",
    }
    payload = _payloads(trace, "draft_skill_extra_file")[0]
    assert payload["artifact_id"] == "a1"
    assert payload["relative_path"] == "references/setup.md"
    assert payload["kind"] == "reference"
    assert payload["draft"] == "Some reference body.\n"


def test_a_repeat_call_with_the_same_relative_path_records_a_SECOND_tool_call(tmp_path):
    """`assemble()` is what resolves "last call per (artifact_id, relative_path) wins" — this tool
    itself just records every call; a retry is TWO tool_call events, not an in-place update."""
    tool = make_draft_skill_extra_file_tool(_chat("second draft"))
    with TraceRecorder(str(tmp_path / "t.jsonl"), run_id="r0"):
        first = tool("a1", "references/x.md", "reference", "e")
        second = tool("a1", "references/x.md", "reference", "e")
    assert first["draft"] == "second draft" and second["draft"] == "second draft"
    assert len(_payloads(str(tmp_path / "t.jsonl"), "draft_skill_extra_file")) == 2


def test_an_invalid_extra_draft_comes_back_ok_false_and_is_still_recorded(tmp_path):
    tool = make_draft_skill_extra_file_tool(_chat("   "))  # blank body
    trace = str(tmp_path / "t.jsonl")
    with TraceRecorder(trace, run_id="r0"):
        out = tool("a1", "references/x.md", "reference", "e")
    assert out["ok"] is False and out["errors"]
    payload = _payloads(trace, "draft_skill_extra_file")[0]
    assert payload["ok"] is False and payload["draft"] == "   "


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
        out = tool("p", "global", "e")
    assert out["ok"] is False and any("endpoint down" in e for e in out["errors"])
    assert _payloads(trace, "draft_skill_file")[0]["endpoint_error"] == "endpoint down"


def test_every_recorded_call_names_its_own_cause_and_whether_the_validator_RAN(snapshot, tmp_path):
    """The source records what it already knows — `CLAUDE.md` invariant 12.

    This module is the only place holding a live `ModelToolResult`; every downstream reader
    (`rl_export`, `schema`, the studio) sees a recorded payload. Reconstructing the cause from
    `ok`/`endpoint_error`/`circuit_broken` at each of those call sites is re-deriving something this
    function was TOLD, and the sibling consumer that got that derivation wrong twice is the argument.
    `validator_ran` is recorded beside it because it is the direct question behind every mislabel:
    only when it is True may a surface say the draft "failed its format check".

    All four outcomes in one trace, in order, so a future author can read the shape off the test.
    """
    trace = str(tmp_path / "t.jsonl")
    invalid = _chat("just prose, no frontmatter\n")
    with TraceRecorder(trace, run_id="r0"):
        make_draft_memory_file_tool(_chat(_GOOD_MEMORY), snapshot)("ok", "project", "e")
        make_draft_memory_file_tool(invalid, snapshot)("invalid", "project", "e")

        def boom(spec):
            raise RuntimeError("endpoint down")

        make_draft_memory_file_tool(boom, snapshot)("endpoint", "project", "e")

        breaker = make_draft_memory_file_tool(invalid, snapshot)
        for _ in range(MAX_CONSECUTIVE_INVALID + 1):
            breaker("broken", "project", "e")

    payloads = _payloads(trace, "draft_memory_file")
    observed = [(p["cause"], p["validator_ran"], p["ok"]) for p in payloads]
    assert observed[:3] == [("ok", True, True), ("invalid", True, False), ("endpoint", False, False)]
    assert observed[-1] == ("circuit_broken", False, False)
    # The recorded cause and the fields it was derived from stay CONSISTENT — a reader on either
    # side of `trace_io.draft_cause`'s fallback gets the same answer for a fresh payload.
    for payload in payloads:
        assert draft_cause(payload) == payload["cause"]
        assert draft_cause({k: v for k, v in payload.items() if k != "cause"}) == payload["cause"]


def test_the_tools_never_write_a_file(snapshot, memory_dir):
    """Write boundary: drafting returns TEXT ONLY — the memory dir is untouched."""
    before = sorted(p.name for p in memory_dir.iterdir())
    make_draft_memory_file_tool(_chat(_GOOD_MEMORY), snapshot)("t", "project", "e")
    make_draft_skill_file_tool(_chat(_GOOD_SKILL), snapshot)("p", "global", "e")
    make_draft_skill_extra_file_tool(_chat("text"))("a1", "references/x.md", "reference", "e")
    assert sorted(p.name for p in memory_dir.iterdir()) == before
