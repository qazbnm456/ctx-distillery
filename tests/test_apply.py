"""`apply_plan` against a REAL filesystem — the one module that mutates disk.

Everything here is offline and model-free: applying a plan is plain host-side file I/O (no RLM, no
sandbox, no rlm-harness tooling involved), so the tests build actual files under `tmp_path` and assert on
what is on disk afterwards. The adversarial cases mirror the read side's: a symlink escaping
`memory_dir` (see `test_adapters_claude_code.py`), and a target path that is not in the authoritative
index.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ctx_distillery import apply as apply_module
from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter
from ctx_distillery.apply import (
    ARCHIVE_DIRNAME,
    _archive_destination,
    _skill_extra_target,
    _skill_target,
    apply_plan,
    slugify,
)
from ctx_distillery.schema import AssembledExtraFile
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
    that primary-source research showed to be wrong (the SIXTH gap `apply.py`'s docstring records).

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


@pytest.mark.parametrize("length", [300, 5000])
def test_an_over_long_drafted_skill_name_is_REFUSED_not_silently_truncated(
    memory_dir, tmp_path, length
):
    """An over-long name is refused, and the distinction from the run-id sluggers is the point.

    `frontmatter["name"]` is untrusted MODEL output, so an over-long one is a plan this project must
    handle, not a hand-built curiosity — it reproduces at 300 characters, not only at 5000.

    A first fix CAPPED it, matching what `cli._slug` / `studio.app._slug_id` / `eval.cli._slug` all
    do. That was wrong HERE, for a reason those three do not share: a run id names a trace file
    nobody approved, so shortening it loses nothing a human relied on, while `CLAUDE.md` invariant 9
    says a project skill's DIRECTORY NAME is its real identifier — the slash command comes from it.
    Truncating installs the candidate under an identity the operator never read in the plan, which is
    the same failure `slugify`'s empty-result branch already refuses, one degree less obvious. So
    `slugify` no longer truncates at all and the caller refuses, exactly as it does for an empty slug.

    The refusal is NOT what prevents the `OSError` — `_skill_target` catches path errors independently
    (the test below stubs `slugify` to prove that wall still stands). It is what makes the refusal
    carry a reason a human can act on instead of an errno from three frames down.
    """
    draft = f'---\nname: "{"a" * length}"\ndescription: d\n---\nBody.\n'
    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(draft=draft)),
        [0],
        global_skills_dir=tmp_path / "skills",
    )
    assert outcomes[0].status == "refused"
    assert str(apply_module._SLUG_MAX) in (outcomes[0].reason or "")
    assert not (tmp_path / "skills").exists(), "a refused promotion must write nothing"


@pytest.mark.parametrize("length", [300, 5000])
def test_a_slug_the_filesystem_cannot_stat_is_REFUSED_and_the_rest_of_the_batch_still_applies(
    memory_dir, tmp_path, monkeypatch, length
):
    """`_skill_target`'s "never an exception escaping apply_plan" contract, over the check that broke it.

    The final `is_symlink()`/`exists()`/`is_dir()` used to sit OUTSIDE the try, and `_ignore_error`
    swallows only ENOENT/ENOTDIR/EBADF/ELOOP — so ENAMETOOLONG came out of `apply_plan` as a raw
    `OSError`, leaving a multi-candidate `--approve 0,3` run half-applied under a stack trace.

    The caller's own length refusal is raised OUT OF THE WAY on purpose (`_SLUG_MAX` is patched
    enormous), because that gate would otherwise answer first and this test would silently stop
    exercising the thing it exists for. The two are independent fixes, not one instead of the other:
    the length refusal gives a human an actionable reason for the case that actually reaches a real
    plan, and this wall catches ANY slug that arrives some other way — a hand-built plan, or a future
    caller deriving one differently. `slugify` is stubbed to a pass-through for the same reason.

    Both halves are asserted: the bad candidate is REFUSED, and the good candidate AFTER it in the
    same call still gets applied.
    """
    monkeypatch.setattr(apply_module, "_SLUG_MAX", 10**6)
    monkeypatch.setattr(apply_module, "slugify", lambda name: (name or "").strip().lower())
    # The root EXISTS, so the stat really reaches the over-long component. Against a missing root a
    # 300-character name short-circuits on the parent's own ENOENT (which `Path.exists()` does
    # swallow) and lands on `mkdir`'s already-guarded refusal instead — also fine, but not this check.
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    draft = f'---\nname: "{"a" * length}"\ndescription: d\n---\nBody.\n'
    outcomes = apply_plan(
        memory_dir,
        plan(skill_promotion(draft=draft), promotion("Survivor")),
        [0, 1],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "refused"
    assert "could not stat" in outcomes[0].reason
    assert outcomes[1].status == "applied", "a later candidate must not be lost to an earlier refusal"
    assert (memory_dir / "survivor.md").is_file()


@pytest.mark.parametrize("length", [300, 5000])
def test_the_containment_check_refuses_an_unstattable_slug_rather_than_raising(tmp_path, length):
    """The wall itself, reached directly — `slugify`'s cap is not the only thing holding it up.

    A future caller deriving the slug some other way (or a hand-built plan) must still hit a refusal
    here, which is why the cap and this try/except are BOTH fixes rather than one instead of the other.
    """
    target, reason = _skill_target(tmp_path, "a" * length, overwrite=False)
    assert target is None
    assert "could not stat" in reason


def test_slugify_does_NOT_truncate_because_the_caller_refuses_instead():
    """`slugify` diverges from the three run-id sluggers, and the divergence is the design.

    `cli._slug`, `studio.app._slug_id` and `eval.cli._slug` all CAP at 120: a run id names a trace
    file nobody approved, so shortening it costs a human nothing. This one produces an APPROVED
    ARTIFACT IDENTITY — `CLAUDE.md` invariant 9 makes a project skill's directory name its real
    identifier — so truncating would install the candidate under a name the operator never read in
    the plan. That is the same failure the empty-slug branch refuses, one degree less obvious, so
    this function stays a pure transform and the two promotion call sites refuse an over-long result.
    """
    assert len(slugify("x" * 5000)) == 5000, "no truncation here — the caller refuses instead"
    assert slugify("Deploy Runbook") == "deploy-runbook", "short names are untouched"
    assert slugify("!!!") == "", "a degenerate name still yields the empty slug the caller refuses"


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
    `list_targets()`'s own glob, or by `rlm_harness.skills.discover_skills`'s `*/SKILL.md` walk."""
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


