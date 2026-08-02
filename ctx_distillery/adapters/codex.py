"""The Codex CLI harness adapter — the SECOND concrete `HarnessAdapter`, read-only ingestion only.

**Evidence tier, stated up front rather than left implicit (CLAUDE.md invariant 6's own discipline):**
every fact this module is built against was confirmed by reading `openai/codex`'s own SOURCE at HEAD
(via the GitHub API, in the session that designed this module) — never against a real, installed
Codex CLI process or a dedicated control experiment on a real machine, the stronger evidence tier
several `ClaudeCodeAdapter` facts rest on. `main` may be ahead of any released build a real operator
runs. Treat every claim below as "confirmed from source, not yet confirmed on a live install."

**Scope: READ-ONLY INGESTION ONLY.** `apply.py` is entirely Claude-Code-specific (invariant 9) and
is UNTOUCHED by this module — it does not consult any adapter when writing. A Codex-sourced run
produces a real judgement-only plan, reviewable via `ctx-distillery show`, but `ctx-distillery-apply`
cannot yet write a `promote_to_skill`/`promote_to_memory` candidate INTO Codex's own store. This
mirrors (and half-closes) the "no adapter for any harness other than Claude Code" known
simplification; it does not create a matching writer.

**Structural facts, each confirmed from a specific source file this session:**

* Sessions ("rollouts") live at `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl`
  (`codex-rs/rollout/src/list.rs`, the `rollout_date_parts` function and its own doc comment).
  Compressed (`.zst`) rollouts are OUT OF SCOPE — this module reads plain `.jsonl` only; a
  compressed/archived session is simply not found, degrading to "fewer transcripts", never an error.
* Each JSONL line is `{"timestamp": ..., "ordinal": int|null, "type": "session_meta"|
  "response_item"|..., "payload": {...}}` — `RolloutLine`/`RolloutItem` in
  `codex-rs/protocol/src/protocol.rs`, tagged `#[serde(tag="type", content="payload")]` with
  `#[serde(flatten)]` on `RolloutLine.item`. A `session_meta` line's `payload` carries `cwd` as a
  DIRECT key (a sibling of `git`), NEVER nested under a `"meta"` key — `SessionMetaLine`'s `meta`
  field is itself `#[serde(flatten)]`, confirmed airtight by its custom `Deserialize` impl checking
  `fields.contains_key("session_id")` directly on the top-level object. Getting this wrong (reading
  `payload["meta"]["cwd"]` instead of `payload["cwd"]`) was a REAL bug caught by adversarial review
  before this shipped — it would have made `for_project` match ZERO sessions for every project,
  forever, indistinguishable from "no Codex history yet."
* Conversational text lives in `type=="response_item"` items whose `payload.type=="message"`, role
  `"user"`/`"assistant"`, `payload.content` a list of `{"type": "input_text"|"output_text", "text":
  ...}` blocks (`codex-rs/protocol/src/models.rs`, `ResponseItem`/`ContentItem`). The full
  `RolloutItem`/`ResponseItem`/`EventMsg` schema is large and actively evolving (a `Legacy` vs.
  `Paginated` `ThreadHistoryMode` split alone implies dozens of variants) — this renderer is
  DELIBERATELY LOSSY, the same stance `render_transcript_file` already states for Claude Code:
  tool-call-shaped items (`function_call`/`local_shell_call`/`tool_search_call`/`custom_tool_call`/
  `web_search_call`/`image_generation_call`) render as `[used tool: <name>]` (mirroring Claude
  Code's own `[used tool: X]` label — Codex's shell/patch calls are a coding agent's actual
  substance, not noise to drop silently the way a first draft of this module did), and every OTHER
  item (reasoning, session meta, compaction, turn context, world state, inter-agent comms, a
  `"developer"`-role message) is silently absent from the render.
* `AGENTS.md` discovery (`codex-rs/core/src/agents_md.rs`): walk UP from `project_dir` to the
  nearest `.git` (Codex's own DEFAULT `project_root_markers`; if none is found, only `project_dir`
  itself is searched), then collect ROOT-TO-LEAF: at each directory, `AGENTS.override.md` wins over
  `AGENTS.md` if both exist (first match in that order), concatenated with plain `"\n\n"` between
  directories. **Simplifications, stated**: no operator-configured extra fallback filenames (Codex's
  own `project_doc_fallback_filenames`, read from ITS OWN TOML config — replicating that means
  parsing Codex's config format, out of scope); no length cap (matching the same "no cap" decision
  already made, and already corrected once by adversarial review, for `project_instructions`
  elsewhere in this project); and the global `~/.codex/AGENTS.md` ("user instructions", a DIFFERENT
  code path in Codex, joined with a DIFFERENT separator) is NOT read — the identical "project-only,
  never the operator's cross-project file" scope decision already made for Claude Code's `CLAUDE.md`.
* Skills (`codex-rs/core-skills/src/loader.rs`): `SKILL.md` under `.agents/skills/<name>/`, at
  scope "project" — checked at EVERY directory in the SAME root-to-`project_dir` walk AGENTS.md
  discovery uses (`repo_agents_skill_roots`, NOT a single fixed `<project_dir>/.agents/skills/` —
  a first draft of this module got that wrong too) — and scope "global" at `~/.agents/skills/`, a
  SIBLING of `~/.codex` (not nested inside it: `home_dir.join(".agents").join("skills")`).
* **Codex's memory system (`~/.codex/memories/`) is DELIBERATELY NOT READ AT ALL** — not a
  simplification, a correctness decision. `codex-rs/memories/README.md` describes Phase 2 as
  "**Global** Consolidation" against "**a single global** phase-2 lock" and "**the** memories
  root," with neither phase's eligibility rules mentioning `cwd`/project scoping anywhere. It is a
  single, MACHINE-WIDE store consolidated across every project the operator has ever used Codex on
  — structurally UNLIKE Claude Code's per-project `memory/`. Including it in a *per-project*
  `for_project()` snapshot would silently mix another, unrelated project's learnings into this
  project's plan — a real bug, not an incompleteness. `list_targets()` therefore enumerates skills
  only; `schema_for("memory")`/`schema_for("index")` return `{}` (nothing to validate against —
  there is nothing for the closed read-only tool set to enumerate under those kinds for this
  harness in the first place).

**The one real structural difference from `ClaudeCodeAdapter`, and its named cost**: Claude Code
partitions storage by project on disk (`~/.claude/projects/<sanitize(project_dir)>/`) — finding
"this project's" transcripts is a free directory glob. Codex does NOT: every rollout on the machine
lives under the SAME `~/.codex/sessions/` tree regardless of project, with the project recorded
INSIDE each file as `SessionMeta.cwd`. So `for_project` must open EVERY rollout on the machine far
enough to read its `session_meta` line (mirroring Codex's OWN `read_session_meta_line` helper's
tolerance for leading non-meta lines, capped at `_MAX_META_SCAN_LINES` so a corrupted/hostile file
that never yields one cannot make this an unbounded per-file read) and check whether `cwd` matches
— a real, stated cost proportional to every session ever recorded, not just this project's own. A
SQLite-index-based fast path (Codex's own `state_*.sqlite`) is a documented, deferred follow-up if
this proves too slow in practice; it would be a new, less-confirmed schema this project would then
be coupled to, which is not a cost to take on speculatively.

No containment/symlink check on rollout enumeration itself: every file under `~/.codex/sessions/`
is written by the operator's own local Codex process on this machine, never populated from a
cloned/untrusted repository the way `.agents/skills/`/`AGENTS.md` (living inside `project_dir`,
which COULD be someone else's cloned repo) are — so the symlink-escape threat model
`_skill_refs`/`_project_agents_md` defend against does not apply there.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .. import frontmatter
from .base import ArtifactKind, ArtifactRef, ArtifactScope, HarnessAdapter, RawSession

#: `~/.codex` — CONFIRMED from source this session (see the module docstring's evidence-tier note).
CODEX_HOME_DIRNAME = ".codex"
#: `<codex_home>/sessions/YYYY/MM/DD/rollout-*.jsonl`.
SESSIONS_DIRNAME = "sessions"
#: `<dir>/.agents/skills/<name>/SKILL.md` (project, at every directory in the root-to-leaf walk) and
#: `~/.agents/skills/<name>/SKILL.md` (global, a SIBLING of `~/.codex`, not nested inside it).
AGENTS_DIRNAME = ".agents"
SKILLS_DIRNAME = "skills"
#: Own module-level copy, not an import from `claude_code.py` — a harness's OWN vocabulary,
#: coincidentally spelled the same as another harness's, same reasoning `MEMORY_TYPES` etc. already
#: gets there: keeping the two adapters independently readable rather than cross-coupled.
SKILL_FILENAME = "SKILL.md"
#: The two built-in AGENTS.md filenames, in PRECEDENCE order — override wins when both exist.
AGENTS_OVERRIDE_FILENAME = "AGENTS.override.md"
AGENTS_MD_FILENAME = "AGENTS.md"
#: The project-root marker `agents_md.rs` uses by DEFAULT (an operator can reconfigure this in their
#: own Codex TOML config; replicating that is out of scope — see the module docstring).
GIT_DIRNAME = ".git"
#: Plain JSONL only — see the module docstring's ".zst out of scope" note.
ROLLOUT_GLOB = "rollout-*.jsonl"
#: A generous cap on how many LEADING lines `_rollout_cwd` will scan looking for `session_meta`
#: before giving up. `SessionMeta` is always the first PERSISTED item in a real rollout (Codex's own
#: `read_session_meta_line` errors if a `ResponseItem`/`InterAgentCommunication` arrives first), so
#: this is generous slack for a few harmless leading lines, never a real file's actual position — it
#: exists so a corrupted/hostile file that never yields a `session_meta` cannot make this an
#: unbounded per-file read, which matters because this scan runs once per file across EVERY session
#: on the machine (see the module docstring's "named cost" note).
_MAX_META_SCAN_LINES = 50
#: `ResponseItem` variants that are Codex's analogue of Claude Code's `tool_use` block — rendered as
#: `[used tool: X]` rather than silently dropped (see the module docstring).
_TOOL_CALL_ITEM_TYPES = frozenset(
    {
        "function_call",
        "local_shell_call",
        "tool_search_call",
        "custom_tool_call",
        "web_search_call",
        "image_generation_call",
    }
)


def _home_dir(*, home: str | Path | None = None) -> Path:
    """The operator's OS home directory, or the explicit override a test supplies.

    Codex's two global roots are NOT both nested under `~/.codex` — sessions are
    (`~/.codex/sessions/`), but global skills are a SIBLING of it (`~/.agents/skills/`, confirmed at
    `codex-rs/core-skills/src/loader.rs`) — so this returns the thing both are actually relative to,
    unlike Claude Code's `claude_home()` (where nearly everything nests under one `~/.claude` root).
    """
    if home is not None:
        return Path(home).expanduser().resolve()
    return Path.home().resolve()


def codex_home(*, home: str | Path | None = None) -> Path:
    """`~/.codex` — CONFIRMED from source this session (see the module docstring)."""
    return _home_dir(home=home) / CODEX_HOME_DIRNAME


def global_skills_root(*, home: str | Path | None = None) -> Path:
    """`~/.agents/skills` — Codex's user-scoped skill store, a SIBLING of `~/.codex`, not nested
    inside it (confirmed: `codex-rs/core-skills/src/loader.rs`,
    `home_dir.join(AGENTS_DIR_NAME).join(SKILLS_DIR_NAME)`)."""
    return _home_dir(home=home) / AGENTS_DIRNAME / SKILLS_DIRNAME


def _iter_rollout_files(home: Path) -> Iterator[Path]:
    """Every `rollout-*.jsonl` under `<home>/sessions/**`, recursive (the `YYYY/MM/DD` buckets),
    sorted (== chronological, since the timestamp is embedded in the filename). No containment
    check — see the module docstring for why that threat model doesn't apply here."""
    sessions_root = home / SESSIONS_DIRNAME
    if not sessions_root.is_dir():
        return
    yield from sorted(sessions_root.glob(f"**/{ROLLOUT_GLOB}"))


