"""`apply_plan` — the human-gated, host-side action that finally writes what a plan proposes.

This module is the ONE place in `ctx_distillery` that mutates a file, and it is deliberately,
structurally OUTSIDE the RLM (`docs/DESIGN.md`, "The apply step"):

* No `RLMTask` constructs it, no tool can reach it, and `task.py` / `session.py` do not import it —
  `run_distillation` never calls it at the end of a run. A human (or a thin CLI wrapper, out of
  scope for this pass) calls `apply_plan` directly, after reading the plan themselves.
* It takes an EXPLICIT list of approved candidate indices. There is no "apply everything
  `assemble()` returned" entry point: a reviewer who approves eight of ten candidates must not have
  to fight the API to reject the other two. For an irreversible operation the safe path is the
  default one.
* `HarnessAdapter` gains no write method for this. Writing into `memory_dir` is ordinary host-side
  Python, the same reasoning `tools/memory_reader.py` already gives for reading being an ordinary
  file read rather than a fourth adapter method.

Five gaps an independent review found in the first draft of this design are fixed here, and each
one is load-bearing rather than cosmetic:

1. **A `prune` candidate names its target.** `DistillCandidate.key_fields` is a free-form dict, so
   the target lives there by CONVENTION: `key_fields["target_path"]`, which `task._INSTRUCTIONS`
   now tells the planner to set. A prune with a missing / non-matching / `kind="index"`
   `target_path` is refused, never guessed at.
2. **The collision authority is a FRESH re-scan.** Apply runs later — possibly much later — than
   the run that produced the plan, so the run's own snapshot is stale by construction and is never
   consulted here. `apply_plan` calls `ClaudeCodeAdapter(memory_dir).list_targets()` itself.
3. **Filename derivation is specified**: `slugify(frontmatter["name"]) + ".md"`, and a name that
   slugifies to nothing is a hard refusal rather than some invented fallback filename.
4. **The archive lives OUTSIDE `memory_dir`** (`<memory_dir's parent>/_ctx_distillery_archive/`), so
   neither `list_targets()`'s own `*.md` glob nor `rlm_kit.skills.discover_skills`'s
   `*/SKILL.md`-one-level-down discovery can ever re-surface an archived file as if it were live.
5. **Write-side containment mirrors the read side.** `list_targets()` only enumerates a resolved
   path whose parent is still `memory_dir` (an adversarial review reproduced a symlink escaping the
   read side); the write side requires exactly the same thing of the computed target path before any
   write is attempted.

`prune` ARCHIVES, never hard-deletes: "propose, apply cautiously, still recoverable" beats "propose,
apply irreversibly" even at the human-approved step. A separate, explicit `purge` (deleting the
archive for real) is future work.

Every candidate gets an outcome, including the ones the caller did not approve — this project's
stated value is auditability, so the step that finally mutates disk should not be the one place that
leaves no record of what happened.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import frontmatter
from .adapters.base import ArtifactRef
from .adapters.claude_code import INDEX_FILENAME, ClaudeCodeAdapter
from .session import PROMOTION_ACTIONS, AssembledCandidate, AssembledPlan

__all__ = [
    "ARCHIVE_DIRNAME",
    "ApplyOutcome",
    "apply_plan",
    "slugify",
]

#: The archive directory's name, created as a SIBLING of `memory_dir` — never inside it (gap #4).
ARCHIVE_DIRNAME = "_ctx_distillery_archive"

#: Outcome statuses. `noop` is distinct from `skipped` on purpose: "you approved a `keep` and there
#: was nothing to do" and "you never approved this" are different audit facts.
STATUS_APPLIED = "applied"
STATUS_REFUSED = "refused"
STATUS_SKIPPED = "skipped"
STATUS_NOOP = "noop"

_SLUG_SEPARATORS = re.compile(r"[\s_]+")
_SLUG_DISALLOWED = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class ApplyOutcome:
    """What actually happened to ONE candidate — the audit record `apply_plan` returns per entry."""

    index: int
    action: str
    status: str
    reason: str = ""
    #: The file created (promotion) or the archive destination (prune). None when nothing moved.
    path: str | None = None
    #: For a prune: where the archived file came FROM.
    source_path: str | None = None

    @property
    def applied(self) -> bool:
        return self.status == STATUS_APPLIED


def slugify(name: str) -> str:
    """Derive the filename stem from a drafted frontmatter `name` (gap #3).

    Lowercase, `[\\s_]+` runs to a hyphen, then everything outside `[a-z0-9-]` dropped. Nothing is
    invented: an empty result means the caller must refuse, not fall back to a made-up name — and
    the character class is what makes the result incapable of carrying a path separator or a `..`
    segment in the first place (the containment check below is still enforced, belt and braces).
    """
    lowered = (name or "").strip().lower()
    return _SLUG_DISALLOWED.sub("", _SLUG_SEPARATORS.sub("-", lowered))


def apply_plan(
    memory_dir: str | Path,
    assembled_plan: AssembledPlan,
    approved_ids: Collection[int],
    *,
    overwrite_ids: Collection[int] = (),
) -> list[ApplyOutcome]:
    """Apply the APPROVED candidates of `assembled_plan` into `memory_dir`. Returns one outcome each.

    * `approved_ids` — LIST INDICES into `assembled_plan.candidates`. Indices, not `artifact_id`,
      because `prune`/`keep` candidates carry `artifact_id=None`: the index is the one identifier
      every candidate actually has. An index that addresses no candidate raises `ValueError` BEFORE
      anything is written — a mis-typed approval must not silently apply nothing (or, worse, apply
      the wrong thing).
    * `overwrite_ids` — the per-candidate escape hatch for a name collision. It is a set of indices,
      never a global flag: an operator who decides ONE specific promotion may replace an existing
      file says so about that one candidate. Every index here must also be approved.
    * Writes nothing for a candidate the caller did not approve, and re-checks the refusal
      conditions itself rather than trusting the caller to have filtered correctly.
    """
    root = Path(memory_dir).resolve()
    candidates = list(assembled_plan.candidates)
    approved = _indices(approved_ids, len(candidates), "approved_ids")
    overwrite = _indices(overwrite_ids, len(candidates), "overwrite_ids")
    unapproved = sorted(overwrite - approved)
    if unapproved:
        raise ValueError(
            f"overwrite_ids {unapproved} are not in approved_ids — an overwrite is an escape hatch "
            f"on an approved candidate, not a way to approve one"
        )

    # Gap #2: the CURRENT state of the memory store is the sole collision/target authority. The
    # plan's own snapshot is older than this call by construction and is never consulted.
    targets = _rescan(root)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    outcomes: list[ApplyOutcome] = []
    for index, candidate in enumerate(candidates):
        if index not in approved:
            outcomes.append(
                _outcome(index, candidate, STATUS_SKIPPED, "not approved by the caller")
            )
            continue
        blocker = _blocking_problem(candidate)
        if blocker is not None:
            outcomes.append(_outcome(index, candidate, STATUS_REFUSED, blocker))
            continue
        if candidate.action in PROMOTION_ACTIONS:
            outcomes.append(
                _promote(index, candidate, root, targets, overwrite=index in overwrite)
            )
        elif candidate.action == "prune":
            outcomes.append(_prune(index, candidate, root, targets, stamp))
        elif candidate.action == "keep":
            outcomes.append(
                _outcome(index, candidate, STATUS_NOOP, "keep is a no-op — there is nothing to apply")
            )
        else:
            outcomes.append(
                _outcome(
                    index, candidate, STATUS_REFUSED, f"unknown action {candidate.action!r}"
                )
            )
    return outcomes


# -- refusal checks that apply to EVERY action kind ----------------------------------------------


def _blocking_problem(candidate: AssembledCandidate) -> str | None:
    """The design's "refused regardless of action kind" checks, re-run here on purpose.

    `assemble()` already computed all three; re-checking means a caller who approved a candidate
    without reading its `problems` still cannot write a draft that failed its own format gate.
    """
    if candidate.problems:
        detail = "; ".join(str(p) for p in candidate.problems)
        return f"the assembled candidate carries problems: {detail}"
    if candidate.draft_ok is False:
        return "the drafting call for this candidate failed its deterministic format check"
    if candidate.action in PROMOTION_ACTIONS and not (candidate.draft or "").strip():
        return "no drafted text was assembled for this promotion (nothing to write)"
    return None


# -- promote_to_memory / promote_to_skill ---------------------------------------------------------


def _promote(
    index: int,
    candidate: AssembledCandidate,
    root: Path,
    targets: dict[str, ArtifactRef],
    *,
    overwrite: bool,
) -> ApplyOutcome:
    """Write the assembled draft to a NEW file, `open(path, "x")` as the collision enforcement."""
    meta, _body = frontmatter.parse(candidate.draft or "")
    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            "the draft's frontmatter carries no usable `name`, so no filename can be derived",
        )
    slug = slugify(name)
    if not slug:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"frontmatter name {name!r} slugifies to nothing — refusing rather than inventing a "
            f"fallback filename",
        )
    filename = f"{slug}.md"
    if filename.casefold() == INDEX_FILENAME.casefold():
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"{filename!r} is the harness's memory index — the index is never a promotion target",
        )

    target = root / filename
    try:
        resolved = target.resolve()
    except OSError as exc:  # pragma: no cover — a path that cannot be resolved cannot be written
        return _outcome(index, candidate, STATUS_REFUSED, f"could not resolve {target}: {exc}")
    # Gap #5: the write-side mirror of `list_targets()`'s read-side containment check. Identical
    # test (`resolved.parent == memory_dir`), and for the identical reason — a symlink sitting in
    # `memory_dir` resolves OUTSIDE it, and a write must no more follow that escape than a read.
    if resolved.parent != root:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"refusing to write outside {root}: {filename!r} resolves to {resolved}",
        )
    existing = targets.get(str(resolved))
    if existing is None:
        existing = _index_ref_matching(targets, resolved)
    if existing is not None and existing.kind == "index":
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"{filename!r} resolves onto the harness's memory index ({existing.path}) — never a "
            f"promotion target",
        )
    if existing is not None and not overwrite:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"name collision: {existing.path} already exists (re-scanned at apply time). Rename "
            f"the draft, or approve this ONE candidate for overwrite explicitly.",
        )

    try:
        # A memory store that does not exist yet is a normal input (everything in it is a
        # promotion), so create it — but in its OWN try, or its FileExistsError (a FILE sitting
        # where the store should be) would be reported as the write's name collision.
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _outcome(
            index, candidate, STATUS_REFUSED, f"could not open the memory store {root}: {exc}"
        )
    try:
        # "x" == O_CREAT|O_EXCL: atomic, TOCTOU-proof, and the ACTUAL enforcement — the re-scan
        # above is an early, friendly message, not the guarantee. `overwrite` is the per-candidate
        # escape hatch, and the only thing that ever downgrades this to a plain truncating write.
        with open(resolved, "w" if overwrite else "x", encoding="utf-8") as handle:
            handle.write(candidate.draft or "")
    except FileExistsError:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"name collision: {resolved} was created between the re-scan and the write "
            f"(exclusive-create refused it). Rename the draft, or approve this ONE candidate for "
            f"overwrite explicitly.",
        )
    except OSError as exc:
        return _outcome(index, candidate, STATUS_REFUSED, f"could not write {resolved}: {exc}")
    return _outcome(
        index,
        candidate,
        STATUS_APPLIED,
        f"wrote {'(overwriting) ' if overwrite else ''}{len(candidate.draft or '')} chars",
        path=str(resolved),
    )


# -- prune ----------------------------------------------------------------------------------------


def _prune(
    index: int,
    candidate: AssembledCandidate,
    root: Path,
    targets: dict[str, ArtifactRef],
    stamp: str,
) -> ApplyOutcome:
    """Archive (never delete) the artifact `key_fields["target_path"]` names — gaps #1 and #4."""
    raw = (candidate.key_fields or {}).get("target_path")
    if not isinstance(raw, str) or not raw.strip():
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            "a prune candidate must set key_fields['target_path'] to the path of an existing "
            "artifact; this one names no target, and a prune target is never guessed at",
        )
    try:
        resolved = str(Path(raw).resolve())
    except OSError:  # pragma: no cover — an unresolvable path cannot match the re-scan
        resolved = ""
    ref = targets.get(resolved)
    if ref is None:
        # Same allowlist discipline as `read_memory_file`: an EXACT resolved-path match against the
        # authoritative set, never a prefix or substring test.
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"target_path {raw!r} does not exactly match any artifact in the memory index "
            f"re-scanned at apply time — refusing rather than guessing what it meant",
        )
    if ref.kind == "index":
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"target_path {raw!r} is the harness's memory index — never a prune target",
        )

    if root.parent == root:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"{root} has no parent directory to archive beside, and the archive must never live "
            f"inside the memory store",
        )
    archive_dir = root.parent / ARCHIVE_DIRNAME
    source = Path(resolved)
    destination = _archive_destination(archive_dir, source.name, stamp)
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        # A MOVE, not a delete. Destination is a sibling of `memory_dir`, so this is a same-device
        # rename in every realistic layout; any OSError refuses this ONE candidate, loudly.
        source.rename(destination)
    except OSError as exc:
        return _outcome(
            index, candidate, STATUS_REFUSED, f"could not archive {source} -> {destination}: {exc}"
        )
    return _outcome(
        index,
        candidate,
        STATUS_APPLIED,
        f"archived {ref.name!r} out of the memory store (recoverable — not deleted)",
        path=str(destination),
        source_path=str(source),
    )


