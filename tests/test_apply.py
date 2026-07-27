"""`apply_plan` against a REAL filesystem — the one module that mutates disk.

Everything here is offline and model-free: applying a plan is plain host-side file I/O (no RLM, no
sandbox, no rlm-kit tooling involved), so the tests build actual files under `tmp_path` and assert on
what is on disk afterwards. The adversarial cases mirror the read side's: a symlink escaping
`memory_dir` (see `test_adapters_claude_code.py`), and a target path that is not in the authoritative
index.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter
from ctx_distillery.apply import (
    ARCHIVE_DIRNAME,
    _archive_destination,
    _skill_target,
    apply_plan,
    slugify,
)
from ctx_distillery.session import AssembledCandidate, AssembledPlan

#: The drafted bytes of one candidate skill — a real `SKILL.md` shape (`name` + `description` only;
#: `when_to_use` / `dispatch_intent` are optional and deliberately absent here).
SKILL_DRAFT = "---\nname: Deploy Runbook\ndescription: How to deploy.\n---\nSteps.\n"


def memory_draft(name: str, *, body: str = "Some durable fact.\n") -> str:
    # `name` is quoted so a deliberately awkward test name (e.g. "!!!") is still valid YAML — the
    # point of those cases is the SLUG being degenerate, not the frontmatter failing to parse.
    return (
        f"---\n"
        f'name: "{name}"\n'
        f"description: A durable thing worth remembering.\n"
        f"metadata:\n"
        f"  type: project\n"
        f"---\n"
        f"{body}"
    )


def promotion(name: str, *, action: str = "promote_to_memory", **kw) -> AssembledCandidate:
    """A promotion candidate as `assemble()` would hand it over: draft present, format check passed."""
    kw.setdefault("draft", memory_draft(name))
    kw.setdefault("draft_ok", True)
    return AssembledCandidate(action=action, artifact_id="artifact-1", **kw)


def skill_promotion(*, scope: str = "global", draft: str = SKILL_DRAFT, **kw) -> AssembledCandidate:
    """A `promote_to_skill` candidate, carrying the `key_fields["scope"]` the convention requires."""
    key_fields = {"scope": scope, **kw.pop("key_fields", {})}
    return promotion("ignored", action="promote_to_skill", draft=draft, key_fields=key_fields, **kw)


def prune(target_path) -> AssembledCandidate:
    return AssembledCandidate(action="prune", key_fields={"target_path": str(target_path)})


def plan(*candidates: AssembledCandidate) -> AssembledPlan:
    return AssembledPlan(candidates=list(candidates))


def names_in(memory_dir) -> set[str]:
    return {ref.name for ref in ClaudeCodeAdapter(memory_dir).list_targets()}


# -- slugify (gap #3: filename derivation was unspecified) ---------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("api-notes", "api-notes"),
        ("API Notes", "api-notes"),
        ("deploy_runbook", "deploy-runbook"),
        ("Release  Process!", "release-process"),
        ("../../etc/passwd", "etcpasswd"),
        ("nested/path.md", "nestedpathmd"),
        ("  Trim Me  ", "trim-me"),
    ],
)
def test_slugify_keeps_only_lowercase_alnum_and_hyphens(raw, expected):
    assert slugify(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "!!!", "。。", "///"])
def test_slugify_degenerates_to_empty_rather_than_inventing_a_name(raw):
    assert slugify(raw) == ""


def test_a_name_that_slugifies_to_nothing_is_a_hard_refusal(memory_dir):
    outcomes = apply_plan(memory_dir, plan(promotion("!!!")), [0])
    assert outcomes[0].status == "refused"
    assert "slugifies to nothing" in outcomes[0].reason
    # No file appeared under any name — not `.md`, not a bare slug.
    assert sorted(p.name for p in memory_dir.iterdir()) == [
        "MEMORY.md",
        "conventions.md",
        "user-prefs.md",
    ]


# -- a clean promotion ----------------------------------------------------------------------------


def test_a_clean_promotion_writes_the_assembled_draft_verbatim(memory_dir):
    candidate = promotion("API Notes")
    outcomes = apply_plan(memory_dir, plan(candidate), [0])

    assert [o.status for o in outcomes] == ["applied"]
    written = memory_dir / "api-notes.md"
    assert Path(outcomes[0].path) == written.resolve()
    # Verbatim: apply writes the assembled bytes, it never re-authors or reformats them.
    assert written.read_text(encoding="utf-8") == candidate.draft
    assert "API Notes" in names_in(memory_dir)


def test_a_skill_promotion_takes_a_DIFFERENT_write_path_than_a_memory_one(memory_dir, tmp_path):
    """Replaces the old `test_a_skill_promotion_takes_the_same_write_path`, which pinned behaviour
    that primary-source research showed to be wrong (`docs/DESIGN.md`, gap #6).

    A skill is NOT a flat `<slug>.md` in the memory store. It is `<skills_root>/<slug>/SKILL.md` —
    one directory deeper, under a root that is never `memory_dir`. The old flat containment check
    (`resolved.parent == memory_dir`) would have refused every legitimate skill write, so this needed
    an architecture fix rather than a new path string, and the test has to assert the real shape.
    """
    skill = "---\nname: Deploy Runbook\ndescription: How to deploy.\n---\nSteps.\n"
    skills_root = tmp_path / "fake-home" / ".claude" / "skills"

    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(draft=skill)),
        [0],
        global_skills_dir=skills_root,
    )

    assert outcomes[0].status == "applied", outcomes[0].reason
    written = skills_root / "deploy-runbook" / "SKILL.md"
    assert written.read_text(encoding="utf-8") == skill
    assert Path(outcomes[0].path) == written.resolve()
    # …and NOTHING landed in the memory store: the two kinds have separate roots now.
    assert sorted(p.name for p in memory_dir.iterdir()) == [
        "MEMORY.md",
        "conventions.md",
        "user-prefs.md",
    ]


def test_a_project_scoped_skill_goes_to_the_project_root_not_the_global_one(memory_dir, tmp_path):
    skill = "---\nname: This Repos Release\ndescription: How this repo ships.\n---\nSteps.\n"
    global_root = tmp_path / "global-skills"
    project_root = tmp_path / "proj" / ".claude" / "skills"

    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(scope="project", draft=skill)),
        [0],
        global_skills_dir=global_root,
        project_skills_dir=project_root,
    )

    assert outcomes[0].status == "applied", outcomes[0].reason
    assert (project_root / "this-repos-release" / "SKILL.md").read_text(encoding="utf-8") == skill
    assert not global_root.exists(), "a project-scoped skill must never touch the global store"


def test_the_first_skill_in_a_brand_new_root_notes_the_restart_caveat(memory_dir, tmp_path):
    """Empirically confirmed: an EXISTING skills root picks up a new skill mid-session, but a
    project's very FIRST top-level skills directory needs a Claude Code restart to be discovered."""
    project_root = tmp_path / "proj" / ".claude" / "skills"
    assert not project_root.exists()

    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(scope="project")),
        [0],
        project_skills_dir=project_root,
    )

    assert outcomes[0].status == "applied", outcomes[0].reason
    assert "restart" in outcomes[0].reason


