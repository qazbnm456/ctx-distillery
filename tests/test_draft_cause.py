"""`trace_io.draft_cause` — the ONE classifier for a recorded drafting call's outcome.

This file exists because there used to be TWO. `rl_export._draft_cause` (the training counters) and
`schema._not_ok_problem` (the human/judge-visible problem line) each derived "was this a validator
decline, an endpoint failure, or a circuit break" from the same two payload fields, in two modules.
They agreed on every payload the suite covered — but nothing PINNED that they must, which is the
whole failure mode `CLAUDE.md` invariant 11 names, and a sibling consumer of the same kit reported
getting this classification wrong twice INDEPENDENTLY, the second time in a "fix" that looked
complete while still counting an endpoint failure as a gate rejection. A partial fix that looks
complete is the dangerous state, because nothing prompts a second look.

So: one implementation, and the two surfaces are pinned to it here — by IDENTITY (they call the same
function object) and by BEHAVIOUR (over a payload matrix, the counter's bucket and the sentence's
wording name the same cause). Make them disagree and this file goes red.

The vocabulary is rlm-kit's own `CAUSE_*` constants, never a parallel local one — `ModelToolResult`
now exposes `cause`/`validator_ran` directly, `tools/drafting.py` records both, and the derivation
below survives only as the fallback for traces recorded before that key existed.
"""

from __future__ import annotations

import pytest
from rlm_kit.tools import CAUSE_CIRCUIT_BROKEN, CAUSE_ENDPOINT, CAUSE_INVALID, CAUSE_OK
from rlm_kit.trace import EVENT_TOOL_CALL

from ctx_distillery import rl_export, schema, trace_io
from ctx_distillery.trace_io import DRAFT_CAUSES, draft_cause

#: `(id, cause, old-shaped payload)` — the four outcomes as a trace recorded BEFORE `cause` existed
#: wrote them: `ok` plus the two infrastructure fields, and no `cause` key anywhere. The fifth row is
#: the SAME cause as the third by a different route, and it is the sibling's actual second bug: rlm-kit
#: fills `endpoint_error` with `str(exc)`, which is `''` for a whole family of real connection
#: failures, so a truthiness test quietly reclassifies it as a validator decline.
LEGACY_SHAPES = [
    ("ok", CAUSE_OK, {"ok": True, "endpoint_error": None, "circuit_broken": False}),
    ("invalid", CAUSE_INVALID, {"ok": False, "endpoint_error": None, "circuit_broken": False}),
    ("endpoint", CAUSE_ENDPOINT,
     {"ok": False, "endpoint_error": "Connection refused", "circuit_broken": False}),
    ("endpoint-empty-message", CAUSE_ENDPOINT,
     {"ok": False, "endpoint_error": "", "circuit_broken": False}),
    ("circuit-broken", CAUSE_CIRCUIT_BROKEN,
     {"ok": False, "endpoint_error": None, "circuit_broken": True}),
]

#: The same five as a FRESH trace records them — the recorded `cause` present and authoritative.
RECORDED_SHAPES = [
    (f"{name}-recorded", cause,
     {**payload, "cause": cause, "validator_ran": cause in (CAUSE_OK, CAUSE_INVALID)})
    for name, cause, payload in LEGACY_SHAPES
]

ALL_SHAPES = [(f"{name}-legacy", cause, payload) for name, cause, payload in LEGACY_SHAPES]
ALL_SHAPES += RECORDED_SHAPES
NOT_OK_SHAPES = [row for row in ALL_SHAPES if row[1] != CAUSE_OK]


# -- the classifier itself -------------------------------------------------------------------


@pytest.mark.parametrize(("cause", "payload"), [row[1:] for row in LEGACY_SHAPES],
                         ids=[row[0] for row in LEGACY_SHAPES])
def test_an_old_trace_with_no_cause_key_still_classifies_correctly(cause, payload):
    """The fallback is the whole reason the derivation survives: every trace recorded before
    rlm-kit `4fcd50b2` has no `cause` key, and `rl_export` / `schema` / the studio all read
    historical traces."""
    assert "cause" not in payload
    assert draft_cause(payload) == cause


