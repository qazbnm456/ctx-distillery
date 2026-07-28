"""Two task concepts, deliberately kept side by side — `Task` (from TRACES) and `EvalTask` (a taskset).

`collect_tasks(trace_glob) -> list[Task]` enumerates `{run_id, trace_path}` pairs out of finished
trace files. It is what `score` has always been built on, and it stays: scoring a batch of existing
traces needs no taskset at all, and deleting it to "unify" the two concepts would break the only
subcommand that shipped first. A trace file normally carries exactly one run
(`ctx_distillery.session.run_distillation_artifacts`'s own docstring: "ingest once, redact once, run
once, assemble once"), but a file is read defensively as potentially carrying more than one `run_id`
(e.g. a hand-assembled or concatenated trace file) rather than assuming exactly one.

`EvalTask` / `load_taskset` / `demo_taskset` are the SECOND concept, the siblings' one: a checked-in
list of things to DRIVE, each with a judge-only `reference`. That split (a fuzzy planner-visible
input vs. a concrete judge-only expectation) is ATLAS's design, and every sibling eval member has it.
Runs pair to tasks by the family's `run_id == task.id` convention.

**The one thing no sibling faced: a ctx-distillery task's planner-visible input is a PROJECT
DIRECTORY, and its storage location is machine-dependent.** Claude Code stores a project's
transcripts under `<claude_home>/projects/<sanitize(absolute project path)>/`, where `sanitize`
replaces every `/` with `-` (`ctx_distillery.adapters.claude_code.sanitize_project_dir`, CONFIRMED —
CLAUDE.md invariant 6). So a checked-in taskset cannot name that directory: the name depends on
where the checkout lives. `demo_taskset(root)` therefore MATERIALIZES the layout at call time under
a caller-supplied root, instead of being the static JSON constant all three siblings' demo tasksets
are. What it materializes is layout ONLY — the transcript CONTENT stays checked in, as the two
`demo/*.jsonl` fixtures beside this module, so the demo taskset remains reviewable DATA (the
property a sibling's static JSON has and a generate-it-in-Python version would throw away).

`project.claude_home` being a separate, overridable field is load-bearing rather than tidy:
CLAUDE.md invariant 6 forbids anything here from reading the machine's real `~/.claude` (it is
non-hermetic, and it would pull real user content into a fixture). `ClaudeCodeAdapter.for_project`
already takes `home=`; a taskset must be able to say so.

Deliberately NO `transcripts` field on `EvalTask`, and no separate `memory_dir`. Carrying transcript
text in the taskset would score against a DIFFERENT redaction than the run saw — the exact drift
`ctx_distillery.session.DistillArtifacts` exists to remove — and a hand-written `memory_dir` could
drift from the layout the product actually reads, which `memory_dir_for_project` derives.
"""

from __future__ import annotations

import glob
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from ctx_distillery.adapters.claude_code import (
    MEMORY_DIRNAME,
    PROJECTS_DIRNAME,
    SKILLS_DIRNAME,
    sanitize_project_dir,
)
from ctx_distillery.trace_io import load_trace

#: The checked-in transcript fixtures `demo_taskset` copies into the layout it materializes. Kept as
#: DATA next to this module (reviewable in a diff, editable without touching Python) rather than as
#: string literals — see the module docstring.
DEMO_DIR = Path(__file__).resolve().parent / "demo"

#: `<root>/<the one shared claude home>` — one `~/.claude` stand-in for every demo task, mirroring
#: the real shape (one home, many projects) instead of inventing one home per project.
DEMO_HOME_DIRNAME = "claude-home"


@dataclass(frozen=True)
class Task:
    """One run to score: its id, and the trace file it was recorded into."""

    run_id: str
    trace_path: str


