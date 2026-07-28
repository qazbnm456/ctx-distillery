"""Reading a trace file safely — the ONE place JSONL bytes become a list of events.

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

from rlm_kit.trace import load_events


def dict_events(events: Iterable[object]) -> list[dict]:
    """Keep only dict-shaped entries — the shape every consumer in this workspace assumes.

    Silently DROPS anything else rather than raising: a malformed line is a corrupt-input problem
    for whichever human reads the trace, never a reason to crash a batch scoring run or a studio
    replay over one bad line in one file.
    """
    return [event for event in events if isinstance(event, dict)]


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
