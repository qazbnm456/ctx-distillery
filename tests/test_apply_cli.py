"""`ctx-distillery-apply` — the writer's own CLI, against a REAL filesystem.

`tests/test_apply.py` covers `apply_plan` itself; this file covers the SHELL-level contract layered
on top of it, which is where the human-approval design actually lives:

* approval is per candidate, by list index, and there is no flag that approves everything;
* the default does nothing (`--confirm` is a second, separate deliberate act);
* a scope whose root the operator did not allow is refused rather than defaulted.

Every case runs against real files under `tmp_path` with a fake `~/.claude` (`--claude-home`), per
`CLAUDE.md` invariant 6 — nothing here may read the developer's actual home. The plan comes from a
genuine `TraceRecorder` trace, because `_cmd_apply` reconstructs it the same way `show` does
(`plan_from_events` -> `assemble`) and a hand-built `AssembledPlan` would skip exactly that path.
"""

from __future__ import annotations

import pytest

from ctx_distillery import apply as apply_mod
from ctx_distillery.adapters.claude_code import memory_dir_for_project
from ctx_distillery.apply import ARCHIVE_DIRNAME
from tests.test_cli import MEMORY_DRAFT, write_trace

SKILL_DRAFT = "---\nname: Deploy Runbook\ndescription: How to deploy.\n---\nSteps.\n"

PROMOTION = {"action": "promote_to_memory", "artifact_id": "artifact-1", "key_fields": {}}
KEEP = {"action": "keep", "key_fields": {"reason": "still relevant"}}


def skill(scope: str) -> dict:
    return {"action": "promote_to_skill", "artifact_id": "artifact-1", "key_fields": {"scope": scope}}


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "proj"
    directory.mkdir()
    return directory


@pytest.fixture
def memory_store(project, claude_home):
    """Where a `promote_to_memory` lands. Deliberately NOT created — `apply_plan` makes it, and a
    project with no memory store yet is a normal input (everything in it is a promotion)."""
    return memory_dir_for_project(project, home=claude_home)


def run(trace, project, claude_home, *extra):
    return apply_mod.main([
        str(trace), "--project", str(project), "--claude-home", str(claude_home), *extra
    ])


# -- the shape of the approval surface ---------------------------------------------------------


def test_approve_is_required(tmp_path, project, claude_home):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION])
    with pytest.raises(SystemExit):
        apply_mod.main([trace, "--project", str(project), "--claude-home", str(claude_home)])


def test_project_is_required(tmp_path):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION])
    with pytest.raises(SystemExit):
        apply_mod.main([trace, "--approve", "0"])


@pytest.mark.parametrize("forbidden", ["--all", "--approve-all", "--yes", "--force", "--everything"])
def test_no_flag_ever_approves_the_whole_plan(forbidden):
    """The CLI-level encoding of `CLAUDE.md` invariant 8's "never apply the whole plan".

    Asserted as a tripwire rather than left as a comment: the API refuses to offer an apply-all
    entry point, and the shell in front of it must not quietly reintroduce one. Checked against the
    parser's real option strings rather than a substring of `--help`, which would have flagged
    `--allow-skill-scope` for merely containing "--all".
    """
    registered = {name for action in apply_mod.build_parser()._actions for name in action.option_strings}
    assert forbidden not in registered


def test_the_help_text_states_that_the_default_writes_nothing():
    help_text = apply_mod.build_parser().format_help()
    assert "--confirm" in help_text and "dry run" in help_text


# -- the dry run (the default) -----------------------------------------------------------------


def test_the_dry_run_is_the_default_and_writes_nothing(tmp_path, project, claude_home, memory_store):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION])
    assert run(trace, project, claude_home, "--approve", "0") == 0
    assert not memory_store.exists(), "a dry run must not even create the memory store"


def test_the_dry_run_shows_the_whole_plan_and_the_roots(tmp_path, project, claude_home, capsys):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION, KEEP])
    run(trace, project, claude_home, "--approve", "0")
    out = capsys.readouterr().out
    # The WHOLE plan, not just the approved slice — a reviewer should see what they are not approving.
    assert "[0] action=promote_to_memory" in out and "[1] action=keep" in out
    assert "Merges into main are frozen" in out
    assert "DRY RUN - nothing has been written." in out
    assert "approved:  [0]" in out
    assert "Re-run the same command with --confirm" in out
    # And which roots it would have used, including the one it is NOT allowed to touch.
    assert "not allowed (pass --allow-skill-scope global)" in out


# -- --confirm: the write ----------------------------------------------------------------------


def test_confirm_writes_only_the_approved_candidate(tmp_path, project, claude_home, memory_store,
                                                    capsys):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION, PROMOTION])
    assert run(trace, project, claude_home, "--approve", "0", "--confirm") == 0
    written = memory_store / "merge-freeze-policy.md"
    assert written.read_text(encoding="utf-8") == MEMORY_DRAFT
    out = capsys.readouterr().out
    assert "1 applied, 0 refused, of 1 approved." in out
    # The unapproved twin is reported, not silently ignored — every candidate gets an outcome.
    assert "[1] promote_to_memory  skipped  not approved by the caller" in out


def test_indices_may_be_comma_separated_or_repeated(tmp_path, project, claude_home, capsys):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[KEEP, KEEP, KEEP])
    assert run(trace, project, claude_home, "--approve", "0,1", "--approve", "2", "--confirm") == 0
    out = capsys.readouterr().out
    assert out.count("noop") == 3, out