# -- harness mismatch: apply.py only understands writes for SUPPORTED_WRITE_HARNESSES ------------


def test_a_mismatched_harness_refuses_every_write_action(memory_dir):
    mismatched_plan = AssembledPlan(
        candidates=[
            promotion("api-notes"),
            skill_promotion(),
            prune(memory_dir / "user-prefs.md"),
        ],
        harness="codex",
    )
    outcomes = apply_plan(
        memory_dir,
        mismatched_plan,
        [0, 1, 2],
        global_skills_dir=memory_dir / "skills",
    )
    assert [o.status for o in outcomes] == ["refused", "refused", "refused"]
    assert all("codex" in o.reason for o in outcomes)
    assert names_in(memory_dir) == {"project-conventions", "user-preferences", "MEMORY.md"}


def test_a_keep_candidate_is_not_refused_under_a_mismatched_harness(memory_dir):
    mismatched_plan = AssembledPlan(
        candidates=[AssembledCandidate(action="keep", key_fields={"reason": "still true"})],
        harness="codex",
    )
    outcomes = apply_plan(memory_dir, mismatched_plan, [0])
    assert outcomes[0].status == "noop"
    assert "no-op" in outcomes[0].reason


@pytest.mark.parametrize("harness", [None, "claude_code"])
def test_a_none_or_matching_harness_permits_the_write(memory_dir, harness):
    permitted_plan = AssembledPlan(candidates=[promotion("api-notes")], harness=harness)
    outcomes = apply_plan(memory_dir, permitted_plan, [0])
    assert outcomes[0].status == "applied"


