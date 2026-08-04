"""The Claude Code harness adapter — the ONE concrete adapter this project scopes in.

Claude Code is the only harness whose real persistence format has been directly verified (a
per-project `memory/` directory of `*.md` files with `name` / `description` / nested
`metadata.type` frontmatter, plus a `MEMORY.md` index). Codex / Hermes / OpenClaw / OpenCode stay
deliberately undesigned — see `CLAUDE.md`, "Harness scope".

Read-only, per `CLAUDE.md` invariant (4): this module opens files for reading only and has no
write/emit path of any kind.

**Auto-discovery (`for_project`)** now locates the real storage instead of making the caller
assemble it (`CLAUDE.md` invariant (6)). Every part below is confirmed, but the EVIDENCE differs —
a first-party SDK source, a dedicated control experiment, and plain observation of a real corpus are
not the same strength of claim, so each says which one it rests on rather than just "CONFIRMED":

* `sanitize(project_dir)` — every `/` of the project's absolute path replaced by `-`, giving
  `~/.claude/projects/<sanitized>/` — is CONFIRMED against real project directories. No other
  transformation is applied, because none was observed.
* the `memory/` SUB-PATH inside it began as this project's PRE-EXISTING assumption, carried forward
  without independent verification because no `memory/` directory existed on the machine the original
  research ran on. It is CONFIRMED now: 12 of 24 project storage directories carry one, every one a
  DIRECT child of the project storage directory, holding 51 `.md` files and 9 `MEMORY.md` indexes —
  the assumed layout exactly. Auto-discovery only ever needed the sanitization rule to be right.
* TRANSCRIPTS are CONFIRMED: one JSONL file per past conversation, `<session-id>.jsonl`, sibling to
  `memory/`. `render_transcript_events` turns those raw events into the `list[str]` this project
  already expects — a deliberately LOSSY rendering (see its docstring), not a full replay.
* SUBAGENT transcripts live at
  `~/.claude/projects/<sanitized>/<session-id>/subagents/**/agent-<agent-id>.jsonl` —
  **recursively**: directly under `subagents/`, and nested under `subagents/workflows/<run-id>/`
  (claude-agent-sdk 0.2.116, `_internal/session_import.py:89-94` and `_internal/sessions.py:1210-1238`,
  which is FIRST-PARTY, not inferred from one machine). Each carries a sibling `<stem>.meta.json`
  whose only REQUIRED keys are `agentType` and `spawnDepth`; `description`, `toolUseId`,
  `parentAgentId`, `model` and `stoppedByUser` are OPTIONAL and really are absent — on the corpus
  this was measured against, every nested file (299 of 874) carried `agentType` + `spawnDepth` and
  nothing else. `journal.jsonl` also lives under `subagents/` and is NOT a transcript; the `agent-`
  filename filter is what excludes it. Discovery is `subagent_files`; ingestion is OPT-IN
  (`for_project(..., include_subagents=True)`).
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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import frontmatter
from .base import ArtifactKind, ArtifactRef, ArtifactScope, HarnessAdapter, RawSession, TranscriptId

#: The four values Claude Code's memory-file frontmatter allows for `metadata.type`.
MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")

#: The harness's memory index file — enumerated as `kind="index"`, not `kind="memory"`.
INDEX_FILENAME = "MEMORY.md"

#: `~/.claude` — the user-global Claude Code home, and the parent of both `projects/` and `skills/`.
CLAUDE_DIRNAME = ".claude"
#: `~/.claude/projects/<sanitize(project_dir)>/` — one directory per project.
PROJECTS_DIRNAME = "projects"
#: The per-project memory store, inside that directory (see the module docstring for its evidence).
MEMORY_DIRNAME = "memory"
#: `~/.claude/skills/` (global) and `<project_dir>/.claude/skills/` (project) both use this name.
SKILLS_DIRNAME = "skills"
#: Each skill is a DIRECTORY containing this file (plus optional `references/`, `scripts/`, ...).
SKILL_FILENAME = "SKILL.md"
#: One past conversation per file, `<session-id>.jsonl`, sibling to `memory/`.
TRANSCRIPT_GLOB = "*.jsonl"
#: The project's own instructions file — `<project_dir>/CLAUDE.md`, or `<project_dir>/.claude/CLAUDE.md`
#: (see `project_claude_md_path`). NOT the global `~/.claude/CLAUDE.md` — that is the operator's own
#: cross-project preference, out of scope for a per-project distillation.
CLAUDE_MD_FILENAME = "CLAUDE.md"

#: `<project storage>/<session-id>/subagents/` — the SIDE-STORAGE directory a session gets when it
#: spawned subagents. The `<session-id>/` level is named for the session FILE's stem, by
#: construction (`session_import.py:90`: `session_dir = resolved.with_suffix("")`).
SUBAGENTS_DIRNAME = "subagents"
#: A subagent transcript is `agent-<agent-id>.jsonl`. The prefix is NOT decoration: `journal.jsonl`
#: (one per workflow run directory) lives under `subagents/` too and is not a transcript, so the
#: SDK's own transcript-reading helper (`sessions._collect_agent_files`) filters on exactly this,
#: while its store-MIRRORING helper (`session_import._collect_jsonl_files`) deliberately does not.
#: Follow the reading one.
AGENT_FILE_PREFIX = "agent-"
#: The metadata sidecar beside each transcript: `agent-<id>.jsonl` -> `agent-<id>.meta.json`.
META_SUFFIX = ".meta.json"
#: `subagents/workflows/<run-id>/agent-<id>.jsonl` — a WORKFLOW-nested agent. Not to be confused
#: with the sibling `<session-id>/workflows/`, which holds run DEFINITIONS + `scripts/*.js` and is
#: not read here at all.
WORKFLOWS_DIRNAME = "workflows"

#: How many characters of a session/agent id a HEADER shows. A disambiguator for a human or a
#: model, never a lookup key — the entry INDEX is the only key anything needs, because that is what
#: `read_transcript_chunk` takes.
SHORT_ID_CHARS = 8

#: The widest line 0 (`subagent_header`/`session_header`'s index line) can be, measured over a real
#: 875-transcript corpus rendered through the exact format below and assuming a three-digit index
#: column (`[999]`, the worst case under 1,000 entries). Subagent lines ran 65..86 (median 76);
#: session lines are a flat 22. The 86 comes from a workflow-nested entry, which is structurally ~8
#: characters wider than a flat one because a workflow run id is NOT shortened.
#:
#: It is a real budget, not trivia: a full-index scan is one REPL cell, and `CD_MAX_OUTPUT_CHARS`
#: (40,000 by default) caps one cell's output — so the ceiling is
#: `max_output_chars // (INDEX_LINE_MAX + 1)` entries, i.e. 459 at the default. `cli._cmd_distill`
#: warns above it, and `tests/test_adapters_claude_code.py` pins the bound and that ceiling
#: TOGETHER, from this one constant, so they can never drift apart.
INDEX_LINE_MAX = 86

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

    **The WHOLE path is resolved, `.claude` included — not just the home directory.** This used to be
    `Path.home().resolve() / CLAUDE_DIRNAME`, which resolves the home component and leaves `.claude`
    itself unresolved. That is silent data loss for anyone who symlinks `~/.claude` into a dotfiles
    repo or an external volume, because `transcript_files` compares a RESOLVED path's parent against
    this directory: the parents never match, every session file is filtered out, and `storage.is_dir()`
    is still True so no "no storage here" branch fires. Reproduced with identical content on both
    sides — a real `~/.claude` yielded 1 transcript, a symlinked one yielded 0, with no error. Found
    by review of `subagent_files`' own containment, which is anchored on this value.
    """
    if home is not None:
        return Path(home).expanduser().resolve()
    return (Path.home() / CLAUDE_DIRNAME).resolve()