def _rollout_cwd(path: Path) -> Path | None:
    """The `cwd` this rollout was recorded under, or `None` if unreadable/absent/malformed/not
    found within `_MAX_META_SCAN_LINES` — never raises. Mirrors Codex's own `read_session_meta_line`
    tolerance for leading non-`session_meta` lines, capped (see `_MAX_META_SCAN_LINES`'s docstring).
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for _ in range(_MAX_META_SCAN_LINES):
                line = handle.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "session_meta":
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    return None
                cwd = payload.get("cwd")
                if not isinstance(cwd, str) or not cwd:
                    return None
                try:
                    return Path(cwd).expanduser().resolve()
                except (OSError, ValueError):
                    return None
    except OSError:
        return None
    return None


def _render_rollout_file(path: Path) -> str:
    """`user: .../assistant: ...` for message items, `[used tool: X]` for tool-call-shaped items,
    everything else silently absent — see the module docstring's "DELIBERATELY LOSSY" note."""
    lines_out: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "response_item":
                    continue
                payload = obj.get("payload")
                if not isinstance(payload, dict):
                    continue
                item_type = payload.get("type")
                if item_type == "message":
                    role = payload.get("role")
                    if role not in ("user", "assistant"):
                        continue
                    content = payload.get("content")
                    if not isinstance(content, list):
                        continue
                    text = "".join(
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict)
                        and block.get("type") in ("input_text", "output_text")
                        and isinstance(block.get("text"), str)
                    )
                    if text.strip():
                        lines_out.append(f"{role}: {text}")
                elif item_type in _TOOL_CALL_ITEM_TYPES:
                    name = payload.get("name")
                    lines_out.append(f"[used tool: {name if isinstance(name, str) and name else item_type}]")
                # every other item_type (reasoning, additional_tools, ...) is silently absent
    except OSError:
        return ""
    return "\n".join(lines_out)


