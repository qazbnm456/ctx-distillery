"""The Claude Code harness adapter — the ONE concrete adapter this project scopes in.

Claude Code is the only harness whose real persistence format has been directly verified (a
per-project `memory/` directory of `*.md` files with `name` / `description` / nested
`metadata.type` frontmatter, plus a `MEMORY.md` index). Codex / Hermes / OpenClaw / OpenCode stay
deliberately undesigned — see `CLAUDE.md`, "Harness scope".

Read-only, per `CLAUDE.md` invariant (4): this module opens files for reading only and has no
write/emit path of any kind.

**Auto-discovery (`for_project`)** now locates the real storage instead of making the caller
assemble it (`CLAUDE.md` invariant (6), the CONFIRMED-vs-INHERITED split). What each part of that
rests on is stated honestly, because the parts do NOT have equal evidence behind them:

* `sanitize(project_dir)` — every `/` of the project's absolute path replaced by `-`, giving
  `~/.claude/projects/<sanitized>/` — is CONFIRMED against real project directories. No other
  transformation is applied, because none was observed.
* the `memory/` SUB-PATH inside it is this project's PRE-EXISTING assumption, carried forward and
  not independently re-verified on disk (no `memory/` directory existed on the machine the research
  ran on). Auto-discovery only needs the sanitization rule to be right; the sub-path convention is
  inherited, not freshly confirmed.
* TRANSCRIPTS are CONFIRMED: one JSONL file per past conversation, `<session-id>.jsonl`, sibling to
  `memory/`. `render_transcript_events` turns those raw events into the `list[str]` this project
  already expects — a deliberately LOSSY rendering (see its docstring), not a full replay.
* GLOBAL skills are CONFIRMED at `~/.claude/skills/<name>/SKILL.md` (a DIRECTORY per skill, not a
  flat file). PROJECT-scoped skills at `<project_dir>/.claude/skills/<name>/SKILL.md` are CONFIRMED
  TOO, by a dedicated control experiment: a scratch directory seeded with a probe skill was read by
  a genuinely fresh `claude -p` process launched from inside it (the skill was listed AND
  invokable), while a sibling control directory without `.claude/skills/` was not — isolating the
  effect to the project-relative directory rather than a global leak. Two caveats from that
  experiment are load-bearing for the WRITE side: a global skill of the same name SHADOWS a project
  one, and a project's very first skills directory needs a Claude Code restart to be discovered.
  See `CLAUDE.md` invariant (6) and "Known simplifications".

The explicit `ClaudeCodeAdapter(memory_dir, transcripts)` constructor is UNCHANGED and still the
right entry point for a test or an advanced caller. Skill enumeration is OPT-IN on it (pass
`global_skills_dir=` / `project_skills_dir=`), so constructing it bare never silently reaches into
a real `~/.claude` — `for_project` is the path that resolves the real locations.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .. import frontmatter
from .base import ArtifactKind, ArtifactRef, ArtifactScope, HarnessAdapter, RawSession

#: The four values Claude Code's memory-file frontmatter allows for `metadata.type`.
MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")

#: The harness's memory index file — enumerated as `kind="index"`, not `kind="memory"`.
INDEX_FILENAME = "MEMORY.md"

#: `~/.claude` — the user-global Claude Code home, and the parent of both `projects/` and `skills/`.
CLAUDE_DIRNAME = ".claude"
#: `~/.claude/projects/<sanitize(project_dir)>/` — one directory per project.
PROJECTS_DIRNAME = "projects"
#: The per-project memory store, inside that directory (the INHERITED sub-path — see the module docstring).
MEMORY_DIRNAME = "memory"
#: `~/.claude/skills/` (global) and `<project_dir>/.claude/skills/` (project) both use this name.
SKILLS_DIRNAME = "skills"
#: Each skill is a DIRECTORY containing this file (plus optional `references/`, `scripts/`, ...).
SKILL_FILENAME = "SKILL.md"
#: One past conversation per file, `<session-id>.jsonl`, sibling to `memory/`.
TRANSCRIPT_GLOB = "*.jsonl"

#: The ONLY event types that carry `message` / `timestamp` / `isSidechain` / `cwd` at all. Every
#: other type on the real wire (`mode`, `file-history-*`, `ai-title`, `last-prompt`,
#: `queue-operation`, ...) lacks them entirely, so a renderer must filter FIRST, not assume.
RENDERED_EVENT_TYPES: tuple[str, ...] = ("user", "assistant")


def sanitize_project_dir(project_dir: str | Path) -> str:
    """`/Users/me/proj` -> `-Users-me-proj`: the CONFIRMED `~/.claude/projects/` naming rule.

    Every `/` of the absolute path becomes `-`, and nothing else is transformed — that mapping was
    checked against real project directories and found exact. The path is `expanduser()`d and
    `resolve()`d first so one project always maps to one directory name (this project resolves paths
    everywhere else for the same reason).
    """
    return str(Path(project_dir).expanduser().resolve()).replace("/", "-")


def claude_home(home: str | Path | None = None) -> Path:
    """`~/.claude`, or the explicit override a test (or a non-default install) supplies.

    Every derivation below takes the override, so nothing in this module has to read the REAL home
    directory to be tested — a test that reached into an actual `~/.claude` would be non-hermetic
    and could pull real user content into a fixture.
    """
    if home is not None:
        return Path(home).expanduser().resolve()
    return Path.home().resolve() / CLAUDE_DIRNAME


def project_storage_dir(project_dir: str | Path, *, home: str | Path | None = None) -> Path:
    """`<claude_home>/projects/<sanitize(project_dir)>` — the transcripts' + memory's container."""
    return claude_home(home) / PROJECTS_DIRNAME / sanitize_project_dir(project_dir)


def memory_dir_for_project(project_dir: str | Path, *, home: str | Path | None = None) -> Path:
    """`<project storage>/memory` — the INHERITED sub-path, not an independently re-verified one."""
    return project_storage_dir(project_dir, home=home) / MEMORY_DIRNAME


def global_skills_root(*, home: str | Path | None = None) -> Path:
    """`<claude_home>/skills` — the CONFIRMED global skill store."""
    return claude_home(home) / SKILLS_DIRNAME


def project_skills_root(project_dir: str | Path) -> Path:
    """`<project_dir>/.claude/skills` — the project-scoped skill store (module docstring).

    CONFIRMED by a control experiment, with the shadowing/restart caveats stated there.
    """
    return Path(project_dir).expanduser().resolve() / CLAUDE_DIRNAME / SKILLS_DIRNAME


def transcript_files(project_dir: str | Path, *, home: str | Path | None = None) -> list[Path]:
    """Every `<session-id>.jsonl` in this project's storage directory, sorted, resolved.

    Only files whose resolved parent is still the storage directory are returned — the same
    containment discipline `list_targets` applies to `memory_dir`, for the same reason (a symlink
    inside the directory resolves outside it, and its content would become LM context).
    """
    storage = project_storage_dir(project_dir, home=home)
    if not storage.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(storage.glob(TRANSCRIPT_GLOB)):
        resolved = path.resolve()
        if resolved.parent == storage and resolved.is_file():
            found.append(resolved)
    return found


def render_transcript_events(events: Iterable[dict]) -> str:
    """Render raw JSONL events as the `"{role}: {text}"` text this project's pipeline expects.

    DELIBERATELY LOSSY, and the choice is stated here rather than baked in silently: a real long
    conversation's raw JSONL is multi-megabyte, and re-inflating it would defeat the point of
    distilling. What survives is a transcript's SUBSTANCE — what was said and decided:

    * only `user` / `assistant` events are rendered at all. Every other event type lacks `message`
      entirely (see `RENDERED_EVENT_TYPES`), so this is a filter, not a preference.
    * `isSidechain` events are skipped. This is a DEFENSIVE NO-OP, stated accurately: across two
      full real transcripts (1216 events) it was `false` on every single one — a subagent's messages
      live in separate `subagents/agent-<id>.jsonl` files, they are NOT inlined here. The filter
      costs nothing and guards a future version that inlines them; it is not currently removing
      "subagent noise", and claiming it does would be wrong.
    * `message.content` is EITHER a plain string (really occurs) or a list of typed blocks. A string
      is used as-is; in a list, `text`/`thinking` contribute their text verbatim, `tool_use` a short
      `[used tool: X]` label, and `tool_result` a size label whose UNIT depends on its OWN content
      being a string or a list (both really occur — never assume one shape for it).
    * an unrecognized block type contributes `[unrecognized content block: X]` rather than raising
      or silently vanishing, so a future block type shows up as a visible gap instead of a lie.

    Subagent transcripts are a real deferred extension (the same file shape, a different glob), not
    built here.
    """
    lines: list[str] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") not in RENDERED_EVENT_TYPES:
            continue
        if event.get("isSidechain"):
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        text = _render_content(message.get("content"))
        if not text.strip():
            continue  # an event with nothing renderable adds a bare "role:" line and no substance
        role = message.get("role") or event.get("type")
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def render_transcript_file(path: str | Path) -> str:
    """`render_transcript_events` over one `<session-id>.jsonl` file.

    A line that is blank or not valid JSON is skipped rather than fatal: a session being written
    right now legitimately has a torn last line, and one bad line must not lose the conversation.
    """
    return render_transcript_events(_read_jsonl(Path(path)))


def _render_content(content: Any) -> str:
    """The `message.content` half of the rendering rule — plain string OR list of typed blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        rendered = _render_block(block)
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)