def collect_tasks(trace_glob: str) -> list[Task]:
    """Expand `trace_glob`, and return one `Task` per distinct `run_id` found in each matched file.

    Sorted by path then by run_id, so a batch CLI invocation is deterministic across runs on the
    same filesystem. A matched file with no recorded events (or none carrying a `run_id`) contributes
    no tasks rather than raising — an empty/corrupt trace file degrades the batch, not the whole run.

    FIXED per adversarial review: that last promise did NOT hold for a line that is valid JSON but
    not an object — `e.get("run_id")` below raised a raw `AttributeError` and took the ENTIRE glob
    down before a single run was scored, the clean traces included. Reads through
    `ctx_distillery.trace_io.load_trace` (public, top-level — this package's established one-way
    reader boundary, never an underscore-prefixed helper), which drops those lines at the read.

    The `run_id` it reads is the trace ENVELOPE's, never the filename stem — which is what lets
    `run` give a trace file a unique, timestamped NAME while keeping `run_id == task.id` for the
    pairing `score --taskset` relies on.
    """
    tasks: list[Task] = []
    for path in sorted(glob.glob(trace_glob)):
        events = load_trace(path)
        run_ids = sorted({e.get("run_id") for e in events if e.get("run_id")})
        for run_id in run_ids:
            tasks.append(Task(run_id=run_id, trace_path=path))
    return tasks


class EvalTask(BaseModel):
    """One taskset entry: an id, the project the PLANNER is pointed at, and the judge-only reference.

    * `id` pairs a run to its task by the family's `run_id == task.id` convention.
    * `project` is `{"project_dir": ..., "claude_home": ...}` — the `run` subcommand's input, handed
      to `ClaudeCodeAdapter.for_project(project_dir, home=claude_home)`. It is OPTIONAL here and
      REQUIRED by `run`, which refuses a task without it LOUDLY rather than driving `{}` (cve-reverser's
      stance; diff-sentry silently passes an empty dict to its driver, which is a gap there, not a
      design choice). `score` needs none of it, so a `{id, reference}`-only taskset is a legitimate
      shape — and now a USEFUL one, since `score --taskset` exists.
    * `reference` is judge-only ground truth: the plan a human would expect from this project's
      transcripts. The planner never sees it — it is passed to `judge.build_prompt`'s third slot and
      nowhere else.
    """

    id: str
    project: dict = Field(
        default_factory=dict,
        description="{project_dir, claude_home} — the `run` subcommand's input; `score` ignores it",
    )
    reference: str = Field(
        "", description="judge-only expected plan; the planner never sees this"
    )


def load_taskset(path: str) -> list[EvalTask]:
    """Load a taskset JSON: a list of `{id, project?, reference?}` objects (or `{"tasks": [...]}`).

    Validation is IMPERATIVE here rather than a pydantic validator on `EvalTask`, matching all three
    siblings (none of which has one): the checks that matter are about the FILE — a non-empty `id`,
    a `project` that is an object, and no duplicate ids across the list — and the last of those is
    not expressible on a single model anyway. Every failure names the item's index, because a
    taskset is hand-edited JSON and "item 7" is the only actionable thing to say about it.
    """
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, dict):
        raw = raw.get("tasks")
    if not isinstance(raw, list):
        # TRY004 (prefer TypeError) is refused HERE and at the sibling check below: this is a
        # malformed DATA FILE, hand-edited JSON, not a caller passing the wrong Python type to an
        # API — and `cli._run_command` catches `(OSError, ValueError)` because those are the two
        # ways a file can be unreadable. All three sibling eval members raise `ValueError` too.
        raise ValueError(  # noqa: TRY004
            "taskset JSON must be a list of {id, project?, reference?} objects"
        )
    tasks: list[EvalTask] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError(f"taskset item {i} must be an object with a non-empty 'id' field")
        project = item.get("project", {})
        if not isinstance(project, dict):
            raise ValueError(  # noqa: TRY004 — a malformed data file, not an API type error
                f"taskset item {i}: 'project' must be an object of "
                f"{{project_dir, claude_home}} fields"
            )
        tasks.append(
            EvalTask(id=str(item["id"]), project=project, reference=str(item.get("reference", "")))
        )
    ids = [t.id for t in tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate task ids in taskset: {dupes}")
    return tasks


def demo_taskset(root: str | Path) -> list[EvalTask]:
    """Materialize the built-in two-task demo under `root`, and return it.

    **`root` is the caller's, and the caller owns its lifetime.** That is the whole reason this takes
    an argument at all: a `mkdtemp()` inside would leak a tree per invocation with nobody to delete
    it, and a `TemporaryDirectory()` cleaned at function exit would delete the transcripts before
    `run` ever reads them. So nothing is created outside a directory the caller chose — `run` passes
    its `--out`, a test passes `tmp_path`.

    What lands under `root`::

        <root>/<task id>/                                       the project directory itself
        <root>/claude-home/projects/<sanitized>/<session>.jsonl  its one transcript
        <root>/claude-home/projects/<sanitized>/memory/          empty: nothing promoted yet
        <root>/claude-home/skills/                               empty: no global skills yet

    `<sanitized>` is `sanitize_project_dir(<root>/<task id>)`, computed HERE with the product's own
    function rather than hand-spelled — the reader (`ClaudeCodeAdapter.for_project`) and this writer
    cannot disagree about a location they derive from one implementation. That machine-dependence is
    exactly why this materializes instead of being a static JSON constant like every sibling's.

    Idempotent: re-running over the same `root` overwrites the fixtures and leaves the directories
    alone, so a second `run demo --out <same dir>` is not an error.

    The two tasks cover both poles, as every sibling's `demo_taskset` docstring argues for:
    a session full of durable, still-true project conventions (which SHOULD be promoted), and a
    one-off debugging exchange that resolved itself (which should NOT be — promoting it is the
    over-promotion failure the reference calls out by name).
    """
    root = Path(root).expanduser().resolve()
    home = root / DEMO_HOME_DIRNAME
    (home / SKILLS_DIRNAME).mkdir(parents=True, exist_ok=True)

    tasks: list[EvalTask] = []
    for task_id, fixture, reference in _DEMO_SPECS:
        project_dir = root / task_id
        project_dir.mkdir(parents=True, exist_ok=True)
        storage = home / PROJECTS_DIRNAME / sanitize_project_dir(project_dir)
        (storage / MEMORY_DIRNAME).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEMO_DIR / fixture, storage / f"{task_id}-session.jsonl")
        tasks.append(
            EvalTask(
                id=task_id,
                project={"project_dir": str(project_dir), "claude_home": str(home)},
                reference=reference,
            )
        )
    return tasks