# -- apply-time shadow check: a stale/hand-built plan is not the draft-time validator's job --------


def test_a_project_skill_matching_an_ALREADY_existing_global_one_is_refused_at_apply_time(
    memory_dir, tmp_path
):
    """`apply_plan`'s own fresh re-scan is the sole collision authority (gap #2) — a `project`
    candidate whose name a `global` skill already occupies must be refused HERE too, not only by
    `make_skill_validator` at draft time, since a plan can be applied long after drafting or be
    built without going through the drafting tool at all."""
    global_root = tmp_path / "global-skills"
    project_root = tmp_path / "proj" / ".claude" / "skills"
    existing = global_root / "shared-name" / "SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("---\nname: Shared Name\ndescription: d.\n---\nBody.\n", encoding="utf-8")

    draft = "---\nname: Shared Name\ndescription: A project one.\n---\nBody.\n"
    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(scope="project", draft=draft)),
        [0],
        global_skills_dir=global_root,
        project_skills_dir=project_root,
    )

    assert outcomes[0].status == "refused"
    assert "shadow" in outcomes[0].reason.lower()
    assert not (project_root / "shared-name").exists()


def test_overwrite_does_not_bypass_the_shadow_refusal(memory_dir, tmp_path):
    """`overwrite` is the escape hatch for replacing THIS candidate's own target file — it must not
    also mean "install a project skill that can never actually be reached."""
    global_root = tmp_path / "global-skills"
    project_root = tmp_path / "proj" / ".claude" / "skills"
    existing = global_root / "shared-name" / "SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("---\nname: Shared Name\ndescription: d.\n---\nBody.\n", encoding="utf-8")

    draft = "---\nname: Shared Name\ndescription: A project one.\n---\nBody.\n"
    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(scope="project", draft=draft)),
        [0],
        overwrite_ids=[0],
        global_skills_dir=global_root,
        project_skills_dir=project_root,
    )

    assert outcomes[0].status == "refused"
    assert "shadow" in outcomes[0].reason.lower()