def project_storage_dir(project_dir: str | Path, *, home: str | Path | None = None) -> Path:
    """`<claude_home>/projects/<sanitize(project_dir)>` — the transcripts' + memory's container."""
    return claude_home(home) / PROJECTS_DIRNAME / sanitize_project_dir(project_dir)


def memory_dir_for_project(project_dir: str | Path, *, home: str | Path | None = None) -> Path:
    """`<project storage>/memory` — assumed at first, CONFIRMED later (see the module docstring)."""
    return project_storage_dir(project_dir, home=home) / MEMORY_DIRNAME


def global_skills_root(*, home: str | Path | None = None) -> Path:
    """`<claude_home>/skills` — the CONFIRMED global skill store."""
    return claude_home(home) / SKILLS_DIRNAME


def project_skills_root(project_dir: str | Path) -> Path:
    """`<project_dir>/.claude/skills` — the project-scoped skill store (module docstring).

    CONFIRMED by a control experiment, with the shadowing/restart caveats stated there.
    """
    return Path(project_dir).expanduser().resolve() / CLAUDE_DIRNAME / SKILLS_DIRNAME


def project_claude_md_path(project_dir: str | Path) -> Path | None:
    """`<project_dir>/CLAUDE.md`, or `<project_dir>/.claude/CLAUDE.md` if the root one is absent —
    the two locations CONFIRMED by Claude Code's own docs (`code.claude.com/docs/en/claude-directory`,
    fetched directly): "Also works at `.claude/CLAUDE.md` if you prefer to keep the project root
    clean." Root wins if BOTH somehow exist — THIS PROJECT'S OWN design choice, not a documented
    Claude Code precedence rule (the docs describe alternative locations, not a coexistence rule).
    `None` if neither exists.

    Deliberately does NOT walk parent directories and does NOT read the global `~/.claude/CLAUDE.md`
    — both confirmed to exist by the same docs, both out of scope: a parent-directory walk has no
    official confirmation as an automatic mechanism (separate from `.claude/rules/`, which is a
    different concept this project already treats as out of scope), and the global file is the
    operator's own cross-project preference, not this project's per-project memory.

    **Containment, mirroring `_memory_refs`'s enumeration-side check exactly**: the FULLY resolved
    candidate's parent must equal the (fixed, already-resolved) directory it was found in. This is
    not the same defense as `read_memory_file`'s request-side allowlist (there is no "request" here
    at all) — it is the OTHER hazard invariant 5 already names for `_memory_refs`: `Path.is_file()`
    and reading both follow symlinks transparently, so a git-tracked `CLAUDE.md -> /etc/passwd` (or
    any absolute path) in a CLONED, untrusted repository would otherwise have its target's bytes
    read as LM context. Comparing against the FIXED `parent` (never re-resolving "the expected
    parent" through the same chain being validated) is what also catches `.claude` itself being a
    symlink, not only `CLAUDE.md`. This does NOT break the documented `ln -s AGENTS.md CLAUDE.md`
    workaround (Claude Code has no native AGENTS.md support as of this research) — that symlink's
    target still resolves to a file in the SAME directory the symlink itself sits in.
    """
    root = Path(project_dir).expanduser().resolve()
    for parent in (root, root / CLAUDE_DIRNAME):
        candidate = parent / CLAUDE_MD_FILENAME
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if resolved.parent == parent:
            return resolved
    return None


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