#: `(task id, transcript fixture filename, judge-only reference)`. The references are prose on
#: purpose — they are read by an LLM judge, never matched by a string comparison, exactly as every
#: sibling's are.
_DEMO_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "demo-durable-fact",
        "durable-fact.jsonl",
        (
            "Expected plan (judge-only ground truth, from the transcript's content — NOT any single "
        "run's narrative): this session states THREE standing project conventions that are still "
        "true after it ends — (a) a merge freeze on main from the Friday before a release tag until "
        "the tag is cut, (b) `ruff check .` + `pytest -q` locally before pushing anything touching "
        "ctx_distillery/, and (c) releases cut manually by the maintainer on record, never from CI. "
        "A good plan PROMOTES these (promote_to_memory for the facts; a promote_to_skill for the "
        "pre-push check routine is also defensible), with a drafted body that carries the actual "
        "rule and its rationale rather than restating that a conversation happened. It should NOT "
        "propose pruning them, and it should not treat the tool-use noise as content. Grade TF on "
        "whether all three durable rules survived, TG on whether each candidate's rationale points "
        "at real transcript content, and PA on whether the drafted file is specific enough to act "
            "on without re-reading the transcript."
        ),
    ),
    (
        "demo-one-off-debugging",
        "one-off-debugging.jsonl",
        (
            "Expected plan (judge-only ground truth): this session is a transient debugging exchange "
        "that RESOLVED ITSELF — the import error was the user's own wrong cwd plus a stale shell "
        "hash, and the user says explicitly that it was a false alarm and nothing in the repo "
        "changed. Almost nothing here is durable. A good plan therefore proposes little or nothing: "
        "`keep`/`prune` with a stated reason is the right shape, and the ONE arguably durable "
        "morsel (that the root pyproject's testpaths exclude the eval member, so its suite must be "
        "run from inside eval/) is at most a single small promotion. OVER-PROMOTION is the failure "
            "mode being tested: drafting a memory file about this specific incident, the user's "
            "stale venv, or the exact error string would be wrong. Grade TF on restraint, TA on "
            "whether the run recognised self-resolution rather than pattern-matching "
            "'error => write it down'."
        ),
    ),
)


__all__ = ["DEMO_DIR", "EvalTask", "Task", "collect_tasks", "demo_taskset", "load_taskset"]
