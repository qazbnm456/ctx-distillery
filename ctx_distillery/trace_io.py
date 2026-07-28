"""Reading a trace safely — the ONE place JSONL bytes become events, and the ONE place a recorded
drafting payload becomes a named CAUSE.

Two jobs, both of them "interpret an untrusted recorded payload once, so every consumer inherits the
answer instead of re-deriving it" (`CLAUDE.md` invariant 11). `dict_events`/`load_trace` are the
shape guard; `draft_cause` is the cause classifier that `schema._not_ok_problem` and
`rl_export.run_metrics` both read — see `draft_cause`'s own docstring for why having had TWO of
those was the bug, not the redundancy.

`rlm_kit.trace.load_events` does NO shape validation: a line that is syntactically valid JSON but
NOT an object (`42`, `null`, `"x"`, `[1, 2, 3]`) parses fine — no `ValueError` — and lands in the
returned list as-is. Every consumer in this workspace calls `.get(...)` on each entry
unconditionally (`rubric.plan_from_events`, `rubric.rubric_from_meta`, `rubric.trace_facts`,
`session.assemble` via `_draft_calls`, `eval`'s `collect_tasks`/`score_run`, `studio`'s
`_load_trace`/`_step_key`/`mapper.to_event`), so such a line reached them and raised a raw
`AttributeError`. That contradicts `session.py`'s stated "none of them raise" philosophy outright,
and in the eval CLI it meant ONE bad line in ONE file of a scoring glob took the WHOLE batch down —
reproduced end to end: a two-file glob (one clean trace, one carrying a single `42` line) scored
ZERO runs, the clean one included, dying in `collect_tasks` before a run was ever reached.

The guard lives HERE, at the boundary where untrusted bytes become events, rather than as five
hand-rolled `isinstance` checks at five call sites — the same "one implementation, never
duplicated" reasoning `CLAUDE.md` invariant 11 gives for `rubric.plan_from_events`. `studio`'s own
`_load_trace` filter (the first, member-local version of this fix) now CALLS this instead of
keeping its own copy; that is a de-duplication, not a removal — the guarantee invariant 10
describes is unchanged, and `studio`'s existing non-dict regression test still pins it end to end.

`load_trace` re-implements the `run_id` filter instead of passing `run_id=` down to `load_events`,
and that is LOAD-BEARING, not a style preference: `load_events`'s own filter is
`event.get("run_id") == run_id`, an unguarded `.get` on exactly the lines this module exists to
drop. Delegating it would put the crash UPSTREAM of our filter and defeat the whole point —
verified: `load_events(path)` on such a file returns fine, while `load_events(path, run_id="r0")`
raises `AttributeError` inside rlm-kit. Hardening `load_events` upstream is a reasonable follow-up
in the rlm-kit repo, but rlm-kit is a commit-pinned dependency of a DIFFERENT project shared with
sibling consumers; this module stays correct either way.
"""

from __future__ import annotations

from collections.abc import Iterable

from rlm_kit.tools import CAUSE_CIRCUIT_BROKEN, CAUSE_ENDPOINT, CAUSE_INVALID, CAUSE_OK
from rlm_kit.trace import load_events

#: rlm-kit's own CLOSED cause vocabulary, re-exported so a reader of a RECORDED payload and a reader
#: of a LIVE `ModelToolResult` name the four outcomes with the same four strings. Imported rather
#: than restated: a parallel vocabulary is exactly the drift this module exists to prevent, and
#: `rlm_kit.tools` is dspy-free (verified), so `schema.py`'s no-dspy property is untouched by it.
DRAFT_CAUSES = (CAUSE_OK, CAUSE_INVALID, CAUSE_ENDPOINT, CAUSE_CIRCUIT_BROKEN)