@dataclass(frozen=True)
class SubagentTranscript:
    """One `agent-<id>.jsonl` under a session's `subagents/`, plus its sidecar's contents.

    `subpath` is deliberately named and shaped to match the SDK's own `SessionKey["subpath"]`
    (`session_import.py:97-103`) — same string, same construction, extension dropped. Two readers of
    one on-disk layout should not invent two names for the same thing.

    **Every meta-derived field degrades INDEPENDENTLY, and that is the live path, not a defensive
    one.** The real case is not a missing sidecar (0 of 874 on the measured corpus) but a PRESENT
    sidecar with absent keys: `agentType` + `spawnDepth` were on 874/874, while `description` and
    `toolUseId` were on the 575 flat files and NONE of the 299 nested ones, and `parentAgentId` on
    just the 44 flat files at depth > 1. So `agent_type`/`spawn_depth` are effectively required and
    everything else is absent-by-default.

    `meta` carries the whole parsed sidecar so a caller never re-reads it — `toolUseId`, `model` and
    `stoppedByUser` live there and deliberately never reach a header.
    """

    path: Path
    session_id: str
    agent_id: str
    subpath: str
    workflow_run: str | None = None
    agent_type: str | None = None
    spawn_depth: int | None = None
    description: str | None = None
    parent_agent_id: str | None = None
    meta: dict = field(default_factory=dict)


