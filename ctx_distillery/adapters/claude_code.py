"""The Claude Code harness adapter — the ONE concrete adapter `docs/DESIGN.md` scopes in.

Claude Code is the only harness whose real persistence format has been directly verified (a
per-project `memory/` directory of `*.md` files with `name` / `description` / nested
`metadata.type` frontmatter, plus a `MEMORY.md` index). Codex / Hermes / OpenClaw / OpenCode stay
deliberately undesigned — see `CLAUDE.md`, "Harness scope".

Read-only, per `CLAUDE.md` invariant (4): this module opens files for reading only and has no
write/emit path of any kind.

Two honest simplifications in this pass, stated rather than hidden:

* This adapter does NOT locate Claude Code's transcript storage. The caller supplies already-loaded
  RAW transcript text; finding the real on-disk transcript location is future work. (Raw is correct
  here — `session.run_distillation` redacts immediately after `ingest()`, before any LM exposure.)
* `list_targets()` never returns `kind="skill"` entries yet: this deployment's Claude Code skill
  storage location hasn't been inspected, and faking one would be the "speculation dressed as
  design" the design doc rejects. `schema_for("skill")` still documents the SHAPE (which comes from
  `rlm_kit.skills`'s Agent-Skills convention), so a drafted skill is still validated — only the
  collision check against EXISTING skills is correspondingly weaker until enumeration lands.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .. import frontmatter
from .base import ArtifactKind, ArtifactRef, HarnessAdapter, RawSession

#: The four values Claude Code's memory-file frontmatter allows for `metadata.type`.
MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")

#: The harness's memory index file — enumerated as `kind="index"`, not `kind="memory"`.
INDEX_FILENAME = "MEMORY.md"


class ClaudeCodeAdapter(HarnessAdapter):
    """Read one Claude Code project's `memory/` directory + caller-supplied transcripts."""

    def __init__(self, memory_dir: str | Path, transcripts: Iterable[str] = ()) -> None:
        # `.resolve()` once, here: every path this adapter hands out is absolute. Combined with
        # `list_targets`'s containment check below, this is what lets `read_memory_file` do an EXACT
        # path match against the snapshot instead of a prefix/substring comparison a `..` segment in
        # a REQUESTED path could slip past. Resolving alone does NOT stop a symlink that already
        # lives inside `memory_dir` at enumeration time — that is a separate, second check (an
        # adversarial review found the first draft only guarded requests, not enumeration, and let a
        # pre-existing symlink's resolved target join the snapshot as if it were a real memory file).
        self.memory_dir = Path(memory_dir).resolve()
        self._transcripts: list[str] = [str(t) for t in transcripts]

    # -- HarnessAdapter -------------------------------------------------------------------

    def ingest(self) -> RawSession:
        """Snapshot this harness's transcripts + memory index into one `RawSession`.

        Called EXACTLY ONCE per run by `session.run_distillation`; the resulting `memory_index` list
        is the immutable snapshot every tool closes over from then on (no tool ever holds a live
        adapter reference, so the allowlist cannot shift mid-run).
        """
        return RawSession(transcripts=list(self._transcripts), memory_index=self.list_targets())

    def list_targets(self) -> list[ArtifactRef]:
        """Enumerate `memory_dir`'s `*.md` files, plus `MEMORY.md` itself as `kind="index"`.

        Returns an empty list when `memory_dir` doesn't exist — a project with no memory store yet
        is a normal input (everything is a promotion candidate), not an error.

        CONTAINMENT CHECK (fixed after an adversarial review reproduced a real escape): a symlink
        living inside `memory_dir` can resolve to a path OUTSIDE it. `glob` follows symlinks, so a
        naive enumeration would happily add that outside target to the snapshot — and everything
        downstream (`read_memory_file`'s allowlist) trusts the snapshot completely, by design. So a
        resolved path is only enumerated when its PARENT is still exactly `memory_dir` itself; a
        symlink pointing elsewhere is silently skipped rather than joining the trusted snapshot.
        """
        if not self.memory_dir.is_dir():
            return []
        refs: list[ArtifactRef] = []
        index_path = (self.memory_dir / INDEX_FILENAME).resolve()
        for path in sorted(self.memory_dir.glob("*.md")):
            resolved = path.resolve()
            if resolved.parent != self.memory_dir:
                continue  # a symlink resolving outside memory_dir — never trust it into the snapshot
            if resolved == index_path:
                continue  # handled below, as kind="index"
            meta, _body = frontmatter.parse(_read_text(resolved))
            refs.append(
                ArtifactRef(
                    name=str(meta.get("name") or resolved.stem),
                    description=str(meta.get("description") or ""),
                    kind="memory",
                    path=str(resolved),
                )
            )
        if index_path.is_file() and index_path.parent == self.memory_dir:
            # Same containment check as above — `MEMORY.md` itself could theoretically be a
            # symlink escaping the directory too. It has no frontmatter of its own — its
            # name/description are fixed, and its VALUE to the planner is the index lines in its
            # body, read via `read_memory_file`.
            refs.append(
                ArtifactRef(
                    name=INDEX_FILENAME,
                    description="The memory index this harness maintains over the memory files.",
                    kind="index",
                    path=str(index_path),
                )
            )
        return refs

    def schema_for(self, kind: ArtifactKind) -> dict[str, Any]:
        """The structural schema a valid `kind` artifact must satisfy in Claude Code.

        Shape only — it says what a well-formed file looks like, never whether the content is a
        GOOD memory/skill (that judgement stays with the human reviewing the plan).
        """
        if kind in ("memory", "index"):
            # An index ENTRY describes a memory file, so it is governed by the same shape;
            # `MEMORY.md`'s own body has no frontmatter of its own.
            return {
                "type": "object",
                "required": ["name", "description", "metadata"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "metadata": {
                        "type": "object",
                        "required": ["type"],
                        "properties": {"type": {"type": "string", "enum": list(MEMORY_TYPES)}},
                    },
                },
            }
        if kind == "skill":
            # rlm_kit.skills's Agent-Skills convention: frontmatter `name` + `description` only.
            return {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
            }
        raise ValueError(f"unknown artifact kind {kind!r}")


def _read_text(path: Path) -> str:
    """Read one file's text, degrading to "" on any read error (a permission-denied or
    concurrently-removed memory file must not sink enumeration of the rest)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