def _project_root_and_search_dirs(project_dir: str | Path) -> list[Path]:
    """Walk UP from `project_dir` to the nearest `.git` (Codex's own DEFAULT
    `project_root_markers`); return the directories from that root DOWN to `project_dir`, inclusive,
    root-to-leaf order — or just `[project_dir]` if no `.git` is found. ONE implementation, shared
    by AGENTS.md discovery and skills discovery below (mirroring `agents_md.rs`'s own walk, which
    `repo_agents_skill_roots` reuses for skills too — a first draft of this module kept a second,
    silently-narrower copy for skills that only checked `project_dir` itself).
    """
    start = Path(project_dir).expanduser().resolve()
    cursor = start
    root: Path | None = None
    while True:
        if (cursor / GIT_DIRNAME).exists():
            root = cursor
            break
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    if root is None:
        return [start]
    dirs: list[Path] = []
    cursor = start
    while True:
        dirs.append(cursor)
        if cursor == root:
            break
        cursor = cursor.parent
    dirs.reverse()
    return dirs


def _project_agents_md(project_dir: str | Path) -> str:
    """`AGENTS.override.md` else `AGENTS.md`, at EACH `_project_root_and_search_dirs` entry, joined
    with plain `"\\n\\n"`, root-to-leaf — never the global `~/.codex/AGENTS.md` (see the module
    docstring). Containment: a found file's resolved parent must still be the directory it was
    found in (mirrors `project_claude_md_path`'s exact pattern) — a symlink escaping that directory
    contributes nothing for THAT directory (no fallback to the other filename), never an exception.
    """
    parts: list[str] = []
    for directory in _project_root_and_search_dirs(project_dir):
        for filename in (AGENTS_OVERRIDE_FILENAME, AGENTS_MD_FILENAME):
            candidate = directory / filename
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except (OSError, ValueError):
                break
            if resolved.parent == directory:
                text = _read_text(resolved)
                if text.strip():
                    parts.append(text)
            break
    return "\n\n".join(parts)