def test_a_same_call_global_then_project_promotion_of_the_same_name_is_still_caught(
    memory_dir, tmp_path
):
    """The pre-call re-scan cannot know about a write THIS SAME call is about to make — the shadow
    set has to grow live as candidates are applied in order, not just start from what pre-existed."""
    global_root = tmp_path / "global-skills"
    project_root = tmp_path / "proj" / ".claude" / "skills"
    global_draft = "---\nname: Shared Name\ndescription: d.\n---\nBody.\n"
    project_draft = "---\nname: Shared Name\ndescription: A project one.\n---\nBody.\n"

    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(scope="global", draft=global_draft),
            skill_promotion(scope="project", draft=project_draft),
        ),
        [0, 1],
        global_skills_dir=global_root,
        project_skills_dir=project_root,
    )

    assert outcomes[0].status == "applied", outcomes[0].reason
    assert outcomes[1].status == "refused"
    assert "shadow" in outcomes[1].reason.lower()
    assert not (project_root / "shared-name").exists()


def test_a_second_skill_in_an_ALREADY_existing_root_gets_no_restart_caveat(memory_dir, tmp_path):
    project_root = tmp_path / "proj" / ".claude" / "skills"
    first = "---\nname: First Skill\ndescription: d.\n---\nBody.\n"
    apply_plan(
        memory_dir,
        plan(skill_promotion(scope="project", draft=first)),
        [0],
        project_skills_dir=project_root,
    )
    assert project_root.exists()

    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(scope="project")),
        [0],
        project_skills_dir=project_root,
    )

    assert outcomes[0].status == "applied", outcomes[0].reason
    assert "restart" not in outcomes[0].reason


@pytest.mark.parametrize("scope", ["global", "project"])
def test_a_skill_promotion_with_no_root_for_its_scope_is_refused(memory_dir, tmp_path, scope):
    """Refused, never defaulted: guessing between a user-global install and a project-local one is
    not a guess this module is entitled to make."""
    other = "project" if scope == "global" else "global"
    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(scope=scope)),
        [0],
        **{f"{other}_skills_dir": tmp_path / f"{other}-skills"},
    )
    assert outcomes[0].status == "refused"
    assert f"no {scope} skills root" in outcomes[0].reason
    assert not (tmp_path / f"{other}-skills").exists()


@pytest.mark.parametrize("scope", [None, "", "Global", "user", 7, ["global"]])
def test_a_skill_promotion_without_a_valid_scope_is_refused(memory_dir, tmp_path, scope):
    key_fields = {} if scope is None else {"scope": scope}
    outcomes = apply_plan(
        memory_dir,
        plan(promotion("x", action="promote_to_skill", draft=SKILL_DRAFT, key_fields=key_fields)),
        [0],
        global_skills_dir=tmp_path / "skills",
        project_skills_dir=tmp_path / "proj-skills",
    )
    assert outcomes[0].status == "refused"
    assert "key_fields['scope']" in outcomes[0].reason
    assert not (tmp_path / "skills").exists() and not (tmp_path / "proj-skills").exists()


@pytest.mark.parametrize("scope", ["global", "project"])
def test_an_existing_skill_directory_refuses_unless_overwritten(memory_dir, tmp_path, scope):
    root = tmp_path / f"{scope}-skills"
    (root / "deploy-runbook").mkdir(parents=True)
    (root / "deploy-runbook" / "SKILL.md").write_text("PRE-EXISTING SKILL\n", encoding="utf-8")
    roots = {f"{scope}_skills_dir": root}

    refused = apply_plan(memory_dir, plan(skill_promotion(scope=scope)), [0], **roots)
    assert refused[0].status == "refused"
    assert "name collision" in refused[0].reason
    assert (root / "deploy-runbook" / "SKILL.md").read_text(encoding="utf-8") == "PRE-EXISTING SKILL\n"

    applied = apply_plan(
        memory_dir, plan(skill_promotion(scope=scope)), [0], overwrite_ids=[0], **roots
    )
    assert applied[0].status == "applied", applied[0].reason
    assert (root / "deploy-runbook" / "SKILL.md").read_text(encoding="utf-8") == SKILL_DRAFT