def draft_cause(payload: dict) -> str:
    """Which of rlm-kit's four outcomes a RECORDED drafting `tool_call` payload describes.

    Returns one of `DRAFT_CAUSES` — `"ok"` / `"invalid"` / `"endpoint"` / `"circuit_broken"` — never
    `None`, so a caller counts by cause rather than by "is it None".

    **Why this is ONE function and not two.** `schema._not_ok_problem` (the human/judge-visible
    problem line) and `rl_export.run_metrics` (the per-cause training counters) each derived the
    cause themselves, from the same two payload fields, in two files. They agreed on every payload
    shape the suite covers — but nothing PINNED that they must, and a sibling consumer reported
    getting the same classification wrong twice independently, in two places, with the second
    "fix" looking complete while still counting an ENDPOINT failure as a gate rejection. A partial
    fix that looks complete is the dangerous state, because nothing prompts a second look. So the
    classification has one implementation and `tests/test_draft_cause.py` pins that the two surfaces
    cannot disagree.

    **Prefer the RECORDED cause; derive only as a fallback.** Since rlm-kit `4fcd50b2`,
    `ModelToolResult` exposes `cause`/`validator_ran` directly and `tools/drafting.py` records both
    onto every drafting `tool_call`, so a fresh trace SAYS what happened instead of leaving it to be
    reconstructed. Every trace recorded before that has no `cause` key at all, and `rl_export` /
    `schema` / `studio` all read historical traces — hence the fallback, which reproduces
    `ModelToolResult.cause`'s own chain exactly (breaker first, then endpoint, then the validator's
    verdict) so an old trace classifies identically to a new one. A recorded value outside the closed
    vocabulary is ignored rather than trusted; deriving is always available.

    **The endpoint string is read under BOTH `endpoint_error` and `error`**, because the consumer
    convention has used each. That half is taken from rlm-kit's own `trace.payload_cause` (added in
    `f217cfad`), which is the read-side mirror of this function; without it a payload that recorded
    the string under `error` classifies as a validator rejection.

    **The other half of `payload_cause` is deliberately NOT taken, and this must not be "collapsed
    into the kit" without fixing that first.** Upstream tests the string for TRUTHINESS
    (`payload.get("endpoint_error") or payload.get("error")`); this function tests `is not None`, and
    the difference is not stylistic. rlm-kit fills the field with `str(exc)`, which is the EMPTY
    STRING for `httpx.ConnectTimeout` / `ReadTimeout` / `ConnectError`, `TimeoutError`, `OSError` and
    `http.client.RemoteDisconnected` — measured, all six. Under truthiness every one of those falls
    through to the validator branch, which is exactly the misclassification `payload_cause`'s own
    docstring says it exists to prevent. Aliasing this to the kit today would REGRESS the common
    transport failures while fixing the rarer key-name one. Take the key set, keep the test.
    Under a truthiness test every one of those fell through to the validator branch — a bare dropped
    connection reported to a human as "failed its format check", and to a trainer as model
    dishonesty, which is the exact harm `CLAUDE.md` invariant 12 exists to prevent.

    The breaker outranks the endpoint because it is the STRONGER claim (the model was never called at
    all). `make_model_tool` never sets both, so the order only matters for a hand-written trace — but
    it is a chain rather than three independent predicates precisely so the four causes PARTITION
    every payload, which is what lets `run_metrics`'s slices sum exactly to its aggregate.
    """
    recorded = payload.get("cause")
    if recorded in DRAFT_CAUSES:
        return recorded
    if payload.get("circuit_broken"):
        return CAUSE_CIRCUIT_BROKEN
    if payload.get("endpoint_error") is not None or payload.get("error") is not None:
        return CAUSE_ENDPOINT
    return CAUSE_OK if payload.get("ok") else CAUSE_INVALID


def dict_events(events: Iterable[object]) -> list[dict]:
    """Normalize to the shape every consumer in this workspace assumes: a dict with a dict `payload`.

    Two normalizations, and the second was added after the first turned out to be only half the job:

    * a non-dict ENTRY (`42`, `null`, `"x"`, `[1,2,3]`) is DROPPED. A malformed line is a
      corrupt-input problem for whichever human reads the trace, never a reason to crash a batch
      scoring run or a studio replay over one bad line in one file.
    * an entry whose `payload` is present but NOT a dict is KEPT with its payload coerced to `{}`.

    **Why the second one is not covered by the first.** Every consumer in the workspace unwraps with
    the idiom `(event.get("payload") or {}).get(...)`, which absorbs `None` and other falsy values
    but NOT a truthy non-dict: `("oops" or {})` is `"oops"`, and `.get` on a `str` raises
    `AttributeError`. So a line that is a perfectly well-formed JSON OBJECT — passing the filter
    above — still crashed `rubric.trace_facts`, `session.assemble` and the studio's
    `/v1/runs/{id}/iterations` with a 500, which is exactly the guarantee `CLAUDE.md` invariant 10
    says this module exists to provide. Found by an adversarial review of the drawer pass.

    Coerced rather than dropped, deliberately: the entry's `type`/`step_id`/`ts`/`run_id` envelope is
    still true and still orders the trace, so discarding the whole event would lose more than the
    corruption did. A consumer sees an event with no payload fields, which every one of them already
    handles — that is the shape a legitimately payload-less event has.

    Fixing it HERE rather than at the ~12 unwrap sites is the same reasoning invariant 11 gives for
    `plan_from_events`: one implementation, so a future consumer inherits the guarantee instead of
    having to remember it.
    """
    out: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if "payload" in event and not isinstance(event["payload"], dict):
            event = {**event, "payload": {}}
        out.append(event)
    return out


def load_trace(path: str, run_id: str | None = None) -> list[dict]:
    """Read a trace JSONL file into dict-shaped events only, optionally filtered to one `run_id`.

    A drop-in replacement for `rlm_kit.trace.load_events(path, run_id=...)` everywhere in this
    workspace. Only the SHAPE of a successfully-parsed line is normalized here: an unreadable file
    or a genuinely torn, non-JSON line still propagates `OSError` / `ValueError` unchanged, because
    those are external failures a caller decides how to surface (the studio turns them into a 502,
    never a 500). Dropping a bad SHAPE and swallowing a bad FILE are different things.
    """
    events = dict_events(load_events(path))
    if run_id is None:
        return events
    return [event for event in events if event.get("run_id") == run_id]
