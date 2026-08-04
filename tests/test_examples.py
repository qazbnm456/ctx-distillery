"""Pin that the shipped example run is really SHIPPED — tracked, readable, and a real plan.

This file exists because the failure it guards has now happened twice, the same way both times, and
is invisible without a check. `.gitignore` carries a blanket `*.jsonl` from the sibling template,
where every `.jsonl` genuinely is a run artifact. Here it swallowed the `eval/` demo transcripts
once — a fresh clone had no fixtures, five eval tests failed, and four docs claimed the files were
tracked. It then swallowed `examples/demo-run.jsonl`, which was added, committed, pushed and merged
with only its README going in; `git status` showed nothing missing, because an ignored file is not
"deleted", it is simply never mentioned.

So the check is on the property that actually matters: a reader who clones or downloads this
repository can run `ctx-distillery show examples/demo-run.jsonl` and see a real plan. Every
assertion below would have failed on the merged commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
DEMO = EXAMPLES / "demo-run.jsonl"


def _tracked(path: Path) -> bool:
    """git's own answer, not the filesystem's — the whole failure mode is a file that EXISTS
    locally and is invisible to everyone else."""
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path.relative_to(ROOT))],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def test_the_demo_trace_is_tracked_by_git() -> None:
    assert DEMO.is_file(), f"{DEMO.relative_to(ROOT)} is missing from the working tree"
    assert _tracked(DEMO), (
        f"{DEMO.relative_to(ROOT)} exists locally but is NOT tracked — `.gitignore`'s blanket "
        f"`*.jsonl` swallowed it, so nobody who clones this repository gets it. The negation "
        f"`!examples/*.jsonl` must stay."
    )


def test_the_demo_trace_reads_back_as_a_real_plan() -> None:
    """Offline, through the same path `ctx-distillery show` uses. A tracked but corrupt file would
    satisfy the test above and still leave a reader with nothing."""
    from ctx_distillery.rubric import plan_from_events
    from ctx_distillery.schema import assemble
    from ctx_distillery.trace_io import load_trace

    plan = assemble(load_trace(str(DEMO)), plan_from_events(load_trace(str(DEMO))))
    assert not plan.problems, f"the shipped demo carries run-level problems: {plan.problems}"
    assert plan.candidates, "the shipped demo proposes no candidates — it demonstrates nothing"


def test_the_demo_shows_a_promotion_with_readable_drafted_text() -> None:
    """What the example is FOR. A plan of nothing but `keep` would be tracked, valid, and useless as
    the first thing a new user is pointed at."""
    from ctx_distillery.rubric import plan_from_events
    from ctx_distillery.schema import assemble
    from ctx_distillery.trace_io import load_trace

    events = load_trace(str(DEMO))
    promotions = [
        c for c in assemble(events, plan_from_events(events)).candidates
        if c.action.startswith("promote_") and c.draft_ok and (c.draft or "").strip()
    ]
    assert promotions, "the shipped demo has no promotion carrying usable drafted text"


@pytest.mark.parametrize(
    "secret",
    ["Users/boik", "cycraft", "llm-proxy", "Bearer ", "sk-"],
)
def test_the_demo_carries_nothing_that_should_not_be_published(secret: str) -> None:
    """A trace is a transcript-derived artifact, and this one is distributed. The probes are the
    shapes that actually reached a candidate demo during authoring — a home directory, the endpoint
    that produced it, and credential prefixes — not a general secret scan."""
    assert secret.lower() not in DEMO.read_text(encoding="utf-8").lower(), (
        f"the shipped demo trace contains {secret!r}. Regenerate it against a throwaway root as "
        f"`examples/README.md` describes; never hand-edit a trace to hide something."
    )
