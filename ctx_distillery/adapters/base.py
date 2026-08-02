"""Harness-adapter seam — the abstract interface, not an implementation.

ctx-distillery is designed to be agent-harness-agnostic: the RLM planning core (the
`DistillSession` task, its tools, the judgement-only SUBMIT + assemble-on-read convention)
never talks to a harness's on-disk format directly. Instead each harness (Claude Code and
Codex now; Hermes / OpenClaw as *named future targets*, still not yet designed — see
CLAUDE.md's "Harness scope") gets a thin adapter that implements this interface.

This module defines ONLY the seam — no concrete adapter lives here. `ClaudeCodeAdapter`
(`adapters/claude_code.py`) and `CodexAdapter` (`adapters/codex.py`, READ-ONLY INGESTION
ONLY — see that module's own docstring for why `apply.py` gained no Codex-specific write
path) are both buildable because their real persistence formats have been directly
inspected, at two different evidence tiers (stated explicitly in each module's own
docstring, per CLAUDE.md invariant 6). Hermes / OpenClaw have not been inspected at all, so
pre-guessing their schemas would still be speculation dressed as design.

Hard constraint (mirrors CLAUDE.md's invariants): every method here is READ-ONLY. There is
no write/emit method on this interface, and none may ever be added — the actual "apply a
plan" step is a separate, human-gated action that lives outside the RLM trajectory entirely,
never reachable from an adapter or an RLM tool.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

# "index" is the harness's own memory INDEX file (Claude Code's `MEMORY.md`) — a third kind, not a
# memory file. It exists because the plan must be able to FLAG candidate `MEMORY.md` index lines,
# which requires the planner to be able to READ the index; a kind excluded from `list_targets` is
# unreachable through `read_memory_file`'s snapshot allowlist, so that requirement would be
# unmeetable. Adding a supporting VALUE here does not change the ABC's three abstract
# methods, and the "no write path" constraint in the module docstring is untouched.
ArtifactKind = Literal["memory", "skill", "index"]

# A skill exists at TWO scopes in Claude Code: the user-global store (`~/.claude/skills/`) and a
# project-repo-relative one (`<project>/.claude/skills/`). They are separate namespaces — the same
# skill name may legitimately exist in both — so a collision check has to know WHICH one it is
# checking, and `apply.py` has to know which root to write into. Memory has no global counterpart
# at all, so a memory/index ref is inherently project-scoped (see `ArtifactRef.__post_init__`).
ArtifactScope = Literal["global", "project"]

#: The two scope values, as a runtime tuple (the `Literal` above is types-only).
ARTIFACT_SCOPES: tuple[str, ...] = ("global", "project")


@dataclass(frozen=True)
class ArtifactRef:
    """One existing memory or skill file, as enumerated by `list_targets`.

    A normalized, harness-agnostic pointer — enough for the planner's collision/merge
    checks (does a proposed name already exist? what type is it?) without needing to
    read the harness's raw on-disk layout.
    """

    name: str
    description: str
    kind: ArtifactKind
    path: str
    #: "global" or "project". Leave it None to take the KIND-DERIVED default (below) — never a
    #: blanket one: a memory/index ref defaulting to "global" would be flatly mislabeled.
    scope: ArtifactScope | None = None

    def __post_init__(self) -> None:
        """Resolve `scope=None` to the default this ref's KIND implies, and reject a bogus value.

        The default is deliberately kind-aware rather than one blanket constant
        (`CLAUDE.md` invariant 9, where a skill's write root is chosen BY this field): a SKILL's default is
        `"global"` (the store Claude Code definitely reads), while a memory or index ref is
        inherently `"project"` — this project's memory store has no global counterpart, so there is
        no other honest value for it. An unrecognized scope raises rather than being silently kept:
        `apply.py` ROUTES A WRITE by this field, and a typo'd scope must not resolve to a
        surprising root.
        """
        if self.scope is None:
            object.__setattr__(self, "scope", "global" if self.kind == "skill" else "project")
        elif self.scope not in ARTIFACT_SCOPES:
            raise ValueError(f"scope must be one of {list(ARTIFACT_SCOPES)}, got {self.scope!r}")


@dataclass(frozen=True)
class TranscriptId:
    """WHAT one `RawSession.transcripts` entry IS — identifiers only, never any of its text.

    `transcripts` is positional: everything downstream (`read_transcript_chunk`'s recorded
    `transcript_index`, `studio/`'s trajectory row, `eval/`'s `--- transcript {i} ---` label) keys on
    an integer, and until this shape existed NOTHING anywhere mapped that integer back to a file.
    A reviewer holding a finished trace could not answer "what was transcript 7?" at all.

    **Four `str` fields, every one derived from a filename or a directory name.** No `description`,
    no `agentType`, no body — nothing model-authored, so there is nothing here for redaction to do
    and no field a future edit could widen into text without that being obvious at the type. That is
    what makes this a DIFFERENT thing from the rejected "put the rendered subagent text on
    `RawSession`" shape, which would have carried real transcript bytes down a path
    `session.run_distillation_artifacts`' single `redact()` call does not cover. Widening it (e.g.
    to carry the free-form, operator-authored `agentType`) is a deliberate decision with its own
    argument, never a convenience.

    * `kind`    — `"session"` or `"subagent"`.
    * `id`      — the session id, or the agent id.
    * `session` — the OWNING session's id (equal to `id` for a session entry).
    * `parent`  — `"session:<id>"` / `"agent:<id>"` / `"workflow:<run-id>"`. A session entry is its
      own root, so it reports `"session:<its own id>"`.
    """

    kind: str
    id: str
    session: str
    parent: str


@dataclass(frozen=True)
class RawSession:
    """The one normalized shape a harness's transcripts + memory store are ingested into.

    `transcripts` holds one entry of raw text per conversation (kept separate, not
    concatenated, so the planner can reason about cross-conversation overlap/conflict).
    `memory_index` is the structured `[{name, description, type, path}, ...]` index the
    read-only adapter seam yields (CLAUDE.md invariant 4) — i.e. a list of `ArtifactRef`.

    `transcript_ids` is the ORDERED identity list for `transcripts`, one entry per entry (see
    `TranscriptId`). It defaults to `()` so no existing adapter, caller or test has to change, and
    the rule for an adapter is all-or-nothing: **populate it for every entry you return, or leave it
    empty.** A partial list would renumber nothing while looking authoritative. The driver's stamp
    into `run_meta` is CONDITIONAL on it being non-empty for the same reason — a present-but-empty
    list beside `meta["transcripts"] == 3` would read as "this run had no transcripts" rather than
    "this adapter reported no identities".

    `project_instructions` is a DIFFERENT kind of thing from the two above: not a raw conversation
    and not a structured index, but the harness's own project-level instructions file (Claude
    Code's `CLAUDE.md`) — the project's existing, human-authored context, given to the planner as
    read-only comparison material so it can notice when a session's finding is already documented
    (redundant) or contradicts what is already written down (a conflict worth flagging). Never a
    promotion/prune target: nothing in this project's `apply.py` can edit an existing file in
    place, only ever create a new one.
    """

    transcripts: list[str] = field(default_factory=list)
    memory_index: list[ArtifactRef] = field(default_factory=list)
    transcript_ids: tuple[TranscriptId, ...] = ()
    #: This project's own root `CLAUDE.md` (or `.claude/CLAUDE.md`), read-only planner CONTEXT —
    #: never a promotion/prune target. Defaults to "" (an honest "none found or empty", never a
    #: fabricated positive claim) so every existing adapter/caller/test is unaffected.
    project_instructions: str = ""


class HarnessAdapter(ABC):
    """Read-only bridge from one agent harness's real on-disk format to `RawSession`.

    This is the read-only seam CLAUDE.md invariant 4 pins down. A concrete
    subclass (e.g. a future `ClaudeCodeAdapter`) implements all three methods against
    one harness's actual, verified persistence format. Nothing in this base class may
    ever gain a write or delete path — see the module docstring.
    """

    @abstractmethod
    def ingest(self) -> RawSession:
        """Read this harness's transcripts + memory store into a `RawSession`.

        This is the only adapter method the RLM tools ever call at task-input time. It
        must not mutate anything it reads.
        """
        raise NotImplementedError

    @abstractmethod
    def schema_for(self, kind: ArtifactKind) -> dict[str, Any]:
        """Return the JSON Schema a valid `kind` file must satisfy in this harness.

        Field names for a "memory" vs. "skill" artifact may differ across harnesses; this
        feeds the deterministic format-validation gate — it checks *shape*, never the
        semantic quality of a proposed artifact.
        """
        raise NotImplementedError

    @abstractmethod
    def list_targets(self) -> list[ArtifactRef]:
        """Enumerate this harness's existing memory/skill files for collision/merge checks.

        Read-only enumeration only — never a write or emit path.
        """
        raise NotImplementedError