def test_a_malformed_non_string_harness_refuses_rather_than_permitting(memory_dir):
    """A non-string `harness` (an int, a list — never produced by a real trace, but not impossible
    from a hand-built/corrupted one) must not be silently coerced to `None` and permitted: it is
    never a member of `SUPPORTED_WRITE_HARNESSES`, so the membership check refuses it naturally."""
    malformed_plan = AssembledPlan(candidates=[promotion("api-notes")], harness=123)
    outcomes = apply_plan(memory_dir, malformed_plan, [0])
    assert outcomes[0].status == "refused"
    assert "123" in outcomes[0].reason


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


# -- _skill_extra_target: containment for a skill's references/scripts files -------------------


@pytest.mark.parametrize(
    "relative_path", ["references/setup.md", "scripts/build.sh", "scripts/lib/util.py"]
)
def test_a_legitimate_extra_path_resolves_inside_the_skill_directory(tmp_path, relative_path):
    skill_dir = tmp_path / "deploy-runbook"
    skill_dir.mkdir()
    target, reason = _skill_extra_target(skill_dir, relative_path)
    assert reason == ""
    assert target == (skill_dir / relative_path).resolve()


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "   ",
        "setup.md",  # no references/ or scripts/ prefix at all
        "assets/setup.md",  # a prefix outside the closed set
        "references/../../../etc/passwd",
        "references/../scripts/x",
        "../references/x.md",
        "..",
        ".",
        "/etc/passwd",  # absolute
        "~/secret",  # home-relative
        "references\\x.md",  # backslash
    ],
)
def test_an_adversarial_extra_path_is_refused_not_written(tmp_path, relative_path):
    skill_dir = tmp_path / "deploy-runbook"
    skill_dir.mkdir()
    target, reason = _skill_extra_target(skill_dir, relative_path)
    assert target is None
    assert reason
    assert list(skill_dir.iterdir()) == []


def test_an_extra_path_with_an_embedded_NUL_is_refused_not_raised(tmp_path):
    """The SAME `(OSError, ValueError)` pair `_skill_target` already catches around `.resolve()` — a
    NUL byte raises `ValueError`, never `OSError`, and this must degrade rather than propagate."""
    skill_dir = tmp_path / "deploy-runbook"
    skill_dir.mkdir()
    target, reason = _skill_extra_target(skill_dir, "references/bad\x00name.md")
    assert target is None
    assert "could not resolve" in reason


@pytest.mark.parametrize("length", [300, 5000])
def test_an_overlong_extra_filename_is_refused_BEFORE_skill_md_is_written(
    memory_dir, tmp_path, length
):
    """An over-long segment must be caught by the validate-before-write pass, like every other
    doomed `relative_path` — not by the write loop's `except OSError`.

    **This test previously asserted the OPPOSITE and pinned a real defect as correct.** It was
    named `..._is_refused_at_the_write_not_the_containment_check`, asserted `"could not write"` in
    the reason — a string only reachable once SKILL.md is already on disk — and justified it with
    "`_skill_extra_target` is pure computation with no stat call, so it cannot catch this itself".
    That reasoning is wrong: a LENGTH check is pure computation and needs no stat call either. The
    behaviour it pinned left a LIVE, discoverable skill (`discover_skills` globs `*/SKILL.md`)
    behind an outcome that told the operator `refused`, which is exactly what the
    validate-before-write pass exists to prevent and what `_extra_path_conflict` already closes for
    the nesting version of the same shape.

    The missing assertion is the last line, and its absence is what made the hole look acceptable:
    `test_a_bad_extra_relative_path_refuses_the_WHOLE_candidate_before_SKILL_md_is_written` has
    made it for the wrong-prefix case all along.
    """
    skills_root = tmp_path / "skills"
    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(
                extra_files={
                    f"references/{'a' * length}.md": AssembledExtraFile(
                        relative_path=f"references/{'a' * length}.md", draft="x", draft_ok=True
                    ),
                }
            )
        ),
        [0],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "refused"
    assert "over the 255-byte limit" in outcomes[0].reason
    assert not skills_root.exists(), (
        "SKILL.md must not exist when an extra file's path is doomed — an over-long segment is no "
        "less doomed than a wrong prefix, and leaving the skill live while reporting `refused` is "
        "the defect this test used to pin as correct"
    )