def subagent_files(
    project_dir: str | Path, *, home: str | Path | None = None
) -> list[SubagentTranscript]:
    """Every subagent transcript belonging to a session this project's storage already exposes.

    RECURSIVE under each session's `subagents/`, keeping `agent-*.jsonl` only. Both halves are the
    SDK's (`sessions._collect_agent_files`), and both are load-bearing: a flat glob misses the
    workflow-nested files (34% of the measured corpus), while `rglob("*.jsonl")` — the shape the
    SDK's store-mirroring helper uses — ingests the `journal.jsonl` sitting in every run directory.

    Iteration is driven off `transcript_files()`, never off a directory listing of the storage
    directory: that directory also contains `memory/`, so a naive "every child" scan walks straight
    into the memory store, and driving off the session files means every subagent found provably
    belongs to a session this run is already reading.

    CONTAINMENT (invariant 5's enumeration side, one directory deeper than `transcript_files`'s).
    Exact-parent alone is the wrong shape here — it would refuse every legitimately nested file — so
    the check is **exact-parent at each hop that must not move, prefix only for the depth that is
    legitimately unbounded**:

    1. the session directory resolves to a DIRECT CHILD of the storage directory;
    2. `subagents/` resolves to a DIRECT CHILD of that session directory;
    3. and only then is a file kept for being under that pinned root.

    Do NOT "simplify" this into `root = (session_dir / "subagents").resolve()` followed by a bare
    `is_relative_to(root)`. That was tried, argued for ("both operands are resolved, so nothing can
    fool the prefix test") and REPRODUCED FAILING: the argument is true of the children and false of
    the root, because resolving the root makes it MOVE WITH the symlink. Three arrangements escaped
    — `subagents/` symlinked outside, the session directory itself symlinked outside, and
    `subagents/` symlinked to the storage directory's own `memory/`. The last is the nastiest: its
    target is INSIDE storage, so any check phrased as "did we stay under the project's directory"
    passes while the memory store gets read as if it were a transcript.

    Nor may the roots be left UNRESOLVED: on macOS `/var` -> `/private/var`, so an unresolved
    ancestor mismatches every resolved child and this function silently returns ZERO files — a total
    loss of the feature that no test written against a non-symlinked temp directory would catch.
    Both sides of each `==` are resolved; that is what gives containment AND survives a
    resolved-ancestor platform.

    `storage_r` is belt-and-braces, and saying so is the point: `claude_home` resolves the whole
    `~/.claude` path, so `storage` already arrives resolved and `storage.resolve()` is a no-op in
    every reachable state today. It stays because this function must not silently depend on a
    property owned by a different function three calls away. That coupling was a REAL bug, not a
    hypothetical: `claude_home` used to leave the `.claude` component unresolved, which made
    `transcript_files` return nothing at all for anyone with a symlinked `~/.claude` — so the escape
    tests here passed for the wrong reason, the caller having already refused everything. Review
    found it by asking why these `.resolve()` calls could not be mutated into failure.

    TOCTOU: the returned `path` is RESOLVED, so re-opening it does not re-traverse a leaf symlink —
    the classic swap-after-check race on the file itself is closed by construction. The residual (a
    directory COMPONENT replaced between `resolve()` and `open()`) is identical to what
    `transcript_files` and `list_targets` already carry; pre-existing, not introduced here, and
    stated rather than implied away.
    """
    storage = project_storage_dir(project_dir, home=home)
    if not storage.is_dir():
        return []
    storage_r = storage.resolve()
    found: list[SubagentTranscript] = []
    for session_path in transcript_files(project_dir, home=home):
        session_id = session_path.stem
        session_dir = storage / session_id
        if not session_dir.is_dir():
            continue  # most sessions have no side storage at all
        resolved_session_dir = session_dir.resolve()
        if resolved_session_dir.parent != storage_r:
            continue  # hop 1: the session directory itself is a symlink out of the storage tree
        root = (resolved_session_dir / SUBAGENTS_DIRNAME).resolve()
        if root.parent != resolved_session_dir or not root.is_dir():
            continue  # hop 2: `subagents/` is a symlink somewhere else (incl. back into `memory/`)
        for path in sorted(root.rglob(TRANSCRIPT_GLOB)):
            if not path.name.startswith(AGENT_FILE_PREFIX):
                continue  # `journal.jsonl` and anything else that is not a transcript
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue  # a symlinked FILE inside a legitimate `subagents/` pointing outside it
            found.append(_subagent_transcript(resolved, resolved_session_dir, session_id))
    found.sort(key=lambda t: (t.session_id, *_subagent_order(t)))
    return found


def _subagent_order(transcript: SubagentTranscript) -> tuple[str, int, str]:
    """Ordering WITHIN one session: a workflow run's agents stay contiguous, then depth, then id."""
    return (
        transcript.workflow_run or "",
        transcript.spawn_depth if transcript.spawn_depth is not None else 0,
        transcript.agent_id,
    )