@pytest.mark.parametrize(("cause", "payload"), [row[1:] for row in RECORDED_SHAPES],
                         ids=[row[0] for row in RECORDED_SHAPES])
def test_a_recorded_cause_is_read_not_reconstructed(cause, payload):
    assert draft_cause(payload) == cause


def test_the_recorded_cause_WINS_over_the_derivation():
    """Preference, stated as a behaviour rather than an implementation detail.

    The source (`tools/drafting.py`) holds the live `ModelToolResult` and knows the answer; a reader
    reconstructing it from two fields is strictly worse-informed. If a future rlm-kit adds a fifth
    cause whose flags look like an existing one, the recorded value is the one that stays right.
    """
    payload = {"ok": False, "circuit_broken": True, "cause": CAUSE_ENDPOINT}
    assert draft_cause(payload) == CAUSE_ENDPOINT, "the derivation would have said circuit_broken"


def test_a_recorded_cause_outside_the_closed_vocabulary_is_ignored_not_trusted():
    """Garbage in the key must not become garbage in a training label or a reviewer's sentence.

    `draft_cause` always answers with one of `DRAFT_CAUSES`, so `run_metrics`'s slices keep summing
    to its aggregate no matter what a hand-edited or foreign-producer trace put in the key.
    """
    for junk in ("gate_rejection", "", None, 7, True, ["endpoint"]):
        payload = {"ok": False, "endpoint_error": "502", "circuit_broken": False, "cause": junk}
        assert draft_cause(payload) == CAUSE_ENDPOINT
        assert draft_cause(payload) in DRAFT_CAUSES


def test_an_endpoint_error_that_STRINGIFIED_TO_NOTHING_is_not_a_validator_decline():
    """`endpoint_error` is `Optional[str]` and rlm-kit fills it with `str(exc)` — which is `''` for
    `httpx.ConnectTimeout`/`ReadTimeout`/`ConnectError`, `TimeoutError`, `OSError` and
    `RemoteDisconnected`. A truthiness test sends every one of those down the validator branch."""
    assert draft_cause({"ok": False, "endpoint_error": ""}) == CAUSE_ENDPOINT


def test_the_breaker_outranks_the_endpoint_because_it_is_the_stronger_claim():
    """`make_model_tool` never sets both; the chain makes the four causes partition anyway, which is
    what lets `run_metrics` slice AND total without double-counting."""
    both = {"ok": False, "endpoint_error": "502", "circuit_broken": True}
    assert draft_cause(both) == CAUSE_CIRCUIT_BROKEN


def test_the_vocabulary_is_rlm_kits_own_not_a_parallel_copy():
    """A local `"circuit_break"`/`"validator_reject"` vocabulary is exactly the drift this
    consolidation removes: the kit owns the cause set, and it is CLOSED."""
    assert DRAFT_CAUSES == (CAUSE_OK, CAUSE_INVALID, CAUSE_ENDPOINT, CAUSE_CIRCUIT_BROKEN)


# -- the two surfaces cannot disagree ---------------------------------------------------------


def test_both_surfaces_call_the_SAME_function_object():
    """Identity, not just equal behaviour. Two functions that happen to agree today is the state
    this consolidation ended; a future author who forks one has to delete this line to do it."""
    assert schema.draft_cause is trace_io.draft_cause
    assert rl_export.draft_cause is trace_io.draft_cause
    assert not hasattr(rl_export, "_draft_cause"), "the second derivation must not come back"


#: How `schema._not_ok_problem`'s sentence names each cause — the marker a reviewer actually reads.
_PROBLEM_MARKER = {
    CAUSE_CIRCUIT_BROKEN: "tripped the circuit breaker",
    CAUSE_ENDPOINT: "endpoint failed after its retries",
    CAUSE_INVALID: "failed its format check",
}