@pytest.mark.parametrize("scope", ["global", "project"])
def test_a_file_sitting_where_the_skill_directory_belongs_is_refused_even_with_overwrite(
    memory_dir, tmp_path, scope
):
    """`overwrite` means "replace this draft's SKILL.md", never "replace a file with a directory"."""
    root = tmp_path / f"{scope}-skills"
    root.mkdir()
    (root / "deploy-runbook").write_text("A FILE, NOT A SKILL DIRECTORY\n", encoding="utf-8")

    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(scope=scope)),
        [0],
        overwrite_ids=[0],
        **{f"{scope}_skills_dir": root},
    )

    assert outcomes[0].status == "refused"
    assert "not a skill directory" in outcomes[0].reason
    assert (root / "deploy-runbook").read_text(encoding="utf-8") == "A FILE, NOT A SKILL DIRECTORY\n"


@pytest.mark.parametrize("scope", ["global", "project"])
def test_a_symlinked_skill_directory_cannot_redirect_the_write_outside_the_root(
    memory_dir, tmp_path, scope
):
    """The nested analogue of the flat write-side containment check: `<root>/<slug>` must resolve to a
    DIRECT child of the root, so a symlink planted there cannot host the write elsewhere."""
    root = tmp_path / f"{scope}-skills"
    root.mkdir()
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    os.symlink(outside, root / "deploy-runbook")

    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(scope=scope)),
        [0],
        overwrite_ids=[0],
        **{f"{scope}_skills_dir": root},
    )

    assert outcomes[0].status == "refused"
    assert "refusing to write outside" in outcomes[0].reason or "not a skill directory" in outcomes[0].reason
    assert list(outside.iterdir()) == [], "nothing may be written through the symlink"


@pytest.mark.parametrize("scope", ["global", "project"])
def test_a_symlinked_SKILL_md_inside_a_legitimate_directory_cannot_redirect_the_write(
    memory_dir, tmp_path, scope
):
    root = tmp_path / f"{scope}-skills"
    (root / "deploy-runbook").mkdir(parents=True)
    outside = tmp_path / "outside-secret.md"
    outside.write_text("SECRET OUTSIDE CONTENT\n", encoding="utf-8")
    os.symlink(outside, root / "deploy-runbook" / "SKILL.md")

    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(scope=scope)),
        [0],
        overwrite_ids=[0],
        **{f"{scope}_skills_dir": root},
    )

    assert outcomes[0].status == "refused"
    assert "refusing to write outside" in outcomes[0].reason
    assert outside.read_text(encoding="utf-8") == "SECRET OUTSIDE CONTENT\n"


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize("slug", ["../escape", "nested/deep", "..", ".", "a\\b"])
def test_a_slug_carrying_a_path_separator_is_refused(memory_dir, tmp_path, scope, slug):
    """`slugify` already makes a separator impossible by character class — `_skill_target` checks
    anyway, because being the check IS its job: a future caller deriving the slug differently must
    still hit a wall. Driven directly, since the tool path cannot produce such a slug."""
    root = tmp_path / f"{scope}-skills"
    root.mkdir()
    target, reason = _skill_target(root, slug, overwrite=False)
    assert target is None
    assert "path separator or traversal segment" in reason
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("slug", ["", "   ", "\t"])
def test_a_blank_slug_is_refused_rather_than_naming_the_root_itself(tmp_path, slug):
    """Same defence-in-depth reasoning as the separator check above, and the same "driven directly"
    caveat: `_promote_skill` refuses an empty slug before it ever gets here. But `root / ""` IS the
    root, and a whitespace-only slug would create a directory nobody can address, so the function
    whose job is to be the check refuses both."""
    root = tmp_path / "skills"
    root.mkdir()
    target, reason = _skill_target(root, slug, overwrite=False)
    assert target is None
    assert "is blank" in reason
    assert list(root.iterdir()) == []


def test_an_unusable_slug_is_refused_instead_of_raising_out_of_apply_plan(tmp_path):
    """An embedded NUL makes `Path.resolve()` raise ValueError, not OSError. A refusal is the right
    answer; an exception would abandon every candidate queued after this one mid-run."""
    root = tmp_path / "skills"
    root.mkdir()
    target, reason = _skill_target(root, "bad\x00slug", overwrite=False)
    assert target is None
    assert "could not resolve" in reason


@pytest.mark.parametrize("scope", ["global", "project"])
def test_a_legitimate_slug_resolves_to_the_nested_skill_md(memory_dir, tmp_path, scope):
    root = tmp_path / f"{scope}-skills"
    root.mkdir()
    target, reason = _skill_target(root, "deploy-runbook", overwrite=False)
    assert reason == ""
    assert target == (root / "deploy-runbook" / "SKILL.md").resolve()


