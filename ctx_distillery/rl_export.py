"""Export ctx-distillery run traces as REWARD-FREE trajectory datasets.

ctx-distillery is the ROLLOUT source (rollout -> reward -> training), NOT the trainer. This module
emits raw materials only: the trajectory splits (`sft_turns` / `drafting` / `orchestrator_tools` /
`planner`), per-run STRUCTURAL labels, per-run objective metrics, and the ATLAS rubric plus its
deterministic per-criterion facts. No reward scalar is attached (`reward=None` to rlm-kit's
exporters), and rlm-kit's `run_label_bundle` *structurally refuses* a surface literally named
`reward` — so "trajectories, never reward" is enforced at the transport, not merely intended.

**Three deliberate divergences from the sibling exporters, each with a reason.**

1. **There is no `main()`, and no `--out`.** `cve_reverser.rl_export.main` / `diff_sentry` /
   `toolscout` all end in `with open(out, "w") ...`, and `tests/test_no_write_capability.py` scans
   every module under `ctx_distillery/` except the human-gated writer. `CLAUDE.md` says a red
   tripwire IS the finding, not a test to relax — so the exposure is `ctx-distillery export
   <trace-glob>...` printing JSON to STDOUT, redirected with `>`. `ctx-distillery show` already set
   that precedent for exactly the same reason. The CLI uses `print(json.dumps(...))` and NOT
   `json.dump(..., sys.stdout)`: both pass the textual scan, but the latter calls `.write` at
   runtime while only LOOKING clean, which in this repo reads as evading the tripwire rather than
   satisfying it.

2. **`run_labels` is STRUCTURAL, and that is a correction of an earlier reading.** A first pass
   dropped `run_labels` entirely, reasoning that ctx-distillery has no oracle for "was this the
   right thing to prune" and that a labels surface would therefore be fabrication. That argument is
   sound about an ORACLE, and it mis-aimed: it cited `toolscout`'s model-decided `met` booleans,
   which live in `rubric_signal` (the surface kept here), not in `run_labels` at all. The two
   `run_labels` actually shipped by the siblings whose domains have no ground truth are purely
   structural — `diff-sentry`'s `{verdict, signal, indicator_count, max_indicator_severity,
   cited_unknown, inconclusive}` and `toolscout`'s `{finalized, cannot_complete, servers_loaded,
   tools_used, unbacked_servers, unbacked_tools, judge_ran}` — and both map one-for-one onto
   `AssembledCandidate`'s real fields (`unbacked_*` ~ `problems`, `finalized` ~ `plan is not None`).
   Only `cve-reverser`'s `valid`/`complete` is oracle-flavoured, and its domain (does this Nuclei
   template match the patch?) genuinely has ground truth. So `run_labels` below counts what
   `session.assemble` ALREADY established and nothing else. Every field is a count or a boolean a
   second reader could recompute from the same JSONL: zero oracle, zero fabrication. Adding a
   `was_the_right_call`-shaped field later would be the fabrication the original objection feared,
   and it stays out.

3. **Reads through `trace_io.load_trace`, never `rlm_kit.trace.load_events`.** `CLAUDE.md`
   invariant 11 and `trace_io.py`'s own docstring: `load_events` does no shape validation, so a
   JSON-valid non-dict line (`42`, `null`, `[1,2,3]`) would reach the unguarded `e["type"]` /
   `.get(...)` calls below and raise a raw `TypeError`/`AttributeError`. A new reader is a new call
   site for that guard, not an exception to it.

**What the `drafting` split does and does NOT carry.** `cve-reverser` and `diff-sentry` narrow their
generator/classifier split to records whose `outcome.output` is non-empty. That filter is WRONG here
and would silently produce an EMPTY split: `drafting.py` records the authored bytes under a `draft=`
key, and rlm-kit's `_action_record` only reads `raw`/`result`/`results`/`preview`, so
`outcome.output` is `None` for EVERY ctx-distillery tool call. The split is therefore by tool name
alone, and it carries the call's args + `ok`/`errors`, not the drafted text. Re-source the text the
way `CLAUDE.md` invariant 2 requires anyway — from the `tool_call` event keyed by `artifact_id`, via
`schema.assemble` — never from a record that merely claims to describe it.

Usage::

    ctx-distillery export "traces/*.jsonl" > dataset.json
"""

from __future__ import annotations

from rlm_kit.dataset import export_actions, export_sft_turns, run_label_bundle
from rlm_kit.trace import EVENT_MAIN_STEP, EVENT_RUN_START, EVENT_SUB_CALL, EVENT_TOOL_CALL, group_by_run

from .rubric import criteria_facts, default_rubric, plan_from_events, rubric_from_meta
from .schema import PROMOTION_ACTIONS, assemble
from .trace_io import dict_events, load_trace

__all__ = [
    "DRAFTING_TOOLS",
    "ORCHESTRATOR_TOOLS",
    "export_dataset",
    "load_runs",
    "rubric_signal",
    "run_labels",
    "run_metrics",
]

