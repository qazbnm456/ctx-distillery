"""ctx-distillery — distill an AI coding agent's transcripts + memory store, as a traced RLM harness.

A downstream *consumer* of rlm-kit (a git-pinned dep; editable overlay for local co-dev): an RLM
planner reads one or more session transcripts plus a persistent memory/skill index through five
READ-ONLY tools, computes over them as code in a `pyodide` sandbox, and emits a judgement-only plan
(keep / prune / promote_to_memory / promote_to_skill) whose drafted bytes are re-sourced from the
trace on read. It PROPOSES; a human applies.

Public surface::

    from ctx_distillery import DistillConfig, setup, make_chat_fn, main   # drive a run
    from ctx_distillery import DistillSession, run_distillation           # the task + its driver
    from ctx_distillery import run_distillation_artifacts, DistillArtifacts  # ... + what it drew from
    from ctx_distillery import DistillPlan, DistillCandidate, DistillAction     # the SUBMIT shape
    from ctx_distillery import AssembledPlan, AssembledCandidate, assemble      # the read side
    from ctx_distillery import HarnessAdapter, ArtifactRef, ClaudeCodeAdapter   # the harness seam
    from ctx_distillery import render_plan, plan_as_dict, load_trace, plan_from_events
    from ctx_distillery import load_runs, export_dataset                  # reward-free RL export

**The writer is deliberately NOT here.** `apply_plan`, `ApplyOutcome`, `slugify` and
`ARCHIVE_DIRNAME` live in `ctx_distillery.apply` and are excluded from this module on purpose —
CLAUDE.md invariant 8 makes the human-gated writer's mutation-scan exemption safe by requiring that
NO module on the RLM path (this one included, since its imports run eagerly) can reach it, and
`tests/test_no_write_capability.py::test_apply_is_unreachable_from_the_planner_path` enforces that
against this file too. Reach the writer explicitly instead, which is the point::

    from ctx_distillery.apply import apply_plan   # or the `ctx-distillery-apply` console script

That is a principled divergence from the sibling projects (`cve-reverser`, `diff-sentry`,
`toolscout`), each of which re-exports its writer-adjacent helpers freely. None of them owns an
operation as irreversible as pruning a user's own history.

`schema`, `config`, `render`, `trace_io`, `frontmatter`, `redact`, `rubric`, `rl_export`,
`adapters.*` and `tools.*` import NO dspy at module top (unit-testable in isolation — and the reason `eval/` and
`studio/` can replay a trace without paying for an LM framework). `DistillSession` /
`run_distillation` / `main` pull in dspy lazily (via RLMTask). `frontmatter`'s single helper is
intentionally absent from `__all__`: it is spelled `frontmatter.parse`, and a bare `parse` on a
package surface names nothing.
"""

from __future__ import annotations

from .adapters.base import ARTIFACT_SCOPES, ArtifactRef, ArtifactScope, HarnessAdapter, RawSession
from .adapters.claude_code import (
    ClaudeCodeAdapter,
    global_skills_root,
    memory_dir_for_project,
    project_skills_root,
    project_storage_dir,
    render_transcript_file,
    transcript_files,
)
from .config import PINNED_INTERPRETER, SUBSCRIPTION_PREFIX, DistillConfig, make_chat_fn, setup
from .redact import redact_all, redact_transcript
from .render import plan_as_dict, render_plan
from .rl_export import (
    DRAFTING_TOOLS,
    ORCHESTRATOR_TOOLS,
    export_dataset,
    load_runs,
    rubric_signal,
    run_labels,
    run_metrics,
)
from .rubric import (
    CATEGORY_MEANING,
    CRITERION_CATEGORIES,
    criteria_facts,
    default_rubric,
    plan_from_events,
    rubric_from_meta,
    rubric_to_meta,
    trace_facts,
    validate_rubric,
)
from .schema import (
    PROMOTION_ACTIONS,
    AssembledCandidate,
    AssembledPlan,
    DistillAction,
    DistillCandidate,
    DistillPlan,
    assemble,
)
from .tools import (
    FormatCheck,
    make_draft_memory_file_tool,
    make_draft_skill_file_tool,
    make_list_memory_files_tool,
    make_read_memory_file_tool,
    make_read_transcript_chunk_tool,
)
from .trace_io import dict_events, load_trace

# RUF022 (sort `__all__`) is suppressed HERE and nowhere else in this package. Every other module's
# `__all__` is sorted and must stay that way; this one is grouped by seam, and the grouping is
# load-bearing rather than decorative — the `# dspy-bearing (lazy):` block below is the manifest of
# which names cost a dspy import, and an isort-style sort would scatter `DistillSession` /
# `run_distillation` / `main` into the middle of the eager names and detach every section comment
# from the seam it labels. The three sibling projects all carry the identical grouped form.
__all__ = [  # noqa: RUF022
    # config
    "DistillConfig",
    "PINNED_INTERPRETER",
    "SUBSCRIPTION_PREFIX",
    "setup",
    "make_chat_fn",
    # the SUBMIT shape + the assemble-on-read side
    "DistillAction",
    "DistillCandidate",
    "DistillPlan",
    "AssembledCandidate",
    "AssembledPlan",
    "PROMOTION_ACTIONS",
    "assemble",
    # the harness seam
    "HarnessAdapter",
    "ArtifactRef",
    "ArtifactScope",
    "ARTIFACT_SCOPES",
    "RawSession",
    "ClaudeCodeAdapter",
    "memory_dir_for_project",
    "project_storage_dir",
    "global_skills_root",
    "project_skills_root",
    "transcript_files",
    "render_transcript_file",
    # the READ-ONLY tool set (closed — CLAUDE.md invariant 1)
    "FormatCheck",
    "make_list_memory_files_tool",
    "make_read_memory_file_tool",
    "make_read_transcript_chunk_tool",
    "make_draft_memory_file_tool",
    "make_draft_skill_file_tool",
    # redaction / rendering / trace reading
    "redact_transcript",
    "redact_all",
    "render_plan",
    "plan_as_dict",
    "load_trace",
    "dict_events",
    # rubric
    "CRITERION_CATEGORIES",
    "CATEGORY_MEANING",
    "default_rubric",
    "criteria_facts",
    "trace_facts",
    "plan_from_events",
    "rubric_from_meta",
    "rubric_to_meta",
    "validate_rubric",
    # reward-free dataset export
    "DRAFTING_TOOLS",
    "ORCHESTRATOR_TOOLS",
    "load_runs",
    "export_dataset",
    "run_labels",
    "run_metrics",
    "rubric_signal",
    # dspy-bearing (lazy):
    "DistillSession",
    "DistillArtifacts",
    "run_distillation",
    "run_distillation_artifacts",
    "main",
]

__version__ = "0.1.0"


def __getattr__(name: str):  # PEP 562 — defer the dspy import to first use
    if name == "DistillSession":
        from . import task

        return task.DistillSession
    if name == "run_distillation":
        from . import session

        return session.run_distillation
    if name == "run_distillation_artifacts":
        from . import session

        return session.run_distillation_artifacts
    if name == "DistillArtifacts":
        # A dataclass, not a callable seam — but it is DEFINED in the dspy-bearing `session.py`
        # (its `plan` field is the driver's own return shape), so it belongs in the lazy block
        # rather than the eager one. `tests/test_public_api.py::test_all_names_resolve` getattrs it.
        from . import session

        return session.DistillArtifacts
    if name == "main":
        from . import cli

        return cli.main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