def test_a_skill_name_that_slugifies_to_nothing_is_a_hard_refusal(memory_dir, tmp_path):
    draft = '---\nname: "!!!"\ndescription: d\n---\nBody.\n'
    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(draft=draft)),
        [0],
        global_skills_dir=tmp_path / "skills",
    )
    assert outcomes[0].status == "refused"
    assert "slugifies to nothing" in outcomes[0].reason
    assert not (tmp_path / "skills").exists()


def test_a_memory_promotion_still_ignores_the_skills_roots_entirely(memory_dir, tmp_path):
    """Roots are PER KIND: giving apply_plan a skills root must not change where a memory goes."""
    skills_root = tmp_path / "skills"
    outcomes = apply_plan(
        memory_dir, plan(promotion("API Notes")), [0], global_skills_dir=skills_root
    )
    assert outcomes[0].status == "applied"
    assert (memory_dir / "api-notes.md").is_file()
    assert not skills_root.exists()


def test_a_promotion_into_a_memory_store_that_does_not_exist_yet_still_applies(tmp_path):
    """An empty/absent memory store is a normal input — everything in it is a promotion."""
    fresh = tmp_path / "project" / "memory"
    outcomes = apply_plan(fresh, plan(promotion("first-memory")), [0])
    assert outcomes[0].status == "applied"
    assert (fresh / "first-memory.md").is_file()


def test_a_memory_store_path_that_is_a_file_refuses_rather_than_reporting_a_collision(tmp_path):
    not_a_dir = tmp_path / "memory"
    not_a_dir.write_text("this is a file, not a memory directory\n", encoding="utf-8")

    outcomes = apply_plan(not_a_dir, plan(promotion("api-notes")), [0])

    assert outcomes[0].status == "refused"
    assert "could not open the memory store" in outcomes[0].reason
    assert not_a_dir.is_file()


def test_the_memory_index_is_never_a_promotion_target(memory_dir):
    before = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    outcomes = apply_plan(memory_dir, plan(promotion("memory")), [0])
    assert outcomes[0].status == "refused"
    assert "memory index" in outcomes[0].reason
    assert (memory_dir / "MEMORY.md").read_text(encoding="utf-8") == before


# -- collisions: the re-scan reports, the exclusive create ENFORCES -------------------------------


def test_a_slugified_name_collision_refuses_and_leaves_the_existing_file_untouched(memory_dir):
    existing = memory_dir / "api-notes.md"
    existing.write_text("PRE-EXISTING CONTENT\n", encoding="utf-8")

    outcomes = apply_plan(memory_dir, plan(promotion("API Notes")), [0])

    assert outcomes[0].status == "refused"
    assert "name collision" in outcomes[0].reason
    assert existing.read_text(encoding="utf-8") == "PRE-EXISTING CONTENT\n"


def test_the_exclusive_create_is_the_actual_enforcement_not_the_re_scan(memory_dir):
    """Two candidates deriving the SAME filename in one call.

    The fresh re-scan happens once, before any write, so it cannot know about the file the first
    candidate is about to create — `open(path, "x")` (O_CREAT|O_EXCL) is what refuses the second.
    That is the point of the design's "atomic, TOCTOU-proof" note: the re-scan is a friendly early
    message, the exclusive create is the guarantee.
    """
    first = promotion("API Notes", draft=memory_draft("API Notes", body="FIRST\n"))
    second = promotion("api notes", draft=memory_draft("api notes", body="SECOND\n"))

    outcomes = apply_plan(memory_dir, plan(first, second), [0, 1])

    assert [o.status for o in outcomes] == ["applied", "refused"]
    assert "between the re-scan and the write" in outcomes[1].reason
    assert "FIRST" in (memory_dir / "api-notes.md").read_text(encoding="utf-8")


def test_overwrite_is_a_per_candidate_escape_hatch(memory_dir):
    existing = memory_dir / "api-notes.md"
    existing.write_text("PRE-EXISTING CONTENT\n", encoding="utf-8")
    also = memory_dir / "other-notes.md"
    also.write_text("ALSO PRE-EXISTING\n", encoding="utf-8")

    outcomes = apply_plan(
        memory_dir,
        plan(promotion("API Notes"), promotion("Other Notes")),
        [0, 1],
        overwrite_ids=[0],
    )

    # Scoped to candidate 0 only — candidate 1 still refuses on its own collision.
    assert [o.status for o in outcomes] == ["applied", "refused"]
    assert "overwriting" in outcomes[0].reason
    assert existing.read_text(encoding="utf-8") == memory_draft("API Notes")
    assert also.read_text(encoding="utf-8") == "ALSO PRE-EXISTING\n"


