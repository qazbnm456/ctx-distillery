"""`draft_memory_file` / `draft_skill_file` — the only two tools that AUTHOR text, and they
author it into the TRACE, never onto disk.

Base/wrap split, per rlm-kit's convention: the kit owns the generic "call a model, retry transient
endpoint errors, run a deterministic validator, break the circuit on repeated declines" core
(`make_model_tool`); this module supplies the project half — the two tool names, the two validators,
the result wording, and the tracing.

Three project invariants live here:

* **Write boundary.** Both tools return TEXT ONLY. Neither touches the memory directory, a
  `skill_dir`, or any other path — `rlm_kit.skills`'s own discovery being directory-based makes
  "helpfully write it where it belongs" a tempting mistake, so it is called out rather than assumed
  (`CLAUDE.md` invariant 1, and its explicit note that the read-only tool set is CLOSED).
* **`output_model` carries judgement only** (`CLAUDE.md` invariant 2), so the drafted bytes must be
  recoverable from the trace. Each call therefore records the FULL drafted text on its `tool_call`
  event, keyed by `artifact_id` — a deliberate departure from the leaner "record size, not body"
  convention, because `session.assemble` re-sources the verbatim bytes from exactly this event.
* **The validator returns `FormatCheck`, never a bare dict.** `make_model_tool` reads
  `getattr(validated, "ok", False)` / `getattr(validated, "errors", [])`; a dict has neither
  attribute, so a dict-returning validator would report every draft as `ok=False` and trip the
  circuit breaker after three calls, silently. The dataclass makes that impossible.

**The recorded payload names its own CAUSE.** Each `tool_call` carries `cause` / `validator_ran`
straight off the live `ModelToolResult` (rlm-kit `4fcd50b2`), beside the `endpoint_error` /
`circuit_broken` fields it already recorded. This module is the only place with the live object, so
recording what it already knows beats every downstream reader reconstructing it — `CLAUDE.md`
invariant 12. The derivation still exists, in `trace_io.draft_cause`, purely as the fallback for
traces recorded before this key did.

Validation is STRUCTURAL only — is the draft well-formed and non-colliding — never semantic. Whether
a memory is worth keeping is the human reviewer's call.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from rlm_kit.tools import CAUSE_CIRCUIT_BROKEN, CAUSE_ENDPOINT
from rlm_kit.tools.model import ChatFn, make_model_tool
from rlm_kit.trace import record_tool_call

from .. import frontmatter
from ..adapters.base import ARTIFACT_SCOPES, ArtifactRef
from ..adapters.claude_code import MEMORY_TYPES

#: Consecutive invalid drafts before `make_model_tool`'s breaker short-circuits the model. A
#: productive repair loop recovers within a couple of declines (see the kit's own docstring).
MAX_CONSECUTIVE_INVALID = 3


@dataclass
class FormatCheck:
    """A validator verdict in the `.ok` / `.errors` shape `make_model_tool` requires.

    Never return a bare dict from a `validate=` callable: `make_model_tool` reads `.ok`/`.errors` as
    ATTRIBUTES, so a dict silently reads as `ok=False` on every call.
    """

    ok: bool
    errors: list[str] = field(default_factory=list)
    #: The parsed frontmatter, when it parsed at all — handy for a caller that wants the drafted name.
    meta: dict = field(default_factory=dict)


def _existing_names(
    memory_index: Sequence[ArtifactRef], kind: str, scope: str | None = None
) -> set[str]:
    """Names already taken for `kind`, narrowed to one `scope` when given.

    Scope-awareness lives HERE rather than only in the caller (`CLAUDE.md` invariant 7, corrected
    per audit): a skill exists at two independent scopes — user-global `~/.claude/skills/` and
    project-relative `<project>/.claude/skills/` — and the same name in the OTHER scope is not a
    collision at all. `scope=None` keeps the pre-existing behaviour (every scope), which is the
    right conservative superset for a caller that has no scope to check against; a memory/index ref
    is inherently project-scoped, so passing a scope for those changes nothing.
    """
    return {
        ref.name.strip().lower()
        for ref in memory_index
        if ref.kind == kind and (scope is None or ref.scope == scope)
    }


def make_memory_validator(
    memory_index: Sequence[ArtifactRef], memory_type: Callable[[], str | None] | None = None
) -> Callable[[str], FormatCheck]:
    """Build the deterministic memory-file format check over an index SNAPSHOT.

    `memory_type`, when given, returns the `memory_type` the planner passed for the CURRENT call, so
    the validator can cross-check it against the frontmatter the model actually produced — the reason
    `draft_memory_file` takes `memory_type` as an explicit param instead of letting the model bury it
    in free text.
    """
    taken = _existing_names(memory_index, "memory")

    def validate(raw: str) -> FormatCheck:
        errors: list[str] = []
        text = raw or ""
        if not text.strip():
            return FormatCheck(ok=False, errors=["empty draft: the model returned no text"])
        meta, body = frontmatter.parse(text)
        if not meta:
            return FormatCheck(
                ok=False,
                errors=[
                    (
                        "no parseable YAML frontmatter: a memory file must open with a `---` block "
                        "carrying `name`, `description`, and `metadata.type`."
                    )
                ],
            )
        name = meta.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("frontmatter `name` is missing or not a non-empty string")
        elif name.strip().lower() in taken:
            errors.append(
                f"frontmatter `name` {name!r} collides with an existing memory file; pick a "
                f"distinct name, or propose this as an update to that file instead"
            )
        description = meta.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append("frontmatter `description` is missing or not a non-empty string")
        metadata = meta.get("metadata")
        if not isinstance(metadata, dict):
            errors.append("frontmatter `metadata` is missing or not a mapping (needs `type:`)")
        else:
            declared = metadata.get("type")
            if declared not in MEMORY_TYPES:
                errors.append(
                    f"`metadata.type` must be one of {list(MEMORY_TYPES)}, got {declared!r}"
                )
            elif memory_type is not None:
                requested = memory_type()
                if requested and declared != requested:
                    errors.append(
                        f"`metadata.type` {declared!r} does not match the requested memory_type "
                        f"{requested!r}"
                    )
        if not body.strip():
            errors.append("the draft has frontmatter but an empty body")
        return FormatCheck(ok=not errors, errors=errors, meta=meta)

    return validate


def make_skill_validator(
    memory_index: Sequence[ArtifactRef], scope: Callable[[], str | None] | None = None
) -> Callable[[str], FormatCheck]:
    """Build the deterministic skill-file format check (the Agent-Skills shape) over a SNAPSHOT.

    REQUIRED frontmatter is `name` + `description`, and nothing more. `when_to_use` and
    `dispatch_intent` are OPTIONAL: every real installed skill the research inspected carries them,
    but all of those were a single author's one suite, so requiring them would generalize from N=1
    while Anthropic's own documented convention requires neither (`CLAUDE.md` invariant 7, corrected
    per audit). They pass through verbatim when the model supplies them — the drafted bytes are what
    `apply.py` writes — and their absence is never an error. `ClaudeCodeAdapter.schema_for("skill")`
    reports this SAME shape; if one of the two changes, both must.

    `scope`, when given, returns the scope ("global" / "project") the planner declared for the
    CURRENT call, so the collision check runs against the RIGHT namespace: the two skill stores are
    independent, and a global skill's name is not a collision for a project-scoped one. Without it,
    every scope's names are treated as taken — the conservative superset.

    A "project" request ALSO checks the "global" namespace for a same-name skill, but for a
    different reason than collision: empirically-confirmed Claude Code precedence gives a personal
    (global) skill priority over a project one of the same name, so a project skill drafted under a
    name that already exists globally would install cleanly but never actually be reachable — a
    silent no-op, not a file conflict. Flagged here rather than only in `apply.py` because the model
    can still pick a different name before anything is written; a "global" request needs no such
    check, since nothing shadows a global skill.
    """

    def validate(raw: str) -> FormatCheck:
        errors: list[str] = []
        requested = scope() if scope is not None else None
        if requested is not None and requested not in ARTIFACT_SCOPES:
            errors.append(f"scope must be one of {list(ARTIFACT_SCOPES)}, got {requested!r}")
            requested = None  # fall back to the superset rather than checking a namespace that
            #                   does not exist; the bad scope is already reported above.
        taken = _existing_names(memory_index, "skill", requested)
        shadowing = _existing_names(memory_index, "skill", "global") if requested == "project" else set()
        text = raw or ""
        if not text.strip():
            return FormatCheck(ok=False, errors=errors + ["empty draft: the model returned no text"])
        meta, body = frontmatter.parse(text)
        if not meta:
            return FormatCheck(
                ok=False,
                errors=errors
                + [
                    (
                        "no parseable YAML frontmatter: a SKILL.md must open with a `---` block "
                        "carrying `name` and `description` (`when_to_use` / `dispatch_intent` are "
                        "optional extras)."
                    )
                ],
            )
        name = meta.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("frontmatter `name` is missing or not a non-empty string")
        elif name.strip().lower() in taken:
            where = f" in the {requested} scope" if requested else ""
            errors.append(f"frontmatter `name` {name!r} collides with an existing skill{where}")
        elif name.strip().lower() in shadowing:
            errors.append(
                f"frontmatter `name` {name!r} matches an existing GLOBAL skill of the same name — "
                f"Claude Code's personal/global skills take precedence over project ones, so this "
                f"project skill would install but never be reachable (silently shadowed). Pick a "
                f"different name, or draft with scope='global' if the intent is to replace it "
                f"everywhere."
            )
        description = meta.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append("frontmatter `description` is missing or not a non-empty string")
        if not body.strip():
            errors.append("the draft has frontmatter but an empty body")
        return FormatCheck(ok=not errors, errors=errors, meta=meta)

    return validate


#: The closed set of supplementary-file kinds `draft_skill_extra_file` accepts, and the directory
#: prefix each is confined to — `apply._skill_extra_target` re-derives the SAME prefixes from the
#: written `relative_path` alone (it has no `kind` to consult), so this dict is this module's own
#: cross-check, not the write-time containment wall.
SKILL_EXTRA_PREFIXES = {"reference": "references/", "script": "scripts/"}


def make_skill_extra_file_validator(
    kind: Callable[[], str | None], relative_path: Callable[[], str | None]
) -> Callable[[str], FormatCheck]:
    """Build the deterministic format check for ONE supplementary file of an already-drafted skill.

    Purely structural, same as every validator in this module — never whether the CONTENT is a good
    reference doc or a correct script (that is the human reviewer's call, exactly like whether a
    SKILL.md's instructions are good advice already is; `CLAUDE.md` invariant 1 pins "validation is
    structural only" for this whole file).

    `kind`/`relative_path` are read-back closures (the current call's own arguments), the same
    "pending" pattern `make_skill_validator`'s `scope` argument already uses — a cross-check between
    what the planner DECLARED and what it actually asked the model to draft.
    """

    def validate(raw: str) -> FormatCheck:
        errors: list[str] = []
        requested_kind = kind()
        requested_path = relative_path() or ""
        if requested_kind not in SKILL_EXTRA_PREFIXES:
            errors.append(
                f"kind must be one of {list(SKILL_EXTRA_PREFIXES)}, got {requested_kind!r}"
            )
        else:
            prefix = SKILL_EXTRA_PREFIXES[requested_kind]
            if not requested_path.startswith(prefix):
                errors.append(
                    f"relative_path {requested_path!r} must start with {prefix!r} for "
                    f"kind={requested_kind!r}"
                )
            if requested_kind == "reference" and not requested_path.endswith(".md"):
                errors.append(
                    f"a reference file's relative_path must end in '.md', got {requested_path!r}"
                )
        text = raw or ""
        if not text.strip():
            errors.append("empty draft: the model returned no text")
        return FormatCheck(ok=not errors, errors=errors)

    return validate


def _spec_for_skill_extra(relative_path: str, kind: str, evidence: str) -> str:
    """The MODEL-FACING prompt text for ONE supplementary skill file — kept in lockstep with
    `make_skill_extra_file_validator` on purpose, the same reason `_spec_for_skill` gives."""
    noun = "reference document" if kind == "reference" else "script"
    frontmatter_note = (
        "plain markdown, no YAML frontmatter block (frontmatter belongs only in SKILL.md itself)"
        if kind == "reference"
        else "the complete script source, ready to save verbatim at that path"
    )
    return (
        f"Draft ONE supplementary {noun} for an ALREADY-DRAFTED skill.\n"
        f"relative_path (must match exactly): {relative_path}\n"
        f"kind: {kind}\n"
        f"evidence from the session transcript(s):\n{evidence}\n"
        f"Output the complete file content for {relative_path} and nothing else — {frontmatter_note}."
    )


def _spec_for_memory(topic: str, memory_type: str, evidence: str) -> str:
    return (
        f"Draft ONE Claude Code memory file.\n"
        f"topic: {topic}\n"
        f"memory_type (must appear verbatim as metadata.type): {memory_type}\n"
        f"evidence from the session transcript(s):\n{evidence}\n"
        f"Output the complete file: a YAML frontmatter block delimited by `---` lines carrying "
        f"`name`, `description`, and a nested `metadata:` mapping with `type: {memory_type}`, then "
        f"the memory body in markdown. Output nothing else."
    )


def _spec_for_skill(procedure: str, scope: str, evidence: str) -> str:
    """The MODEL-FACING prompt text — kept in lockstep with `make_skill_validator` on purpose.

    `CLAUDE.md` invariant 7 calls out this exact drift risk: a model following prompt text that never
    mentions `when_to_use` / `dispatch_intent` would simply never volunteer them (harmless while
    they are OPTIONAL), but if this text is ever tightened to describe them as required it must match
    what the validator actually enforces. So it names them here as optional extras, which is what the
    validator and `schema_for("skill")` both say.
    """
    return (
        f"Draft ONE SKILL.md documenting a reusable procedure.\n"
        f"procedure: {procedure}\n"
        f"scope (where this skill would be installed): {scope}\n"
        f"evidence from the session transcript(s):\n{evidence}\n"
        f"Output the complete file: a YAML frontmatter block delimited by `---` lines carrying "
        f"`name` and `description` (REQUIRED), optionally also `when_to_use` and/or "
        f"`dispatch_intent` if you have something concrete to say in them, then the procedure body "
        f"in markdown. Output nothing else."
    )


def _errors_with_infra(result) -> list[str]:
    """`errors`, with an INFRASTRUCTURE failure's own message substituted when it recorded none.

    Branches on `result.cause` — rlm-kit's own name for which of the four outcomes this is — rather
    than re-deriving it from `circuit_broken` / `endpoint_error`. That is the kit's stated advice
    ("read one of them before writing any string or label that attributes a failure"), and it means
    the message written here and the `cause` recorded beside it cannot disagree by construction. The
    `is not None`-vs-truthiness care that used to live in this docstring now lives ONCE, in
    `ModelToolResult.cause` upstream and `trace_io.draft_cause` on the read side.

    The `or` fallback still needs its own care: an empty `endpoint_error` names the failure no better
    than nothing does, so an empty string gets a description rather than being echoed as one.
    """
    errors = list(result.errors)
    if result.cause == CAUSE_CIRCUIT_BROKEN:
        return errors or ["circuit breaker: too many consecutive invalid drafts"]
    if result.cause == CAUSE_ENDPOINT:
        return errors or [
            result.endpoint_error or "the drafting model endpoint failed and recorded no message"
        ]
    return errors


def make_draft_memory_file_tool(
    chat_fn: ChatFn, memory_index: Sequence[ArtifactRef]
) -> Callable[[str, str, str], dict]:
    """Wrap an injected `chat_fn` into the sync `draft_memory_file` tool.

    Sync because dspy's interpreter invokes tools with a plain call (no await). Returns a dict so
    dspy JSON-bridges it into a real REPL value the planner can subscript.
    """
    snapshot = list(memory_index)
    # The current call's requested type, read back by the validator to cross-check the frontmatter.
    # Safe as plain closure state: dspy's interpreter invokes a tool with one plain SYNCHRONOUS call
    # (only the sub-LM is ever fanned across threads), so a call's validate() runs before the next
    # call can overwrite this.
    pending: dict[str, str | None] = {"memory_type": None}
    call_model = make_model_tool(
        chat_fn,
        make_memory_validator(snapshot, lambda: pending["memory_type"]),
        max_consecutive_invalid=MAX_CONSECUTIVE_INVALID,
    )

    def draft_memory_file(topic: str, memory_type: str, evidence: str) -> dict:
        """Draft the text of ONE candidate memory file. Writes nothing — text only.

        ``memory_type`` must be one of "user", "feedback", "project", "reference"; it is checked
        against the drafted frontmatter's ``metadata.type``. ``evidence`` should quote or summarise
        the transcript material that justifies the memory. Returns
        ``{"artifact_id", "ok", "errors", "draft"}``; put the ``artifact_id`` on the matching
        ``promote_to_memory`` candidate in your final plan — the drafted text itself is re-sourced
        from this tool call, so do NOT copy it into the plan."""
        artifact_id = uuid.uuid4().hex[:12]
        pending["memory_type"] = memory_type
        result = call_model(_spec_for_memory(topic, memory_type, evidence))
        draft = result.raw or ""
        errors = _errors_with_infra(result)
        # ONE tool_call per call, carrying the FULL drafted text — `session.assemble` re-sources the
        # verbatim bytes from THIS event, keyed by artifact_id (see the module docstring).
        record_tool_call(
            "draft_memory_file",
            args={"topic": topic, "memory_type": memory_type, "evidence": evidence},
            ok=result.ok,
            artifact_id=artifact_id,
            kind="memory",
            draft=draft,
            errors=errors,
            reasoning=result.reasoning,
            endpoint_error=result.endpoint_error,
            circuit_broken=result.circuit_broken,
            cause=result.cause,
            validator_ran=result.validator_ran,
        )
        return {"artifact_id": artifact_id, "ok": result.ok, "errors": errors, "draft": draft}

    return draft_memory_file


def make_draft_skill_file_tool(
    chat_fn: ChatFn, memory_index: Sequence[ArtifactRef]
) -> Callable[[str, str, str], dict]:
    """Wrap an injected `chat_fn` into the sync `draft_skill_file` tool (same shape as above)."""
    snapshot = list(memory_index)
    # The current call's declared scope, read back by the validator so the collision check runs
    # against the right namespace. Same plain-closure reasoning as `draft_memory_file`'s `pending`:
    # dspy's interpreter invokes a tool with ONE plain synchronous call, so this call's validate()
    # runs before the next call can overwrite it.
    pending: dict[str, str | None] = {"scope": None}
    call_model = make_model_tool(
        chat_fn,
        make_skill_validator(snapshot, lambda: pending["scope"]),
        max_consecutive_invalid=MAX_CONSECUTIVE_INVALID,
    )

    def draft_skill_file(procedure: str, scope: str, evidence: str) -> dict:
        """Draft the text of ONE candidate SKILL.md. Writes nothing — text only.

        Use this for a reusable HOW-TO discovered in the session (a workflow, technique, or recipe),
        as opposed to a fact about the user or project — that is ``draft_memory_file``'s job.
        ``scope`` must be "global" (a portable technique, installed for every project) or "project"
        (tied to THIS project's own tooling or conventions); it selects which existing-skill names
        count as a collision, and must match the ``key_fields["scope"]`` you put on the candidate.
        Returns ``{"artifact_id", "ok", "errors", "draft"}``; put the ``artifact_id`` on the matching
        ``promote_to_skill`` candidate in your final plan rather than copying the text."""
        artifact_id = uuid.uuid4().hex[:12]
        pending["scope"] = scope
        result = call_model(_spec_for_skill(procedure, scope, evidence))
        draft = result.raw or ""
        errors = _errors_with_infra(result)
        record_tool_call(
            "draft_skill_file",
            args={"procedure": procedure, "scope": scope, "evidence": evidence},
            ok=result.ok,
            artifact_id=artifact_id,
            kind="skill",
            draft=draft,
            errors=errors,
            reasoning=result.reasoning,
            endpoint_error=result.endpoint_error,
            circuit_broken=result.circuit_broken,
            cause=result.cause,
            validator_ran=result.validator_ran,
        )
        return {"artifact_id": artifact_id, "ok": result.ok, "errors": errors, "draft": draft}

    return draft_skill_file


def make_draft_skill_extra_file_tool(chat_fn: ChatFn) -> Callable[[str, str, str, str], dict]:
    """Wrap an injected `chat_fn` into the sync `draft_skill_extra_file` tool.

    The SIXTH read-only tool (`CLAUDE.md` invariant 1's enumeration widened from five to six) — text
    only, exactly like the other two drafting tools, and needing LESS closure state than they do: a
    supplementary file has no name-collision concept, so unlike `draft_memory_file`/`draft_skill_file`
    this tool takes no `memory_index` snapshot at all.

    Unlike its two siblings, this tool does NOT mint its own `artifact_id` — it takes the
    caller-supplied one from an earlier `draft_skill_file` call, because it is explicitly attaching a
    file to an ALREADY-DRAFTED skill artifact, not authoring a new one. A typo'd/mismatched
    `artifact_id` is not caught here (this closure has no access to the run's own trace as it
    executes) — it becomes a silently orphaned `tool_call` that no `promote_to_skill` candidate ends
    up referencing, never written and never an error. Documented, not solved: see this project's
    design notes for `draft_skill_extra_file` on why a live cross-call registry is a deferred
    follow-up rather than a v1 requirement.
    """
    pending: dict[str, str | None] = {"kind": None, "relative_path": None}
    call_model = make_model_tool(
        chat_fn,
        make_skill_extra_file_validator(lambda: pending["kind"], lambda: pending["relative_path"]),
        max_consecutive_invalid=MAX_CONSECUTIVE_INVALID,
    )

    def draft_skill_extra_file(artifact_id: str, relative_path: str, kind: str, evidence: str) -> dict:
        """Draft ONE supplementary file for an ALREADY-DRAFTED skill. Writes nothing — text only.

        Call this AFTER ``draft_skill_file``, passing the SAME ``artifact_id`` it returned — this
        file is attached to that skill, not a new artifact. ``relative_path`` must be
        ``references/<name>.md`` with ``kind="reference"``, or ``scripts/<name>`` (any extension)
        with ``kind="script"``. Returns ``{"artifact_id", "relative_path", "ok", "errors", "draft"}``.
        Call it as many times as you need, once per supplementary file; a repeat call with the SAME
        ``relative_path`` replaces that one file only — every other file you already drafted for this
        skill is untouched."""
        pending["kind"] = kind
        pending["relative_path"] = relative_path
        result = call_model(_spec_for_skill_extra(relative_path, kind, evidence))
        draft = result.raw or ""
        errors = _errors_with_infra(result)
        record_tool_call(
            "draft_skill_extra_file",
            args={
                "artifact_id": artifact_id,
                "relative_path": relative_path,
                "kind": kind,
                "evidence": evidence,
            },
            ok=result.ok,
            artifact_id=artifact_id,
            relative_path=relative_path,
            kind=kind,
            draft=draft,
            errors=errors,
            reasoning=result.reasoning,
            endpoint_error=result.endpoint_error,
            circuit_broken=result.circuit_broken,
            cause=result.cause,
            validator_ran=result.validator_ran,
        )
        return {
            "artifact_id": artifact_id,
            "relative_path": relative_path,
            "ok": result.ok,
            "errors": errors,
            "draft": draft,
        }

    return draft_skill_extra_file