def _archive_destination(archive_dir: Path, source_name: str, stamp: str) -> Path:
    """`<timestamp>-<name>`, disambiguated if that exact name is already archived.

    Archiving must never clobber an earlier archive — that would turn "still recoverable" into a
    silent delete, which is the whole point of not deleting.
    """
    destination = archive_dir / f"{stamp}-{source_name}"
    attempt = 2
    while destination.exists() or destination.is_symlink():
        destination = archive_dir / f"{stamp}-{attempt}-{source_name}"
        attempt += 1
    return destination


# -- plumbing -------------------------------------------------------------------------------------


def _rescan(root: Path) -> dict[str, ArtifactRef]:
    """`{resolved_path: ArtifactRef}` from a FRESH `list_targets()` — the sole authority (gap #2)."""
    refs = ClaudeCodeAdapter(root).list_targets()
    return {str(Path(ref.path).resolve()): ref for ref in refs}


def _index_ref_matching(targets: dict[str, ArtifactRef], resolved: Path) -> ArtifactRef | None:
    """The index ref `resolved` would land on, comparing case-INSENSITIVELY.

    `Path.resolve()` does not case-normalize, but macOS/Windows filesystems do: a draft named
    "memory" derives `memory.md`, which IS `MEMORY.md` on such a filesystem while comparing unequal
    as a string. Only used to catch the index — a genuine memory-file collision is caught by the
    exclusive create regardless of case.
    """
    wanted = str(resolved).casefold()
    for path, ref in targets.items():
        if ref.kind == "index" and path.casefold() == wanted:
            return ref
    return None


def _indices(values: Iterable[int], count: int, label: str) -> set[int]:
    """Validate caller-supplied candidate indices BEFORE any mutation happens."""
    indices: set[int] = set()
    bad: list[object] = []
    for value in values:
        # `bool` first: `True` is an int and would otherwise silently mean index 1. The range test
        # runs only after the type test, so a non-int never reaches the comparison.
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < count:
            bad.append(value)
        else:
            indices.add(value)
    if bad:
        raise ValueError(
            f"{label} must be candidate list indices in range(0, {count}); got invalid {bad!r}"
        )
    return indices


def _outcome(
    index: int,
    candidate: AssembledCandidate,
    status: str,
    reason: str,
    *,
    path: str | None = None,
    source_path: str | None = None,
) -> ApplyOutcome:
    return ApplyOutcome(
        index=index,
        action=candidate.action,
        status=status,
        reason=reason,
        path=path,
        source_path=source_path,
    )