def test_overwrite_cannot_smuggle_in_an_unapproved_candidate(memory_dir):
    with pytest.raises(ValueError, match="not in approved_ids"):
        apply_plan(memory_dir, plan(promotion("api-notes")), [], overwrite_ids=[0])
    assert not (memory_dir / "api-notes.md").exists()


# -- write-side containment (gap #5 — the mirror of the read side's) -----------------------------


def test_a_symlink_in_memory_dir_cannot_redirect_a_write_outside_it(memory_dir, tmp_path):
    """The write-side mirror of `list_targets()`'s containment check.

    An adversarial review found a symlink INSIDE `memory_dir` resolves outside it and would join the
    trusted read snapshot. The write side has the identical exposure: the derived filename could
    already exist as such a symlink, and following it would write the draft outside the memory store
    entirely. Same check, same reason — `resolved.parent == memory_dir`, before any write.
    """
    outside = tmp_path / "outside-secret.md"
    outside.write_text("SECRET OUTSIDE CONTENT\n", encoding="utf-8")
    os.symlink(outside, memory_dir / "escape.md")

    outcomes = apply_plan(memory_dir, plan(promotion("escape")), [0])

    assert outcomes[0].status == "refused"
    assert "refusing to write outside" in outcomes[0].reason
    assert outside.read_text(encoding="utf-8") == "SECRET OUTSIDE CONTENT\n"


def test_containment_also_holds_for_a_symlink_pointing_at_a_nonexistent_outside_path(
    memory_dir, tmp_path
):
    """A DANGLING symlink is the nastier variant: nothing exists to collide with, so a naive
    implementation's collision check passes and the write creates the outside file itself."""
    target = tmp_path / "not-yet-there.md"
    os.symlink(target, memory_dir / "escape.md")

    outcomes = apply_plan(memory_dir, plan(promotion("escape")), [0])

    assert outcomes[0].status == "refused"
    assert "refusing to write outside" in outcomes[0].reason
    assert not target.exists()


def test_a_symlinked_memory_dir_itself_is_still_writable(memory_dir, tmp_path):
    """Containment compares against the RESOLVED memory_dir, exactly as the adapter does — so an
    operator whose `memory/` is reached through a symlink is not locked out."""
    link = tmp_path / "linked-memory"
    os.symlink(memory_dir, link)
    outcomes = apply_plan(link, plan(promotion("via-link")), [0])
    assert outcomes[0].status == "applied"
    assert (memory_dir / "via-link.md").is_file()


# -- prune: archive, never delete (gaps #1 and #4) -----------------------------------------------


def test_a_prune_with_a_valid_target_path_archives_outside_the_memory_store(memory_dir, tmp_path):
    source = memory_dir / "conventions.md"
    before = source.read_text(encoding="utf-8")

    outcomes = apply_plan(memory_dir, plan(prune(source)), [0])

    assert outcomes[0].status == "applied"
    assert not source.exists(), "the pruned file must leave the memory store"
    archived = Path(outcomes[0].path)
    # Archived, not deleted: same bytes, still on disk, recoverable by hand.
    assert archived.read_text(encoding="utf-8") == before
    assert Path(outcomes[0].source_path) == source.resolve()

    archive_dir = tmp_path / ARCHIVE_DIRNAME
    assert archived.parent == archive_dir
    assert archived.name.endswith("-conventions.md")


def test_the_archive_lives_outside_memory_dir_and_is_never_re_enumerated(memory_dir, tmp_path):
    """Gap #4: an archive INSIDE `memory_dir` would eventually be re-discovered as live — by
    `list_targets()`'s own glob, or by `rlm_kit.skills.discover_skills`'s `*/SKILL.md` walk."""
    source = memory_dir / "conventions.md"
    outcomes = apply_plan(memory_dir, plan(prune(source)), [0])
    archived = Path(outcomes[0].path)

    assert memory_dir.resolve() not in archived.parents
    assert not archived.is_relative_to(memory_dir.resolve())
    assert (tmp_path / ARCHIVE_DIRNAME).is_dir()

    after = ClaudeCodeAdapter(memory_dir).list_targets()
    assert "project-conventions" not in {ref.name for ref in after}
    assert all(not Path(ref.path).is_relative_to(tmp_path / ARCHIVE_DIRNAME) for ref in after)
    # …and the rest of the store is untouched.
    assert {ref.name for ref in after} == {"user-preferences", "MEMORY.md"}


def test_a_prune_with_no_target_path_is_refused(memory_dir):
    outcomes = apply_plan(
        memory_dir, plan(AssembledCandidate(action="prune", key_fields={"reason": "stale"})), [0]
    )
    assert outcomes[0].status == "refused"
    assert "target_path" in outcomes[0].reason
    assert names_in(memory_dir) == {"project-conventions", "user-preferences", "MEMORY.md"}