def _subagent_transcript(path: Path, session_dir: Path, session_id: str) -> SubagentTranscript:
    """Build one `SubagentTranscript` from a resolved transcript path + its sidecar."""
    relative = path.relative_to(session_dir)
    meta = _read_sidecar(path)
    return SubagentTranscript(
        path=path,
        session_id=session_id,
        agent_id=path.stem[len(AGENT_FILE_PREFIX):],
        # Extension dropped, matching the SDK's own `subpath` construction.
        subpath=relative.with_suffix("").as_posix(),
        workflow_run=_workflow_run(relative.parts),
        agent_type=_meta_str(meta.get("agentType")),
        spawn_depth=_meta_int(meta.get("spawnDepth")),
        description=_meta_str(meta.get("description")),
        parent_agent_id=_meta_str(meta.get("parentAgentId")),
        meta=meta,
    )


def _read_sidecar(path: Path) -> dict:
    """`<stem>.meta.json` as a dict — `{}` when it is missing, unreadable or not a JSON object.

    Never observed on the measured corpus (0 missing, 0 malformed of 874), so this whole path is
    DEFENSIVE. The live degradation is per-KEY, and it happens in `_subagent_transcript` above.
    """
    try:
        loaded = json.loads(_read_text(path.parent / (path.stem + META_SUFFIX)) or "{}")
    except ValueError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _workflow_run(relative_parts: tuple[str, ...]) -> str | None:
    """The `<run-id>` of `subagents/workflows/<run-id>/agent-<id>.jsonl`, else None.

    Derived from the PATH, which is the only evidence that is total: all 299 nested files on the
    measured corpus were `spawnDepth: 1` with NO `parentAgentId`, so a depth- or sidecar-based rule
    is confidently wrong on a third of the corpus.
    """
    directories = relative_parts[:-1]  # drop the filename
    for position, part in enumerate(directories):
        if part == WORKFLOWS_DIRNAME and position + 1 < len(directories):
            return directories[position + 1]
    return None


def _meta_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _meta_int(value: Any) -> int | None:
    return None if isinstance(value, bool) or not isinstance(value, int) else value


def parent_ref(transcript: SubagentTranscript) -> str:
    """What this subagent DESCENDS from: `workflow:<run-id>` / `agent:<id>` / `session:<id>`.

    THREE cases, path first, and the order is the correction that matters. A workflow-nested agent's
    parent is the RUN, not the session — and it carries no `parentAgentId` to say so — so a rule
    that read "depth 1 means the session owns it" is wrong on every nested file. The real precedence
    is (path-nested AND no `parentAgentId`) -> workflow; `parentAgentId` -> agent; else session, so
    a future nested file that DID carry an explicit parent agent would report the agent.

    Every branch is derived from something total (the path, or the directory name the session id
    comes from), so there is no "unknown parent" case; the fallback exists only so a caller that
    hand-built a `SubagentTranscript` with no session id still gets a string.
    """
    if transcript.workflow_run and not transcript.parent_agent_id:
        return f"workflow:{transcript.workflow_run}"
    if transcript.parent_agent_id:
        return f"agent:{transcript.parent_agent_id}"
    if transcript.session_id:
        return f"session:{transcript.session_id}"
    return "(unknown)"


def _short_parent(transcript: SubagentTranscript) -> str:
    """`parent_ref` with the ID shortened for the index line — but NEVER the workflow run id.

    A run id is 15 characters and IS the parent identity for every nested file; truncating it would
    make two runs indistinguishable. That is exactly why a nested index line is structurally ~8
    characters wider than a flat one, and why `INDEX_LINE_MAX` is what it is.
    """
    ref = parent_ref(transcript)
    kind, separator, value = ref.partition(":")
    if separator and kind in ("session", "agent"):
        return f"{kind}:{value[:SHORT_ID_CHARS]}"
    return ref


def session_header(index: int, session_id: str) -> str:
    """The 2-line header for a SESSION entry — emitted only when subagents are included.

    Keeping it off the default path is what makes main-only ingestion byte-identical to what this
    adapter has always produced; labelling every entry unconditionally would be a defensible choice
    but it should be a deliberate one, not a side effect of adding a feature.
    """
    return f"[{index}] session {session_id[:SHORT_ID_CHARS]}\nsession={session_id}"


