"""Harness-adapter seam — the abstract interface, not an implementation.

ctx-distillery is designed to be agent-harness-agnostic: the RLM planning core (the
`DistillSession` task, its tools, the judgement-only SUBMIT + assemble-on-read convention)
never talks to a harness's on-disk format directly. Instead each harness (Claude Code today;
Codex / Hermes / OpenClaw / OpenCode as *named future targets*, not yet designed — see
docs/DESIGN.md, "Multi-harness seam") gets a thin adapter that implements this interface.

This module defines ONLY the seam. There is no concrete adapter here yet — that is
deliberate. See docs/DESIGN.md for why: a Claude Code adapter is buildable now because its
real persistence format has been directly inspected; the others have not, so pre-guessing
their schemas would be speculation dressed as design.

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
# memory file. It exists because docs/DESIGN.md's success criterion (b) ("flags candidate MEMORY.md
# index lines") requires the planner to be able to READ the index; a kind excluded from
# `list_targets` is unreachable through `read_memory_file`'s snapshot allowlist, so the criterion
# would be unmeetable. Adding a supporting VALUE here does not change the ABC's three abstract
# methods, and the "no write path" constraint in the module docstring is untouched.
ArtifactKind = Literal["memory", "skill", "index"]


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


@dataclass(frozen=True)
class RawSession:
    """The one normalized shape a harness's transcripts + memory store are ingested into.

    `transcripts` holds one entry of raw text per conversation (kept separate, not
    concatenated, so the planner can reason about cross-conversation overlap/conflict
    per docs/DESIGN.md's "Cross-conversation intersection" section). `memory_index` is the
    structured `[{name, description, type, path}, ...]` index described in docs/DESIGN.md's
    "Multi-harness seam" section — i.e. a list of `ArtifactRef`.
    """

    transcripts: list[str] = field(default_factory=list)
    memory_index: list[ArtifactRef] = field(default_factory=list)


class HarnessAdapter(ABC):
    """Read-only bridge from one agent harness's real on-disk format to `RawSession`.

    This is the seam described in docs/DESIGN.md, "Multi-harness seam." A concrete
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
        feeds the deterministic format-validation gate described in docs/DESIGN.md — it
        checks *shape*, never the semantic quality of a proposed artifact.
        """
        raise NotImplementedError

    @abstractmethod
    def list_targets(self) -> list[ArtifactRef]:
        """Enumerate this harness's existing memory/skill files for collision/merge checks.

        Read-only enumeration only — never a write or emit path.
        """
        raise NotImplementedError