def test_the_extra_segment_cap_counts_BYTES_not_characters(memory_dir, tmp_path):
    """A 100-character CJK segment is 300 bytes, and the filesystem counts bytes. Counting
    characters would let it through to the same ENAMETOOLONG this cap exists to pre-empt."""
    name = "references/" + "中" * 100 + ".md"
    skills_root = tmp_path / "skills"
    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(
                extra_files={name: AssembledExtraFile(relative_path=name, draft="x", draft_ok=True)}
            )
        ),
        [0],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "refused"
    assert "over the 255-byte limit" in outcomes[0].reason
    assert not skills_root.exists()


def test_a_long_but_writable_extra_filename_still_applies(memory_dir, tmp_path):
    """The cap must not refuse a segment the filesystem would have accepted — 200 ASCII bytes is
    under it, so this is the boundary's other side."""
    name = f"references/{'a' * 200}.md"
    skills_root = tmp_path / "skills"
    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(
                extra_files={name: AssembledExtraFile(relative_path=name, draft="x", draft_ok=True)}
            )
        ),
        [0],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "applied", outcomes[0].reason
    assert (skills_root / "deploy-runbook" / name).is_file()


def test_a_symlinked_references_directory_cannot_redirect_the_write_outside_the_skill(tmp_path):
    skill_dir = tmp_path / "deploy-runbook"
    skill_dir.mkdir()
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    os.symlink(outside, skill_dir / "references")
    target, reason = _skill_extra_target(skill_dir, "references/setup.md")
    assert target is None
    assert "refusing to write outside" in reason


# -- _promote_skill: writing the supplementary files -----------------------------------------


def test_a_skill_promotion_writes_SKILL_md_and_its_supplementary_files(memory_dir, tmp_path):
    skills_root = tmp_path / "skills"
    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(
                extra_files={
                    "references/one.md": AssembledExtraFile(
                        relative_path="references/one.md", draft="reference body", draft_ok=True
                    ),
                    "scripts/setup.sh": AssembledExtraFile(
                        relative_path="scripts/setup.sh", draft="#!/bin/sh\necho hi\n", draft_ok=True
                    ),
                }
            )
        ),
        [0],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "applied", outcomes[0].reason
    skill_dir = skills_root / "deploy-runbook"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == SKILL_DRAFT
    assert (skill_dir / "references" / "one.md").read_text(encoding="utf-8") == "reference body"
    assert (skill_dir / "scripts" / "setup.sh").read_text(encoding="utf-8") == "#!/bin/sh\necho hi\n"
    assert "2 supplementary file" in outcomes[0].reason


def test_a_bad_extra_relative_path_refuses_the_WHOLE_candidate_before_SKILL_md_is_written(
    memory_dir, tmp_path
):
    """Validate-before-write: a candidate doomed by a bad `relative_path` must never leave a
    half-written, DISCOVERABLE skill behind (`rlm_harness.skills.discover_skills` globs `*/SKILL.md`)."""
    skills_root = tmp_path / "skills"
    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(
                extra_files={
                    "assets/escape.md": AssembledExtraFile(
                        relative_path="assets/escape.md", draft="x", draft_ok=True
                    ),
                }
            )
        ),
        [0],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "refused"
    assert "assets/escape.md" in outcomes[0].reason
    assert not skills_root.exists(), "SKILL.md must not exist when an extra file's path is doomed"