def _render_block(block: Any) -> str:
    if not isinstance(block, dict):
        # Not a shape the real wire produced; named rather than dropped, same as an unknown type.
        return f"[unrecognized content block: {type(block).__name__}]"
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text") or "")
    if kind == "thinking":
        return str(block.get("thinking") or "")
    if kind == "tool_use":
        return f"[used tool: {block.get('name') or 'unknown'}]"
    if kind == "tool_result":
        inner = block.get("content")
        if isinstance(inner, str):
            return f"[tool result: {len(inner)} chars]"
        if isinstance(inner, list):
            return f"[tool result: {len(inner)} blocks]"
        # Neither shape the research observed. The design specifies the two real ones; this names
        # the leftover instead of guessing a size, so it cannot silently read as "empty result".
        return "[tool result: unrecognized content shape]"
    return f"[unrecognized content block: {kind}]"


def _read_jsonl(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in _read_text(path).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            loaded = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(loaded, dict):
            events.append(loaded)
    return events


class ClaudeCodeAdapter(HarnessAdapter):
    """Read one Claude Code project's `memory/` directory, skills, and transcripts."""

    @classmethod
    def for_project(
        cls, project_dir: str | Path, *, home: str | Path | None = None
    ) -> ClaudeCodeAdapter:
        """Discover the REAL storage for `project_dir` — memory, transcripts, and BOTH skill scopes.

        The alternate constructor that removes the path-assembly step: it computes `sanitize(project_dir)`,
        derives `<claude_home>/projects/<sanitized>/memory`, renders every sibling
        `<session-id>.jsonl` as one transcript, and points skill enumeration at the global
        (`<claude_home>/skills`) and project (`<project_dir>/.claude/skills`) roots.

        Nothing here has to exist: a project with no storage yet yields an adapter with no
        transcripts and an empty index, which is a normal input (everything is a promotion).
        `home=` overrides `~/.claude` — that is how the tests stay hermetic.
        """
        return cls(
            memory_dir_for_project(project_dir, home=home),
            transcripts=[render_transcript_file(p) for p in transcript_files(project_dir, home=home)],
            global_skills_dir=global_skills_root(home=home),
            project_skills_dir=project_skills_root(project_dir),
        )

    def __init__(
        self,
        memory_dir: str | Path,
        transcripts: Iterable[str] = (),
        *,
        global_skills_dir: str | Path | None = None,
        project_skills_dir: str | Path | None = None,
    ) -> None:
        # `.resolve()` once, here: every path this adapter hands out is absolute. Combined with
        # `list_targets`'s containment check below, this is what lets `read_memory_file` do an EXACT
        # path match against the snapshot instead of a prefix/substring comparison a `..` segment in
        # a REQUESTED path could slip past. Resolving alone does NOT stop a symlink that already
        # lives inside `memory_dir` at enumeration time — that is a separate, second check (an
        # adversarial review found the first draft only guarded requests, not enumeration, and let a
        # pre-existing symlink's resolved target join the snapshot as if it were a real memory file).
        self.memory_dir = Path(memory_dir).resolve()
        self._transcripts: list[str] = [str(t) for t in transcripts]
        # Skill enumeration is OPT-IN on this constructor: `ClaudeCodeAdapter(memory_dir)` (which is
        # what `apply.py`'s re-scan builds) must never silently reach into a real `~/.claude/skills`.
        # `for_project` is the constructor that resolves the real roots.
        self.global_skills_dir = Path(global_skills_dir).resolve() if global_skills_dir else None
        self.project_skills_dir = Path(project_skills_dir).resolve() if project_skills_dir else None

    # -- HarnessAdapter -------------------------------------------------------------------

    def ingest(self) -> RawSession:
        """Snapshot this harness's transcripts + memory index into one `RawSession`.

        Called EXACTLY ONCE per run by `session.run_distillation`; the resulting `memory_index` list
        is the immutable snapshot every tool closes over from then on (no tool ever holds a live
        adapter reference, so the allowlist cannot shift mid-run).
        """
        return RawSession(transcripts=list(self._transcripts), memory_index=self.list_targets())

    def list_targets(self) -> list[ArtifactRef]:
        """Enumerate `memory_dir`'s `*.md` files, `MEMORY.md` as `kind="index"`, and both skill roots.

        Returns an empty list when nothing exists — a project with no memory store yet
        is a normal input (everything is a promotion candidate), not an error.

        CONTAINMENT CHECK (fixed after an adversarial review reproduced a real escape): a symlink
        living inside `memory_dir` can resolve to a path OUTSIDE it. `glob` follows symlinks, so a
        naive enumeration would happily add that outside target to the snapshot — and everything
        downstream (`read_memory_file`'s allowlist) trusts the snapshot completely, by design. So a
        resolved path is only enumerated when its PARENT is still exactly `memory_dir` itself; a
        symlink pointing elsewhere is silently skipped rather than joining the trusted snapshot.
        """
        refs: list[ArtifactRef] = self._memory_refs()
        # Skills come AFTER memory so the memory-first ordering existing callers see is unchanged.
        refs.extend(_skill_refs(self.global_skills_dir, "global"))
        refs.extend(_skill_refs(self.project_skills_dir, "project"))
        return refs

    def _memory_refs(self) -> list[ArtifactRef]:
        """The memory half of `list_targets` — `memory_dir/*.md` plus `MEMORY.md` as the index.

        Every ref here is `scope="project"` by construction (`ArtifactRef`'s kind-derived default):
        a memory store has no global counterpart, so there is no other honest value.
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
            # Anthropic's documented Agent-Skills convention requires `name` + `description` ONLY,
            # and that is what this reports as REQUIRED. `when_to_use` / `dispatch_intent` are
            # described as OPTIONAL because every real installed skill the research inspected does
            # carry them — but all of those were one author's single suite, so treating them as
            # mandatory would generalize from N=1 (`CLAUDE.md` invariant (7), corrected per audit).
            # This schema must keep describing the same shape `make_skill_validator` actually
            # ENFORCES; the two drifting apart is the specific failure mode that invariant calls out.
            return {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    # Optional, pass-through: accepted verbatim when present, never required.
                    "when_to_use": {"type": "string"},
                    "dispatch_intent": {"type": "string"},
                },
            }
        raise ValueError(f"unknown artifact kind {kind!r}")


def _skill_refs(root: Path | None, scope: ArtifactScope) -> list[ArtifactRef]:
    """Enumerate `<root>/*/SKILL.md` as `kind="skill"` refs at `scope`.

    A skill is a DIRECTORY (confirmed), so the ref's `name` falls back to the DIRECTORY name when
    frontmatter carries none — never `SKILL` (the file stem), which would name every skill the same.

    Containment mirrors the memory side: the resolved `SKILL.md` must sit exactly one level under
    `root`, so a symlinked skill directory resolving outside the store never joins the trusted
    snapshot. `references/` / `scripts/` / any other sibling file is ignored — this project reads and
    writes the `SKILL.md` body only.
    """
    if root is None or not root.is_dir():
        return []
    refs: list[ArtifactRef] = []
    for path in sorted(root.glob(f"*/{SKILL_FILENAME}")):
        resolved = path.resolve()
        if resolved.parent.parent != root or not resolved.is_file():
            continue
        meta, _body = frontmatter.parse(_read_text(resolved))
        refs.append(
            ArtifactRef(
                name=str(meta.get("name") or resolved.parent.name),
                description=str(meta.get("description") or ""),
                kind="skill",
                path=str(resolved),
                scope=scope,
            )
        )
    return refs


def _read_text(path: Path) -> str:
    """Read one file's text, degrading to "" on any read error (a permission-denied or
    concurrently-removed memory file must not sink enumeration of the rest)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