#: How `rl_export.run_metrics` counts each cause.
_METRIC_KEY = {
    CAUSE_CIRCUIT_BROKEN: "draft_circuit_breaks",
    CAUSE_ENDPOINT: "draft_endpoint_errors",
    CAUSE_INVALID: "draft_validator_rejects",
}


def _cause_from_problem_line(line: str) -> str:
    """Read the cause BACK out of the human-visible sentence, by its distinguishing marker.

    Deliberately indirect: the point is to compare what a HUMAN would conclude from the problem line
    against what a TRAINER would conclude from the counter, not to compare two calls to the same
    helper (which would pass trivially).
    """
    hits = [cause for cause, marker in _PROBLEM_MARKER.items() if marker in line]
    assert len(hits) == 1, f"the problem line names {len(hits)} causes, not exactly one: {line!r}"
    return hits[0]


@pytest.mark.parametrize(("cause", "payload"), [row[1:] for row in NOT_OK_SHAPES],
                         ids=[row[0] for row in NOT_OK_SHAPES])
def test_the_problem_line_and_the_training_counter_name_the_SAME_cause(cause, payload):
    """The cross-surface pin, over both payload shapes.

    `schema._not_ok_problem` writes the sentence a reviewer reads before approving an apply (and
    that `apply._blocking_problem` echoes into a refusal); `rl_export.run_metrics` writes the counter
    a trainer reads. Naming a 502 a "format check" failure in one and a validator reject in the other
    is the SAME bug wearing two hats — this asserts neither can do it alone.
    """
    line = schema._not_ok_problem("a1", payload)
    events = [{"type": EVENT_TOOL_CALL, "run_id": "r0", "step_id": 1,
               "payload": {"tool": "draft_memory_file", "artifact_id": "a1", **payload}}]
    metrics = rl_export.run_metrics(events)

    assert _cause_from_problem_line(line) == cause
    assert metrics[_METRIC_KEY[cause]] == 1
    assert metrics["draft_not_ok"] == 1
    assert sum(metrics[key] for key in _METRIC_KEY.values()) == metrics["draft_not_ok"]


def test_a_successful_call_is_counted_nowhere_and_produces_no_problem_line():
    """`CAUSE_OK` is a real member of the partition, not a `None` hole — so `draft_not_ok` is the
    complement of the ok count rather than "everything the classifier declined to label"."""
    for _, _, payload in [row for row in ALL_SHAPES if row[1] == CAUSE_OK]:
        events = [{"type": EVENT_TOOL_CALL, "run_id": "r0", "step_id": 1,
                   "payload": {"tool": "draft_memory_file", "artifact_id": "a1", **payload}}]
        metrics = rl_export.run_metrics(events)
        assert metrics["draft_memory_file_calls"] == 1
        assert metrics["draft_not_ok"] == 0
        assert all(metrics[key] == 0 for key in _METRIC_KEY.values())


def test_the_run_level_breaker_fact_is_deliberately_NOT_routed_through_the_classifier():
    """`rubric.trace_facts`'s `any_circuit_broken` asks a DIFFERENT question, and folding it in
    would be the wrong way to "finish the job".

    The classifier answers "which ONE of the four outcomes was this call" — a per-call partition.
    `any_circuit_broken` answers "did the breaker trip anywhere in this run", a run-level existence
    check over one field. A payload that (impossibly) set both flags classifies as `circuit_broken`
    AND has `circuit_broken` set, so the two agree there; the distinction bites the other way —
    the TA fact must stay true to the raw observation rather than inherit a precedence order it has
    no stake in.
    """
    from ctx_distillery.rubric import trace_facts

    events = [{"type": EVENT_TOOL_CALL, "run_id": "r0", "step_id": 1,
               "payload": {"tool": "draft_memory_file", "artifact_id": "a1",
                           "ok": False, "endpoint_error": "502", "circuit_broken": False}}]
    assert draft_cause(events[0]["payload"]) == CAUSE_ENDPOINT
    assert trace_facts(events)["any_circuit_broken"] is False