def _skill_refs(root: Path, scope: ArtifactScope) -> list[ArtifactRef]:
    """Enumerate `<root>/*/SKILL.md` as `kind="skill"` refs at `scope` — the same shape and
    containment discipline `claude_code._skill_refs` uses (own copy, not an import; see
    `SKILL_FILENAME`'s own docstring for why). `root` must already be the TRUSTED, un-re-resolved
    path it was constructed from — never re-resolved here, so an intermediate symlink (e.g. `.agents`
    itself) cannot validate its own escape.
    """
    if not root.is_dir():
        return []
    refs: list[ArtifactRef] = []
    for path in sorted(root.glob(f"*/{SKILL_FILENAME}")):
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            continue
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


def _project_skill_refs(project_dir: str | Path) -> list[ArtifactRef]:
    """`<dir>/.agents/skills/*/SKILL.md` at EVERY `_project_root_and_search_dirs` entry — the
    corrected algorithm (see that function's docstring)."""
    refs: list[ArtifactRef] = []
    for directory in _project_root_and_search_dirs(project_dir):
        refs.extend(_skill_refs(directory / AGENTS_DIRNAME / SKILLS_DIRNAME, "project"))
    return refs


def _read_text(path: Path) -> str:
    """Read one file's text, degrading to "" on any read error — the same stance
    `claude_code._read_text` already takes (own copy here, same reasoning as `SKILL_FILENAME`)."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


class CodexAdapter(HarnessAdapter):
    """Read one Codex CLI project's rollout sessions, `AGENTS.md`, and `.agents/skills/`.

    READ-ONLY INGESTION ONLY — see the module docstring's "Scope" note: `apply.py` cannot yet write
    a Codex-sourced promotion anywhere Codex-native, and this class does not change that.
    """

    @classmethod
    def for_project(cls, project_dir: str | Path, *, home: str | Path | None = None) -> CodexAdapter:
        """Discover the real storage for `project_dir` — see the module docstring for the full
        algorithm and its one real structural difference from `ClaudeCodeAdapter` (a machine-wide
        rollout scan, not a free directory partition). `home=` overrides the operator's real home
        directory — how tests stay hermetic, matching every discovery helper in this package.
        """
        target = Path(project_dir).expanduser().resolve()
        home_root = codex_home(home=home)
        matches = [p for p in _iter_rollout_files(home_root) if _rollout_cwd(p) == target]
        return cls(
            transcripts=[_render_rollout_file(p) for p in matches],
            project_dir=target,
            global_skills_dir=global_skills_root(home=home),
            project_instructions=_project_agents_md(target),
        )

    def __init__(
        self,
        *,
        transcripts: Iterable[str] = (),
        project_dir: str | Path | None = None,
        global_skills_dir: str | Path | None = None,
        project_instructions: str = "",
    ) -> None:
        """Skill enumeration is OPT-IN on this constructor, same reasoning as
        `ClaudeCodeAdapter.__init__`: a bare `CodexAdapter(transcripts=[...])` must never silently
        reach into a real `~/.codex`/`~/.agents` — `for_project` is the path that resolves the real
        locations. `project_dir` (when given) is stored RESOLVED, and `list_targets()` re-derives the
        project skill roots from it fresh on every call (never cached at construction) — the same
        "live re-scan, not a snapshot" behavior `ClaudeCodeAdapter.list_targets()` already has.
        """
        self._transcripts: list[str] = [str(t) for t in transcripts]
        self.project_dir = Path(project_dir).expanduser().resolve() if project_dir is not None else None
        self.global_skills_dir = (
            Path(global_skills_dir).expanduser().resolve() if global_skills_dir is not None else None
        )
        self.project_instructions = project_instructions

    # -- HarnessAdapter -------------------------------------------------------------------

    def ingest(self) -> RawSession:
        """Snapshot this harness's transcripts + skill index (+ `AGENTS.md`) into one `RawSession`.

        Called EXACTLY ONCE per run — same contract `ClaudeCodeAdapter.ingest()` documents.
        """
        return RawSession(
            transcripts=list(self._transcripts),
            memory_index=self.list_targets(),
            project_instructions=self.project_instructions,
        )

    def schema_for(self, kind: ArtifactKind) -> dict[str, Any]:
        """The structural schema a valid `kind` artifact must satisfy for Codex.

        `"memory"`/`"index"` return `{}` — DELIBERATELY, not an oversight: Codex has no per-project
        memory concept at all (see the module docstring's "memory system" note), so
        `list_targets()` never enumerates either kind for this harness, and nothing in this project
        ever validates a draft against this schema anyway (`apply.py` is Claude-Code-only).
        `"skill"` mirrors Claude Code's own shape (`name` + `description` required) — Codex's own
        skills docs confirm the identical two-field requirement.
        """
        if kind == "skill":
            return {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
            }
        return {}

    def list_targets(self) -> list[ArtifactRef]:
        """Enumerate project + global skills — see the module docstring for why there is no memory
        enumeration for this harness at all."""
        refs: list[ArtifactRef] = []
        if self.project_dir is not None:
            refs.extend(_project_skill_refs(self.project_dir))
        if self.global_skills_dir is not None:
            refs.extend(_skill_refs(self.global_skills_dir, "global"))
        return refs
