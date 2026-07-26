"""`list_memory_files` / `read_memory_file` — progressive disclosure over the memory store.

Mirrors `rlm_kit.skills`'s `list_skills` -> `read_skill` shape: the planner first sees a cheap
name+description+kind index, then pulls one file's full body on demand. Both tools are READ-ONLY.

Two design points worth stating outright, because both were decided rather than defaulted:

**Tools take a SNAPSHOT, never a live adapter.** Each factory closes over the `list[ArtifactRef]`
that ONE `adapter.ingest()` call produced (see `session.run_distillation`). `HarnessAdapter` promises
nothing about `list_targets()` being cheap or stable across calls, so a naive adapter re-walking a
directory could shift the allowlist mid-run; and holding a live adapter would create a second copy of
state the driver already owns. The snapshot removes both by construction.

**The read is an ordinary host-side file read.** The ABC has three methods — `ingest`,
`schema_for`, `list_targets` — and no "read one file's body" method. Rather than silently add a
fourth abstract method, this pass reads through `ArtifactRef.path`, which every in-scope harness
(Claude Code: a local filesystem) already carries as a real path. Whether a FUTURE non-filesystem
harness needs a different read seam is deferred to when that harness is actually designed — a stated
simplification (`CLAUDE.md`), not a hidden assumption.

**Allowlist invariant (a project rule, not tool-docstring prose):** `read_memory_file` resolves the
requested path and refuses unless it EXACT-MATCHES one `ArtifactRef.path` in the snapshot. Never a
prefix or substring test — a prefix test on a resolved directory still lets a symlink planted inside
`memory_dir` escape, and a substring test lets `/etc/passwd` through under a crafted name. Only
paths the snapshot already enumerated are readable, full stop.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from rlm_kit.trace import record_tool_call

from ..adapters.base import ArtifactRef

#: Hard cap on how much of one memory file is returned to the planner in a single read. Memory files
#: are small by convention; the cap keeps a pathologically large one from blowing the REPL output cap.
MAX_READ_CHARS = 100_000


def make_list_memory_files_tool(
    memory_index: Sequence[ArtifactRef],
) -> Callable[[], list[dict]]:
    """Build the zero-param `list_memory_files()` tool over an immutable index SNAPSHOT."""
    snapshot = list(memory_index)

    def list_memory_files() -> list[dict]:
        """List the existing memory/skill/index files: name, description, kind, and path.

        Returns a list of ``{"name", "description", "kind", "path"}`` dicts. Read one file's full
        body with ``read_memory_file(path)``, passing a ``path`` exactly as listed here — no other
        path is readable. ``kind`` is ``"memory"`` (a memory file), ``"skill"`` (a skill file), or
        ``"index"`` (the harness's own memory index)."""
        entries = [
            {"name": ref.name, "description": ref.description, "kind": ref.kind, "path": ref.path}
            for ref in snapshot
        ]
        # Record the COUNT, not the full listing — same "record size, not body" convention as
        # rlm-kit's `fetch_url`.
        record_tool_call(
            "list_memory_files",
            args={},
            ok=True,
            count=len(entries),
            kinds=sorted({ref.kind for ref in snapshot}),
        )
        return entries

    return list_memory_files


def make_read_memory_file_tool(
    memory_index: Sequence[ArtifactRef],
) -> Callable[[str], str]:
    """Build the `read_memory_file(path)` tool, allowlisted to the index SNAPSHOT's exact paths."""
    snapshot = list(memory_index)
    # Pre-resolve once: the adapter already stores resolved paths, but resolving here too means a
    # snapshot built by hand (a test, a future adapter) gets the same exact-match guarantee.
    allowed: dict[str, ArtifactRef] = {}
    for ref in snapshot:
        try:
            allowed[str(Path(ref.path).resolve())] = ref
        except OSError:  # pragma: no cover — an unresolvable path is simply not readable
            continue

    def read_memory_file(path: str) -> str:
        """Read the full text of ONE existing memory/skill/index file.

        ``path`` must be a path exactly as returned by ``list_memory_files()``. Any other path is
        refused — this tool cannot read arbitrary files, and it can never write or delete one."""
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            resolved = ""
        ref = allowed.get(resolved)
        if ref is None:
            note = (
                f"refused: {path!r} is not in this run's memory index. Call list_memory_files() "
                f"and pass one of the paths it returns."
            )
            record_tool_call("read_memory_file", args={"path": path}, ok=False, note=note)
            return note
        try:
            text = Path(resolved).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            note = f"error: could not read {ref.name!r}: {exc}"
            record_tool_call("read_memory_file", args={"path": path}, ok=False, note=note)
            return note
        truncated = len(text) > MAX_READ_CHARS
        if truncated:
            text = text[:MAX_READ_CHARS] + "\n[...truncated]"
        # Record the path + size, never the body (the body is already the planner's REPL value).
        record_tool_call(
            "read_memory_file",
            args={"path": path},
            ok=True,
            name=ref.name,
            kind=ref.kind,
            resolved_path=resolved,
            chars=len(text),
            truncated=truncated,
        )
        return text

    return read_memory_file