@pytest.mark.parametrize("value", [None, "", "   ", 17, ["a/path"]])
def test_a_prune_whose_target_path_is_not_a_usable_string_is_refused(memory_dir, value):
    candidate = AssembledCandidate(action="prune", key_fields={"target_path": value})
    outcomes = apply_plan(memory_dir, plan(candidate), [0])
    assert outcomes[0].status == "refused"
    assert names_in(memory_dir) == {"project-conventions", "user-preferences", "MEMORY.md"}


def test_a_prune_targeting_a_path_outside_the_fresh_index_is_refused(memory_dir, tmp_path):
    outside = tmp_path / "outside-secret.md"
    outside.write_text("SECRET OUTSIDE CONTENT\n", encoding="utf-8")

    outcomes = apply_plan(memory_dir, plan(prune(outside)), [0])

    assert outcomes[0].status == "refused"
    assert "does not exactly match" in outcomes[0].reason
    assert outside.is_file(), "a non-indexed path must never be touched"
    assert not (tmp_path / ARCHIVE_DIRNAME).exists()


def test_a_prune_targeting_a_file_that_no_longer_exists_is_refused(memory_dir):
    outcomes = apply_plan(memory_dir, plan(prune(memory_dir / "deleted-last-week.md")), [0])
    assert outcomes[0].status == "refused"
    assert "does not exactly match" in outcomes[0].reason


def test_a_prune_targeting_a_stale_path_from_an_older_run_is_refused(memory_dir, tmp_path):
    """Gap #2 in its sharpest form: the plan was produced against an EARLIER state of the store.

    A candidate that named a file which has since been renamed/removed must not be silently
    reinterpreted — the fresh re-scan is the authority, and a miss is a refusal.
    """
    stale = memory_dir / "conventions.md"
    stale.rename(memory_dir / "renamed-conventions.md")

    outcomes = apply_plan(memory_dir, plan(prune(stale)), [0])

    assert outcomes[0].status == "refused"
    assert (memory_dir / "renamed-conventions.md").is_file()


def test_a_prune_targeting_the_memory_index_is_refused(memory_dir):
    index = memory_dir / "MEMORY.md"
    before = index.read_text(encoding="utf-8")

    outcomes = apply_plan(memory_dir, plan(prune(index)), [0])

    assert outcomes[0].status == "refused"
    assert "memory index" in outcomes[0].reason
    assert index.read_text(encoding="utf-8") == before


def test_a_second_prune_of_the_same_target_refuses_instead_of_clobbering(memory_dir, tmp_path):
    source = memory_dir / "conventions.md"
    outcomes = apply_plan(memory_dir, plan(prune(source), prune(source)), [0, 1])
    assert [o.status for o in outcomes] == ["applied", "refused"]
    assert len(list((tmp_path / ARCHIVE_DIRNAME).iterdir())) == 1


def test_archiving_never_overwrites_an_earlier_archive(tmp_path):
    """Same timestamp + same basename must not turn "still recoverable" into a silent delete."""
    archive = tmp_path / ARCHIVE_DIRNAME
    archive.mkdir()
    (archive / "20260727T000000Z-notes.md").write_text("EARLIER ARCHIVE\n", encoding="utf-8")

    destination = _archive_destination(archive, "notes.md", "20260727T000000Z")

    assert destination.name == "20260727T000000Z-2-notes.md"
    assert (archive / "20260727T000000Z-notes.md").read_text(encoding="utf-8") == "EARLIER ARCHIVE\n"


# -- refused regardless of action kind ------------------------------------------------------------


def test_a_candidate_with_problems_is_refused_even_when_approved(memory_dir):
    candidate = promotion("api-notes", problems=["no draft_memory_file tool_call for artifact_id"])
    outcomes = apply_plan(memory_dir, plan(candidate), [0])
    assert outcomes[0].status == "refused"
    assert "carries problems" in outcomes[0].reason
    assert not (memory_dir / "api-notes.md").exists()


def test_a_pruning_candidate_with_problems_is_refused_too(memory_dir):
    """`problems` blocks EVERY action kind, not just promotions."""
    candidate = AssembledCandidate(
        action="prune",
        key_fields={"target_path": str(memory_dir / "conventions.md")},
        problems=["action 'prune' carries artifact_id 'x'"],
    )
    outcomes = apply_plan(memory_dir, plan(candidate), [0])
    assert outcomes[0].status == "refused"
    assert (memory_dir / "conventions.md").is_file()