def subagent_header(index: int, transcript: SubagentTranscript) -> str:
    """The 3-line header for a SUBAGENT entry: index line, full identity, free text.

    Line 0 is the INDEX LINE and is the whole orientation mechanism: it is what
    `[t.split("\\n")[0] for t in transcripts]` prints, and it is short enough that the scan fits one
    REPL cell's `CD_MAX_OUTPUT_CHARS` budget (see `INDEX_LINE_MAX`). The `parent=`/`type=`/`depth=`
    labels cost ~28 characters an entry and buy legibility; dropping them for an unlabelled
    positional form is the identified lever if that budget ever binds, and it needs a sentence of
    instruction to explain the positions.

    Line 1 is the FULL identity, reachable with `read_transcript_chunk(i, 0, 400)` — full session
    id, full agent id, and the SDK-shaped `subpath` that locates the file on disk. `subpath` is
    relative, so it identifies nothing about the machine.

    Line 2 is the only free text, and it is the one that really degrades: `description` was absent
    on 299 of 874 files. It is also MODEL-AUTHORED (the parent's Task-tool summary), which is
    exactly why the header lives in the transcript STRING — `redact()` covers the whole entry, so
    treating it as trusted metadata beside the text would be a redaction hole.

    `type=(unknown)` / `depth=?` are defensive: neither key was ever observed absent.
    """
    depth = "?" if transcript.spawn_depth is None else transcript.spawn_depth
    return (
        f"[{index}] subagent {transcript.agent_id[:SHORT_ID_CHARS]} "
        f"parent={_short_parent(transcript)} "
        f"type={transcript.agent_type or '(unknown)'} depth={depth}\n"
        f"session={transcript.session_id} agent={transcript.agent_id} "
        f"subpath={transcript.subpath}\n"
        f"task: {transcript.description or '(not recorded)'}"
    )


def render_transcript_events(events: Iterable[dict], *, include_sidechain: bool = False) -> str:
    """Render raw JSONL events as the `"{role}: {text}"` text this project's pipeline expects.

    DELIBERATELY LOSSY, and the choice is stated here rather than baked in silently: a real long
    conversation's raw JSONL is multi-megabyte, and re-inflating it would defeat the point of
    distilling. What survives is a transcript's SUBSTANCE — what was said and decided:

    * only `user` / `assistant` events are rendered at all. Every other event type lacks `message`
      entirely (see `RENDERED_EVENT_TYPES`), so this is a filter, not a preference.
    * `isSidechain` events are skipped unless `include_sidechain=True`. **That field is what
      separates the two transcript STORES, not noise.** On a main-thread file it filters nothing
      (measured: `False` on 0 of 57,928 user/assistant events across 883 session files, so the
      default path here is byte-identical either way). On a SUBAGENT file it filters EVERYTHING
      (72,126 of 72,126, across 874 files) — which is why reading a subagent transcript means
      turning it off EXPLICITLY rather than deleting the filter. The default stays `False` so a
      future Claude Code that inlines sidechain events into the main file cannot silently
      double-count them. `for_project(..., include_subagents=True)` is the one caller that passes
      `True`, and only for a file it discovered under `subagents/`.
    * `message.content` is EITHER a plain string (really occurs) or a list of typed blocks. A string
      is used as-is; in a list, `text`/`thinking` contribute their text verbatim, `tool_use` a short
      `[used tool: X]` label, and `tool_result` a size label whose UNIT depends on its OWN content
      being a string or a list (both really occur — never assume one shape for it). "Verbatim" is
      the RULE, not a prediction about volume: a `thinking` block's own `thinking` field is EMPTY in
      every transcript Claude Code writes (measured: 2,384 blocks across 60 session files, 0 with
      content), so that branch contributes nothing and `_render_block` returning `""` for it is
      correct. Do not "fix" it, and do not reach for the ~1,840-char `signature` — that is a crypto
      signature, not prose.
    * an unrecognized block type contributes `[unrecognized content block: X]` rather than raising
      or silently vanishing, so a future block type shows up as a visible gap instead of a lie.

    The rendering knows nothing about the filesystem, and `include_sidechain` is an explicit
    parameter rather than a path sniff for that reason: this function takes an ITERABLE of events,
    so making the filter path-aware would drag a pure function onto the filesystem seam. Nor is it
    auto-detected ("if every event is sidechain, keep them") — an input-dependent filter is the
    implicit behaviour this project documents its way out of everywhere else, and a
    partially-sidechain file (never observed, and the only case where it matters) would behave
    unpredictably.
    """
    lines: list[str] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") not in RENDERED_EVENT_TYPES:
            continue
        if not include_sidechain and event.get("isSidechain"):
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