def test_a_prune_is_archived_and_reported_as_recoverable(tmp_path, project, claude_home,
                                                         memory_store, capsys):
    memory_store.mkdir(parents=True)
    doomed = memory_store / "stale.md"
    doomed.write_text(
        "---\nname: stale-note\ndescription: Old.\nmetadata:\n  type: project\n---\nOld.\n",
        encoding="utf-8",
    )
    prune = {"action": "prune", "key_fields": {"target_path": str(doomed.resolve())}}
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[prune])

    assert run(trace, project, claude_home, "--approve", "0", "--confirm") == 0

    assert not doomed.exists()
    archive = memory_store.parent / ARCHIVE_DIRNAME
    archived = [p.name for p in archive.iterdir()]
    assert len(archived) == 1 and archived[0].endswith("stale.md")
    assert "still recoverable" in capsys.readouterr().out


# -- refusals that happen BEFORE anything is written ---------------------------------------------


def test_an_out_of_range_index_refuses_before_writing(tmp_path, project, claude_home, memory_store,
                                                      capsys):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION])
    assert run(trace, project, claude_home, "--approve", "0,9", "--confirm") == 2
    assert "--approve" in capsys.readouterr().err
    assert not memory_store.exists(), "a mistyped index must apply nothing at all"


def test_a_non_integer_index_is_refused(tmp_path, project, claude_home):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION])
    with pytest.raises(SystemExit) as excinfo:
        run(trace, project, claude_home, "--approve", "first", "--confirm")
    assert "not a candidate index" in str(excinfo.value)


def test_an_empty_approval_is_refused_rather_than_applying_nothing(tmp_path, project, claude_home,
                                                                   capsys):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION])
    assert run(trace, project, claude_home, "--approve", "  ", "--confirm") == 2
    assert "named no candidates" in capsys.readouterr().err


def test_overwrite_must_also_be_approved(tmp_path, project, claude_home, memory_store, capsys):
    """`apply_plan` raises on this too; the CLI catches it EARLY so a dry run already says so."""
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION, PROMOTION])
    assert run(trace, project, claude_home, "--approve", "0", "--overwrite", "1") == 2
    assert "escape hatch" in capsys.readouterr().err
    assert not memory_store.exists()


def test_a_trace_that_never_finalized_is_refused(tmp_path, project, claude_home, capsys):
    trace = write_trace(tmp_path / "r0.jsonl", with_result=False)
    assert run(trace, project, claude_home, "--approve", "0", "--confirm") == 1
    assert "no usable plan" in capsys.readouterr().err


def test_a_plan_with_no_candidates_is_refused(tmp_path, project, claude_home, capsys):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[])
    assert run(trace, project, claude_home, "--approve", "0", "--confirm") == 1
    assert "nothing to apply" in capsys.readouterr().err


def test_a_missing_project_directory_is_refused(tmp_path, claude_home, capsys):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION])
    assert run(trace, tmp_path / "absent", claude_home, "--approve", "0", "--confirm") == 1
    assert "no such project directory" in capsys.readouterr().err


def test_an_unreadable_trace_is_a_message_not_a_traceback(tmp_path, project, claude_home, capsys):
    assert run(tmp_path / "nope.jsonl", project, claude_home, "--approve", "0") == 1
    assert "cannot read" in capsys.readouterr().err


# -- skill scopes: project by default, global by explicit opt-in --------------------------------


def test_a_project_skill_is_installed_under_the_default_scope(tmp_path, project, claude_home):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[skill("project")], draft=SKILL_DRAFT,
                        tool="draft_skill_file")
    assert run(trace, project, claude_home, "--approve", "0", "--confirm") == 0
    installed = project / ".claude" / "skills" / "deploy-runbook" / "SKILL.md"
    assert installed.read_text(encoding="utf-8") == SKILL_DRAFT


def test_a_global_skill_needs_an_explicit_opt_in(tmp_path, project, claude_home, capsys):
    """`~/.claude/skills` reaches every project the operator will ever open, and a global skill
    SHADOWS a project one of the same name — so it earns its own flag even though `--confirm` is
    already a second deliberate act. `apply_plan` refuses a scope whose root it was not given."""
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[skill("global")], draft=SKILL_DRAFT,
                        tool="draft_skill_file")
    assert run(trace, project, claude_home, "--approve", "0", "--confirm") == 1
    assert not (claude_home / "skills" / "deploy-runbook").exists()
    assert "no global skills root was given" in capsys.readouterr().out


def test_allowing_the_global_scope_installs_it(tmp_path, project, claude_home):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[skill("global")], draft=SKILL_DRAFT,
                        tool="draft_skill_file")
    rc = run(trace, project, claude_home, "--approve", "0", "--allow-skill-scope", "global", "--confirm")
    assert rc == 0
    assert (claude_home / "skills" / "deploy-runbook" / "SKILL.md").read_text(encoding="utf-8") == SKILL_DRAFT


def test_allowing_only_global_withdraws_the_project_default(tmp_path, project, claude_home, capsys):
    """`--allow-skill-scope` REPLACES the default rather than adding to it — naming a scope is an
    explicit statement of where a skill may be installed, so it should mean exactly what it says."""
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[skill("project")], draft=SKILL_DRAFT,
                        tool="draft_skill_file")
    rc = run(trace, project, claude_home, "--approve", "0", "--allow-skill-scope", "global", "--confirm")
    assert rc == 1
    assert not (project / ".claude" / "skills").exists()
    assert "no project skills root was given" in capsys.readouterr().out