#: The two `make_model_tool`-backed tools — this project's analogue of `cve-reverser`'s `generator`
#: and `diff-sentry`'s `classifier`: a SINGLE-TURN spec -> document -> verdict model, separate from
#: the multi-turn orchestrator policy, and the one thing here a trainer would fine-tune on its own.
DRAFTING_TOOLS = ("draft_memory_file", "draft_skill_file")

#: The three READ-ONLY lookups — the orchestrator's own evidence-gathering ops. Enumerated rather
#: than derived as "everything that is not drafting" (the siblings' complement form) because
#: `CLAUDE.md` invariant 1 makes this tool set CLOSED: naming it is the honest encoding, and an
#: unexpected tool then lands in neither split while still appearing in `actions`, which is the
#: complete stream. A silently-absorbed unknown tool would be the worse failure.
ORCHESTRATOR_TOOLS = ("list_memory_files", "read_memory_file", "read_transcript_chunk")

#: Matches `DistillConfig.max_iterations` / `RLMConfig`'s own default. Only used for a legacy trace
#: whose `run_start` meta recorded no budget; `cli._cmd_distill` stamps the real one on every run.
_DEFAULT_MAX_ITERATIONS = 30


def _meta(events: list[dict]) -> dict:
    for event in events:
        if event.get("type") == EVENT_RUN_START:
            meta = (event.get("payload") or {}).get("meta")
            # A truthy non-dict `meta` slips past `or {}` — see `trace_io.dict_events`.
            return meta if isinstance(meta, dict) else {}
    return {}


def _resolve_max_iterations(events: list[dict]) -> int:
    recorded = _meta(events).get("max_iterations")
    return recorded if isinstance(recorded, int) and recorded > 0 else _DEFAULT_MAX_ITERATIONS


def load_runs(*trace_paths: str) -> dict[str, list[dict]]:
    """Read trace files into `{run_id: [events...]}`.

    Through `trace_io.load_trace`, never `rlm_kit.trace.load_events` — see divergence 3 in the
    module docstring. Unlike the siblings' version this one is part of the PUBLIC surface, because
    without a writing `main()` it is the entry a library caller needs to reach `export_dataset`.
    """
    events: list[dict] = []
    for path in trace_paths:
        events.extend(load_trace(path))
    return group_by_run(events)


def _tool_calls(events: list[dict], tool: str) -> list[dict]:
    """Every `tool_call` payload for `tool`, in trace order."""
    return [
        (event.get("payload") or {})
        for event in events
        if event.get("type") == EVENT_TOOL_CALL and (event.get("payload") or {}).get("tool") == tool
    ]


def run_labels(events: list[dict]) -> dict:
    """STRUCTURAL outcome labels for one run — facts, NOT a reward and NOT an oracle.

    Every field is recomputable from the same JSONL by a second reader, and none of them claims the
    plan was RIGHT (see divergence 2 in the module docstring for why that boundary is the whole
    design of this function). Sourced from `schema.assemble()`'s output rather than re-derived from
    raw events, the same rule `rubric.trace_facts` follows — `assemble` is already this project's
    authority on which candidates are backed, and a second derivation is a second thing to drift.

    * `finalized` — the run reached SUBMIT with an output that VALIDATES as a `DistillPlan`. A trace
      that died mid-trajectory, or whose result payload was the wrong shape, is False. (`plan is not
      None`, `toolscout`'s `finalized` exactly.)
    * `n_candidates` / `n_keep` / `n_prune` / `n_promote_memory` / `n_promote_skill` — the plan's
      own action histogram. `keep` is counted explicitly rather than left as a remainder so an
      action this project might add later cannot silently inflate it.
    * `n_unbacked` — candidates carrying at least one `problem`: a fabricated `artifact_id`, an
      action/tool mismatch, an empty draft, a failed format check. `toolscout`'s `unbacked_*`, and
      exactly the set `apply.apply_plan` refuses.
    * `n_draft_not_ok` — promotion candidates whose drafting call did NOT pass its deterministic
      host-side validator. Overlaps `n_unbacked` on purpose: they answer different questions ("was
      anything wrong with this candidate" vs "did the drafter produce valid bytes"), and collapsing
      them would lose the distinction a trainer would want.
    * `plan_problems` — the RUN-level problem strings verbatim (`assemble`'s own list), not a count:
      the text says WHY, and there is exactly one plan per run so it cannot grow unbounded.
    """
    events = dict_events(events)
    plan = plan_from_events(events)
    assembled = assemble(events, plan)
    actions = [candidate.action for candidate in assembled.candidates]
    return {
        "finalized": plan is not None,
        "n_candidates": len(assembled.candidates),
        "n_keep": actions.count("keep"),
        "n_prune": actions.count("prune"),
        "n_promote_memory": actions.count("promote_to_memory"),
        "n_promote_skill": actions.count("promote_to_skill"),
        "n_unbacked": sum(1 for candidate in assembled.candidates if candidate.problems),
        "n_draft_not_ok": sum(
            1
            for candidate in assembled.candidates
            if candidate.action in PROMOTION_ACTIONS and not candidate.draft_ok
        ),
        "plan_problems": list(assembled.problems),
    }