def render_transcript_file(path: str | Path, *, include_sidechain: bool = False) -> str:
    """`render_transcript_events` over one `<session-id>.jsonl` or `agent-<id>.jsonl` file.

    A line that is blank or not valid JSON is skipped rather than fatal: a session being written
    right now legitimately has a torn last line, and one bad line must not lose the conversation.
    That is not hypothetical — a transcript store is LIVE, and the same directory has been measured
    growing between two reads minutes apart.

    `include_sidechain=True` is required to get anything at all out of a subagent file (see
    `render_transcript_events`); the default leaves the main-thread rendering unchanged.
    """
    return render_transcript_events(
        _read_jsonl(Path(path)), include_sidechain=include_sidechain
    )


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

    harness_name = "claude_code"

    @classmethod
    def for_project(
        cls,
        project_dir: str | Path,
        *,
        home: str | Path | None = None,
        include_subagents: bool = False,
    ) -> ClaudeCodeAdapter:
        """Discover the REAL storage for `project_dir` — memory, transcripts, and BOTH skill scopes.

        The alternate constructor that removes the path-assembly step: it computes `sanitize(project_dir)`,
        derives `<claude_home>/projects/<sanitized>/memory`, renders every sibling
        `<session-id>.jsonl` as one transcript, and points skill enumeration at the global
        (`<claude_home>/skills`) and project (`<project_dir>/.claude/skills`) roots.

        Nothing here has to exist: a project with no storage yet yields an adapter with no
        transcripts and an empty index, which is a normal input (everything is a promotion).
        `home=` overrides `~/.claude` — that is how the tests stay hermetic.

        **`include_subagents` is OPT-IN, and default-off is a decision rather than caution.** The
        cost/benefit reading favours default-on (the data is already on disk, the prompt cost is two
        characters because `REPLVariable`'s preview is fixed at 1000, and a project where 351 agents
        were spawned has most of its real work inside them). It stays off anyway, decisively on the
        first two grounds: (1) `transcripts` is POSITIONAL, so flipping it silently renumbers every
        entry and `read_transcript_chunk(3, ...)` names a different conversation before and after —
        a change that invalidates historical comparison should be something an operator DID; and
        (2) redaction is pattern-based and admits false negatives by construction, subagent files
        are where bulk tool output lands, and shipping ~1.5x more text (and up to 18.5x more
        ENTRIES) to a remote model is the operator's call. It also matches this project's posture
        everywhere else (`--approve` takes indices, `--confirm` is a second act) and keeps `eval/`
        baselines from moving underneath anyone.

        With it ON, the entry list is ordered session-then-that-session's-own-subagents, each entry
        carries a synthesized header (`session_header` / `subagent_header`), and `transcript_ids`
        names every entry. With it OFF the rendering is BYTE-IDENTICAL to what it has always been —
        no headers, no sidechain events — and only `transcript_ids` is new.

        Also reads the project's own `CLAUDE.md` (or `.claude/CLAUDE.md`) via
        `project_claude_md_path` — see that function's docstring for scope and the containment
        check. Absent (or refused for escaping `project_dir`) degrades to `""`, the same
        "nothing here yet is a normal input" stance every other piece of storage this method
        discovers already takes.
        """
        sessions = transcript_files(project_dir, home=home)
        if include_subagents:
            transcripts, transcript_ids = _render_with_subagents(project_dir, sessions, home=home)
        else:
            transcripts = [render_transcript_file(p) for p in sessions]
            transcript_ids = tuple(
                TranscriptId(kind="session", id=p.stem, session=p.stem, parent=f"session:{p.stem}")
                for p in sessions
            )
        claude_md_path = project_claude_md_path(project_dir)
        return cls(
            memory_dir_for_project(project_dir, home=home),
            transcripts=transcripts,
            transcript_ids=transcript_ids,
            global_skills_dir=global_skills_root(home=home),
            project_skills_dir=project_skills_root(project_dir),
            project_instructions=_read_text(claude_md_path) if claude_md_path else "",
        )

    def __init__(
        self,
        memory_dir: str | Path,
        transcripts: Iterable[str] = (),
        *,
        transcript_ids: Sequence[TranscriptId] = (),
        global_skills_dir: str | Path | None = None,
        project_skills_dir: str | Path | None = None,
        project_instructions: str = "",
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
        # ALL-OR-NOTHING, per `RawSession.transcript_ids`: `for_project` names every entry it
        # returns; the explicit constructor is handed plain strings and names none, which is why a
        # bare `ClaudeCodeAdapter(memory_dir, transcripts=[...])` reports `()` rather than a
        # partial list that would look authoritative while mapping nothing.
        self._transcript_ids: tuple[TranscriptId, ...] = tuple(transcript_ids)
        # Skill enumeration is OPT-IN on this constructor: `ClaudeCodeAdapter(memory_dir)` (which is
        # what `apply.py`'s re-scan builds) must never silently reach into a real `~/.claude/skills`.
        # `for_project` is the constructor that resolves the real roots.
        self.global_skills_dir = Path(global_skills_dir).resolve() if global_skills_dir else None
        self.project_skills_dir = Path(project_skills_dir).resolve() if project_skills_dir else None
        # Plain text, not a path: unlike memory_dir/skills, there is no re-request surface for this
        # later (no tool ever reads it by name), so there is nothing here for a containment check to
        # defend — `project_claude_md_path` already did that at DISCOVERY time, in `for_project`.
        self.project_instructions = project_instructions

    # -- HarnessAdapter -------------------------------------------------------------------

    def ingest(self) -> RawSession:
        """Snapshot this harness's transcripts + memory index into one `RawSession`.

        Called EXACTLY ONCE per run by `session.run_distillation`; the resulting `memory_index` list
        is the immutable snapshot every tool closes over from then on (no tool ever holds a live
        adapter reference, so the allowlist cannot shift mid-run).
        """
        return RawSession(
            transcripts=list(self._transcripts),
            memory_index=self.list_targets(),
            transcript_ids=self._transcript_ids,
            project_instructions=self.project_instructions,
        )

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


def _render_with_subagents(
    project_dir: str | Path, sessions: list[Path], *, home: str | Path | None
) -> tuple[list[str], tuple[TranscriptId, ...]]:
    """Render sessions AND their subagents as separate `transcripts` entries, plus their identities.

    **Separate entries, never folded into the parent's text, and never a new `RawSession` TEXT
    field.** The text field is the unsafe one: `run_distillation_artifacts` redacts exactly
    `raw.transcripts`, so any other text-carrying field sails past that line unless `session.py`
    changes in lockstep — a second place a redaction fix has to land, which is the failure mode
    invariant 11 exists to prevent — and it would also miss `read_transcript_chunk`'s closure (the
    audit record) and `DistillArtifacts.transcripts` (what `eval/`'s judge reads). Putting the text
    in `transcripts` makes redaction, auditability and judgeability STRUCTURAL.

    Folding a subagent's text into its parent's entry was rejected separately: it makes a subagent
    read indistinguishable from a parent read in the audit trail, the largest folded entry measured
    7,014,836 characters (351 sequential `read_transcript_chunk` calls at `MAX_LIMIT`, against a
    30-iteration budget), and there is no honest splice point — the renderer emits a flat string
    with no event offsets, and a workflow-nested agent's parent is not the session at all.

    Ordering is session, then that session's own subagents (`_subagent_order`), then the next
    session: deterministic, related material adjacent, a workflow run's agents contiguous.

    A subagent whose render is EMPTY is DROPPED rather than emitted as a header with no body — which
    is why the index is assigned in a second pass, after the set of surviving entries is known.
    """
    by_session: dict[str, list[SubagentTranscript]] = {}
    for transcript in subagent_files(project_dir, home=home):
        by_session.setdefault(transcript.session_id, []).append(transcript)

    entries: list[tuple[SubagentTranscript | None, str, str]] = []
    for path in sessions:
        session_id = path.stem
        entries.append((None, session_id, render_transcript_file(path)))
        for transcript in sorted(by_session.get(session_id, []), key=_subagent_order):
            body = render_transcript_file(transcript.path, include_sidechain=True)
            if not body:
                continue
            entries.append((transcript, session_id, body))

    transcripts: list[str] = []
    ids: list[TranscriptId] = []
    for index, (transcript, session_id, body) in enumerate(entries):
        if transcript is None:
            transcripts.append(f"{session_header(index, session_id)}\n{body}")
            ids.append(
                TranscriptId(
                    kind="session", id=session_id, session=session_id,
                    parent=f"session:{session_id}",
                )
            )
        else:
            transcripts.append(f"{subagent_header(index, transcript)}\n{body}")
            ids.append(
                TranscriptId(
                    kind="subagent", id=transcript.agent_id, session=session_id,
                    parent=parent_ref(transcript),
                )
            )
    return transcripts, tuple(ids)


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
