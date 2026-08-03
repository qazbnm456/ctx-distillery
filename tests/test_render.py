"""`ctx_distillery.render.render_plan` — the ONE plan rendering, now including supplementary files."""

from __future__ import annotations

import dataclasses

from ctx_distillery.render import plan_as_dict, render_plan
from ctx_distillery.schema import AssembledCandidate, AssembledExtraFile, AssembledPlan


def test_render_plan_shows_each_extra_file_with_its_own_ok_state():
    plan = AssembledPlan(
        candidates=[
            AssembledCandidate(
                action="promote_to_skill",
                artifact_id="a1",
                draft="---\nname: x\ndescription: d\n---\nBody.\n",
                draft_ok=True,
                extra_files={
                    "references/one.md": AssembledExtraFile(
                        relative_path="references/one.md", draft="ref body", draft_ok=True
                    ),
                    "scripts/build.sh": AssembledExtraFile(
                        relative_path="scripts/build.sh", draft="", draft_ok=False
                    ),
                },
            )
        ]
    )
    text = render_plan(plan)
    assert "extra file references/one.md (ok=True):\nref body" in text
    assert "extra file scripts/build.sh (ok=False):\n" in text


def test_render_plan_omits_the_extra_file_section_when_there_are_none():
    plan = AssembledPlan(
        candidates=[AssembledCandidate(action="keep", key_fields={"why": "still true"})]
    )
    assert "extra file" not in render_plan(plan)


def test_render_plan_warns_when_the_harness_is_unsupported():
    plan = AssembledPlan(candidates=[], harness="codex")
    text = render_plan(plan)
    assert "harness is 'codex'" in text
    assert "refuse every promotion/prune" in text


def test_render_plan_is_silent_about_harness_when_none():
    plan = AssembledPlan(candidates=[], harness=None)
    assert "harness" not in render_plan(plan)


def test_render_plan_is_silent_about_harness_when_claude_code():
    plan = AssembledPlan(candidates=[], harness="claude_code")
    assert "harness" not in render_plan(plan)


def test_plan_as_dict_picks_up_extra_files_automatically():
    """`plan_as_dict` is `dataclasses.asdict`, so a field added to `AssembledCandidate` shows up here
    without a hand-written mapping to update — this is the property that makes it worth asserting."""
    plan = AssembledPlan(
        candidates=[
            AssembledCandidate(
                action="promote_to_skill",
                artifact_id="a1",
                extra_files={
                    "references/one.md": AssembledExtraFile(
                        relative_path="references/one.md", draft="ref body", draft_ok=True
                    ),
                },
            )
        ]
    )
    as_dict = plan_as_dict(plan)
    assert as_dict == dataclasses.asdict(plan)
    assert as_dict["candidates"][0]["extra_files"]["references/one.md"] == {
        "relative_path": "references/one.md",
        "draft": "ref body",
        "draft_ok": True,
    }