def run_metrics(events: list[dict]) -> dict:
    """Objective EFFORT metrics — the raw material a trainer shapes into a reward. Facts, never a
    score.

    The per-tool counts are spelled out one seat at a time because the tool set is closed: a reader
    can tell "the planner never opened a transcript" from "the planner read ten chunks and drafted
    nothing", which a single `tool_calls` total hides.
    """
    events = dict_events(events)
    cap = _resolve_max_iterations(events)
    steps = sum(1 for event in events if event.get("type") == EVENT_MAIN_STEP)
    drafts = [payload for tool in DRAFTING_TOOLS for payload in _tool_calls(events, tool)]
    stamps = [event["ts"] for event in events if isinstance(event.get("ts"), (int, float))]
    return {
        "steps": steps,
        "list_memory_files_calls": len(_tool_calls(events, "list_memory_files")),
        "read_memory_file_calls": len(_tool_calls(events, "read_memory_file")),
        "read_transcript_chunk_calls": len(_tool_calls(events, "read_transcript_chunk")),
        "draft_memory_file_calls": len(_tool_calls(events, "draft_memory_file")),
        "draft_skill_file_calls": len(_tool_calls(events, "draft_skill_file")),
        # A drafting call the host-side validator rejected, and one the breaker short-circuited
        # after too many consecutive rejections (`tools/drafting.MAX_CONSECUTIVE_INVALID`) — the
        # second is a subset of the first's *cause*, not of its count, so both are reported.
        "draft_rejects": sum(1 for payload in drafts if not payload.get("ok")),
        "draft_circuit_breaks": sum(1 for payload in drafts if payload.get("circuit_broken")),
        "sub_calls": sum(1 for event in events if event.get("type") == EVENT_SUB_CALL),
        "elapsed_s": round(max(stamps) - min(stamps), 3) if len(stamps) >= 2 else None,
        "hit_iteration_cap": steps >= cap,
    }


def rubric_signal(events: list[dict]) -> dict:
    """The ATLAS rubric surface for one run — the fixed rubric + its deterministic per-criterion
    FACTS. All LABELS: a downstream trainer computes `dᵢ∈[0,1]` and aggregates; this project never
    does, and `rubric.py` never decides met/unmet.

    The reported `rubric` is the EFFECTIVE one — the `run_start`-meta rubric, or the constant
    `default_rubric()` for a legacy trace that carries none — so it always names the SAME criteria
    `criteria_facts` was computed against, with no orphan facts. That backfill follows
    `cve-reverser`/`diff-sentry`, NOT `toolscout`'s bare `rubric_from_meta(events).criteria`, which
    reports an EMPTY rubric beside a full set of facts on any trace recorded before the rubric was.

    There is no judge-observations key here. `toolscout` has one because it wires an opt-in
    `rubric_judge` TOOL whose model decides per-criterion `met` booleans; ctx-distillery's judge
    lives in the separate `eval/` workspace member, never runs inside the trajectory, and writes
    nothing into the trace this module reads.
    """
    rubric = rubric_from_meta(events).criteria or default_rubric().criteria
    return {
        "rubric": [criterion.model_dump() for criterion in rubric],
        "criteria_facts": [fact.model_dump() for fact in criteria_facts(events, rubric)],
    }


def export_dataset(runs: dict[str, list[dict]]) -> dict:
    """Build the REWARD-FREE trajectory bundle for a set of runs.

    Two policies are separable here, the same split every sibling makes. The ORCHESTRATOR is the RLM
    root — ONE multi-turn policy, `sft_turns` for SFT and `planner`/`actions` for RL. The DRAFTER is
    a single-turn document author reached only through the two `make_model_tool` tools. `drafting`
    and `orchestrator_tools` are slices OF `actions`, not separate streams: `actions` stays the
    complete, step-ordered record, so nothing is lost if a future tool matches neither list.

    Every ACTION record carries `reward: None` — an SFT turn is `{run_id, turn, input, output}` and
    has no reward key at all — and the three per-run LABEL surfaces ride via rlm-kit's
    shared `run_label_bundle` — the canonical `{surface: {run_id: fn(events)}}` seam, which refuses
    a surface named `reward` by raising. That makes the reward-free property structural at the
    transport rather than a convention this module could quietly drop.
    """
    actions = export_actions(runs, reward=None)
    tool_actions = [action for action in actions if action["kind"] == "tool"]
    return {
        "actions": actions,
        "drafting": [a for a in tool_actions if a.get("tool") in DRAFTING_TOOLS],
        "orchestrator_tools": [a for a in tool_actions if a.get("tool") in ORCHESTRATOR_TOOLS],
        "planner": [action for action in actions if action["kind"] == "planner"],
        "sft_turns": export_sft_turns(runs),
        **run_label_bundle(runs, labels=run_labels, metrics=run_metrics, rubric_signal=rubric_signal),
    }