def test_a_draft_that_failed_its_format_check_is_refused(memory_dir):
    """`draft_ok is False` blocks the write even if the caller approved the candidate."""
    candidate = AssembledCandidate(
        action="promote_to_memory",
        artifact_id="artifact-1",
        draft=memory_draft("api-notes"),
        draft_ok=False,
    )
    outcomes = apply_plan(memory_dir, plan(candidate), [0])
    assert outcomes[0].status == "refused"
    assert "format check" in outcomes[0].reason
    assert not (memory_dir / "api-notes.md").exists()


@pytest.mark.parametrize("draft", [None, "", "   \n\n"])
def test_a_promotion_with_an_empty_draft_is_refused(memory_dir, draft):
    candidate = AssembledCandidate(
        action="promote_to_memory", artifact_id="artifact-1", draft=draft, draft_ok=True
    )
    outcomes = apply_plan(memory_dir, plan(candidate), [0])
    assert outcomes[0].status == "refused"
    assert "nothing to write" in outcomes[0].reason


def test_a_promotion_whose_draft_has_no_frontmatter_name_is_refused(memory_dir):
    candidate = AssembledCandidate(
        action="promote_to_memory",
        artifact_id="artifact-1",
        draft="no frontmatter at all, just prose\n",
        draft_ok=True,
    )
    outcomes = apply_plan(memory_dir, plan(candidate), [0])
    assert outcomes[0].status == "refused"
    assert "no usable `name`" in outcomes[0].reason
    assert sorted(p.name for p in memory_dir.glob("*.md")) == [
        "MEMORY.md",
        "conventions.md",
        "user-prefs.md",
    ]


def test_an_unknown_action_is_refused(memory_dir):
    outcomes = apply_plan(memory_dir, plan(AssembledCandidate(action="merge")), [0])
    assert outcomes[0].status == "refused"
    assert "unknown action" in outcomes[0].reason


# -- the audit record ----------------------------------------------------------------------------


def test_every_candidate_gets_an_outcome_including_the_unapproved_ones(memory_dir):
    outcomes = apply_plan(
        memory_dir,
        plan(
            promotion("api-notes"),
            promotion("rejected-notes"),
            AssembledCandidate(action="keep", key_fields={"reason": "still true"}),
            prune(memory_dir / "user-prefs.md"),
        ),
        [0, 2, 3],
    )

    assert [o.index for o in outcomes] == [0, 1, 2, 3]
    assert [o.status for o in outcomes] == ["applied", "skipped", "noop", "applied"]
    assert [o.action for o in outcomes] == [
        "promote_to_memory",
        "promote_to_memory",
        "keep",
        "prune",
    ]
    assert outcomes[0].applied is True
    assert "not approved" in outcomes[1].reason
    assert not (memory_dir / "rejected-notes.md").exists()
    assert "no-op" in outcomes[2].reason and outcomes[2].path is None


def test_an_empty_approval_set_writes_nothing(memory_dir):
    outcomes = apply_plan(memory_dir, plan(promotion("api-notes"), prune(memory_dir / "MEMORY.md")), [])
    assert [o.status for o in outcomes] == ["skipped", "skipped"]
    assert names_in(memory_dir) == {"project-conventions", "user-preferences", "MEMORY.md"}


def test_an_empty_plan_applies_nothing(memory_dir):
    assert apply_plan(memory_dir, plan(), []) == []


@pytest.mark.parametrize("approved", [[5], [-1], [0, 99], ["0"], [True], [1.0]])
def test_an_approval_that_addresses_no_candidate_raises_before_any_write(memory_dir, approved):
    """Fail loudly and BEFORE mutating: a mis-typed approval must not quietly apply nothing — or,
    worse, apply something the operator did not mean to name."""
    with pytest.raises(ValueError, match="candidate list indices"):
        apply_plan(memory_dir, plan(promotion("api-notes")), approved)
    assert not (memory_dir / "api-notes.md").exists()


def test_apply_plan_accepts_a_set_a_list_or_a_tuple_of_indices(memory_dir):
    for approved in ({0}, [0], (0,)):
        for path in memory_dir.glob("api-notes.md"):
            path.unlink()
        outcomes = apply_plan(memory_dir, plan(promotion("api-notes")), approved)
        assert outcomes[0].status == "applied"


def test_apply_plan_never_mutates_the_plan_it_is_given(memory_dir):
    candidate = promotion("api-notes")
    given = plan(candidate)
    apply_plan(memory_dir, given, [0])
    assert given.candidates == [candidate]
    assert candidate.draft == memory_draft("api-notes")
    assert candidate.problems == []