def test_two_extras_where_one_would_have_to_be_both_a_file_and_a_directory_are_refused_up_front(
    memory_dir, tmp_path
):
    """`relative_path="scripts"` (a file, no filename under it — nothing at draft time refuses
    THIS specific string via `apply_plan`'s own input contract, which never trusts a hand-built
    candidate; a real `draft_skill_extra_file` call's OWN validator does refuse it) and
    `relative_path="scripts/build.sh"` are each individually valid containment-wise, but cannot
    BOTH exist. Found by adversarial review, reproduced end to end: `_extra_path_conflict` catches
    this BEFORE anything is written, so `SKILL.md` is never left on disk under a `refused` outcome —
    the guarantee `_promote_skill`'s own validate-before-write pass exists to give."""
    skills_root = tmp_path / "skills"
    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(
                extra_files={
                    "scripts": AssembledExtraFile(relative_path="scripts", draft="oops", draft_ok=True),
                    "scripts/build.sh": AssembledExtraFile(
                        relative_path="scripts/build.sh", draft="echo hi", draft_ok=True
                    ),
                }
            )
        ),
        [0],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "refused"
    assert "conflict" in outcomes[0].reason
    assert not skills_root.exists(), "a conflicting pair must refuse BEFORE SKILL.md is ever written"


@pytest.mark.parametrize(
    "paths",
    [
        ("scripts/utils", "scripts/utils/helper.py"),  # the exact shape found by adversarial review
        ("references", "references/notes.md"),
        ("scripts/lib", "scripts/lib/deep/nested.py"),
    ],
)
def test_extra_path_conflicts_are_caught_regardless_of_which_entry_is_the_ancestor(
    memory_dir, tmp_path, paths
):
    skills_root = tmp_path / "skills"
    shallow, deep = paths
    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(
                extra_files={
                    shallow: AssembledExtraFile(relative_path=shallow, draft="x", draft_ok=True),
                    deep: AssembledExtraFile(relative_path=deep, draft="y", draft_ok=True),
                }
            )
        ),
        [0],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "refused"
    assert "conflict" in outcomes[0].reason
    assert not skills_root.exists()


def test_a_file_already_on_disk_from_an_EARLIER_apply_still_refuses_via_the_isolated_mkdir(
    memory_dir, tmp_path
):
    """`_extra_path_conflict` only ever compares entries within the SAME call — a file already on
    disk from a PRIOR apply (a different scenario, not a conflict between this call's own entries)
    is what the isolated `mkdir` try/except around each extra's write still exists to catch."""
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "deploy-runbook"
    (skill_dir / "scripts").parent.mkdir(parents=True)
    # A stale FILE (not a directory) sitting where `scripts/build.sh`'s PARENT needs to be — from
    # some earlier, unrelated write this call knows nothing about.
    (skill_dir / "scripts").write_text("stale file from an earlier run\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("stale SKILL.md\n", encoding="utf-8")

    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(
                extra_files={
                    "scripts/build.sh": AssembledExtraFile(
                        relative_path="scripts/build.sh", draft="echo hi", draft_ok=True
                    ),
                }
            )
        ),
        [0],
        overwrite_ids=[0],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "refused"
    assert "supplementary" in outcomes[0].reason


def test_overwrite_does_not_delete_a_stale_extra_file_not_in_the_new_draft(memory_dir, tmp_path):
    """Consistent with "archives, never deletes": overwriting only ever touches files present in
    THIS candidate's `extra_files` — a previously-written extra the new draft omits is left alone."""
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "deploy-runbook"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("stale SKILL.md\n", encoding="utf-8")
    (skill_dir / "references" / "old.md").write_text("stale reference\n", encoding="utf-8")

    outcomes = apply_plan(
        memory_dir,
        plan(
            skill_promotion(
                extra_files={
                    "references/new.md": AssembledExtraFile(
                        relative_path="references/new.md", draft="fresh reference", draft_ok=True
                    ),
                }
            )
        ),
        [0],
        overwrite_ids=[0],
        global_skills_dir=skills_root,
    )
    assert outcomes[0].status == "applied", outcomes[0].reason
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == SKILL_DRAFT
    assert (skill_dir / "references" / "new.md").read_text(encoding="utf-8") == "fresh reference"
    assert (skill_dir / "references" / "old.md").read_text(encoding="utf-8") == "stale reference\n"


def test_apply_plan_never_mutates_the_plan_it_is_given(memory_dir):
    candidate = promotion("api-notes")
    given = plan(candidate)
    apply_plan(memory_dir, given, [0])
    assert given.candidates == [candidate]
    assert candidate.draft == memory_draft("api-notes")
    assert candidate.problems == []
