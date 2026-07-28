"""`apply_plan` — the human-gated, host-side action that finally writes what a plan proposes.

This module is the ONE place in `ctx_distillery` that mutates a file, and it is deliberately,
structurally OUTSIDE the RLM (`CLAUDE.md` invariant (8), "`apply.py` is the ONE writer"):

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

A SIXTH gap, found by the later primary-source research into Claude Code's real storage
(`CLAUDE.md` invariant (9), the per-kind roots), needed an architecture fix rather than another
path string: a skill does NOT live as a flat `<slug>.md` under `memory_dir` at
all. It is `<skills_root>/<slug>/SKILL.md` — one directory level deeper, under a COMPLETELY
different root (`~/.claude/skills` for a global skill, `<project_dir>/.claude/skills` for a
project-scoped one). The flat containment check above (`resolved.parent == root`) would have REFUSED
every legitimate skill write. So:

* `apply_plan` takes root paths PER KIND: `memory_dir` positionally as before, plus
  `global_skills_dir=` / `project_skills_dir=`. Derive those with
  `adapters.claude_code.global_skills_root()` / `project_skills_root(project_dir)`, the same
  functions `ClaudeCodeAdapter.for_project` uses — one derivation, two consumers.
* a `promote_to_skill` candidate declares WHICH root via `key_fields["scope"]` ("global" /
  "project"), the same documented-convention pattern `prune`'s `target_path` uses. A missing or
  bogus scope is refused, never defaulted — the two roots are different places on disk.
* the skill target's containment check is its OWN function (`_skill_target`), a DIFFERENT check
  rather than a relaxed version of the flat one: the slug must carry no path separator, must resolve
  to exactly `<skills_root>/<slug>/SKILL.md`, and `<skills_root>/<slug>` must not already exist as
  something else.

`prune` ARCHIVES, never hard-deletes: "propose, apply cautiously, still recoverable" beats "propose,
apply irreversibly" even at the human-approved step. A separate, explicit `purge` (deleting the
archive for real) is future work.

Every candidate gets an outcome, including the ones the caller did not approve — this project's
stated value is auditability, so the step that finally mutates disk should not be the one place that
leaves no record of what happened.

**This module also hosts its own CLI (`ctx-distillery-apply`, `main()` at the bottom), and that
placement is forced rather than chosen.** `tests/test_no_write_capability.py::test_apply_is_unreachable_from_the_planner_path`
asserts that no module under `ctx_distillery/` imports this one — it is the guard that makes this
module's exemption from the mutation scan safe (`CLAUDE.md` invariant 8), and it matches a
function-local import as readily as a top-level one. So a single CLI module offering both `distill`
and `apply` cannot exist without turning that test red, and `apply.py` is explicitly "not a
precedent for a second exemption". Putting the writer's entry point in the writer keeps both
properties: `ctx_distillery/cli.py` never imports this module, and applying a plan is a visibly
different command at the shell — which is the same thing the API says by refusing to offer an
"apply everything" call.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, frontmatter
from .adapters.base import ARTIFACT_SCOPES, ArtifactRef
from .adapters.claude_code import (
    INDEX_FILENAME,
    SKILL_FILENAME,
    ClaudeCodeAdapter,
    global_skills_root,
    memory_dir_for_project,
    project_skills_root,
)
from .render import render_plan
from .rubric import plan_from_events
from .session import PROMOTION_ACTIONS, AssembledCandidate, AssembledPlan, assemble
from .trace_io import load_trace

__all__ = [
    "ARCHIVE_DIRNAME",
    "ApplyOutcome",
    "apply_plan",
    "build_parser",
    "main",
    "slugify",
]

#: The action whose target is a NESTED `<slug>/SKILL.md` under a skills root, not a flat file.
SKILL_ACTION = "promote_to_skill"

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
    global_skills_dir: str | Path | None = None,
    project_skills_dir: str | Path | None = None,
) -> list[ApplyOutcome]:
    """Apply the APPROVED candidates of `assembled_plan`. Returns one outcome per candidate.

    * `approved_ids` — LIST INDICES into `assembled_plan.candidates`. Indices, not `artifact_id`,
      because `prune`/`keep` candidates carry `artifact_id=None`: the index is the one identifier
      every candidate actually has. An index that addresses no candidate raises `ValueError` BEFORE
      anything is written — a mis-typed approval must not silently apply nothing (or, worse, apply
      the wrong thing).
    * `overwrite_ids` — the per-candidate escape hatch for a name collision. It is a set of indices,
      never a global flag: an operator who decides ONE specific promotion may replace an existing
      file says so about that one candidate. Every index here must also be approved.
    * ROOTS ARE PER KIND. `memory_dir` takes `promote_to_memory` writes and `prune` archives, exactly
      as before. A `promote_to_skill` goes to `global_skills_dir` or `project_skills_dir` depending
      on its own `key_fields["scope"]` — a skill is `<skills_root>/<slug>/SKILL.md`, never a flat
      file in the memory store. Derive those roots with
      `adapters.claude_code.global_skills_root()` / `project_skills_root(project_dir)`. Leaving one
      unset REFUSES the skill promotions that would need it (with a message saying so) rather than
      inventing a location — the caller decides where a skill may be installed, not this module.
    * Writes nothing for a candidate the caller did not approve, and re-checks the refusal
      conditions itself rather than trusting the caller to have filtered correctly.
    """
    root = Path(memory_dir).resolve()
    skills_roots: dict[str, Path | None] = {
        "global": Path(global_skills_dir).resolve() if global_skills_dir else None,
        "project": Path(project_skills_dir).resolve() if project_skills_dir else None,
    }
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
    # plan's own snapshot is older than this call by construction and is never consulted. Skills
    # are included too (per skills_roots), so a global-name-shadows-project check below has
    # something fresher than the plan's own stale snapshot to consult.
    targets = _rescan(root, skills_roots)
    # A MUTABLE seed of already-taken global skill names, re-checked (and grown) as this same call
    # applies candidates: a plan approving BOTH a `global` and a `project` promotion of the same
    # name must have the second one see the first's write, not just what existed before this call.
    global_skill_names = {
        ref.name.strip().lower()
        for ref in targets.values()
        if ref.kind == "skill" and ref.scope == "global"
    }
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
        if candidate.action == SKILL_ACTION:
            # A DIFFERENT write path, not a variant of the flat one — nested target, other root.
            outcomes.append(
                _promote_skill(
                    index,
                    candidate,
                    skills_roots,
                    global_skill_names,
                    overwrite=index in overwrite,
                )
            )
        elif candidate.action in PROMOTION_ACTIONS:
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


# -- promote_to_skill: a NESTED target under a DIFFERENT root -------------------------------------


def _promote_skill(
    index: int,
    candidate: AssembledCandidate,
    skills_roots: dict[str, Path | None],
    global_skill_names: set[str],
    *,
    overwrite: bool,
) -> ApplyOutcome:
    """Write the assembled draft to `<skills_root>/<slug>/SKILL.md` — gap #6.

    Separate from `_promote` on purpose. A skill's real on-disk shape is a DIRECTORY per skill under
    a root that is never `memory_dir`, so sharing the flat-file function would mean either bending
    its containment check (the thing that makes the memory write safe) or carrying two meanings in
    one code path. The scope→root selection is refused, never defaulted: guessing between a
    user-global install and a project-local one is not a guess this module is entitled to make.

    `global_skill_names` is the write-time mirror of `make_skill_validator`'s draft-time "shadowed"
    check (an adversarial review found the draft-time check alone left a real gap: `apply_plan`'s own
    fresh re-scan is supposed to be the sole collision authority per gap #2, and a plan can be applied
    long after — or even built without going through `draft_skill_file` at all — so a stale or
    hand-built `project` candidate whose name a `global` skill has since taken must still be refused
    here, not just at draft time). It is refused HARD, with no `overwrite` bypass: `overwrite` means
    "replace the file this candidate targets," never "install a skill this project can never actually
    reach." The caller MUTATES this set after a successful `global` write (below) so a plan approving
    both a `global` and a `project` promotion of the same name in ONE call still catches it, even
    though the pre-call re-scan could not have known about the `global` write yet.
    """
    scope = (candidate.key_fields or {}).get("scope")
    if not isinstance(scope, str) or scope not in ARTIFACT_SCOPES:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"a {SKILL_ACTION} candidate must set key_fields['scope'] to one of "
            f"{list(ARTIFACT_SCOPES)}; got {scope!r}. The two scopes are different directories on "
            f"disk, so this is never guessed at",
        )
    root = skills_roots.get(scope)
    if root is None:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"no {scope} skills root was given to apply_plan, so there is nowhere to install this "
            f"skill — pass {scope}_skills_dir= (see adapters.claude_code.global_skills_root / "
            f"project_skills_root) if you intend to allow {scope} skill writes",
        )

    meta, _body = frontmatter.parse(candidate.draft or "")
    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            "the draft's frontmatter carries no usable `name`, so no skill directory can be derived",
        )
    slug = slugify(name)
    if not slug:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"frontmatter name {name!r} slugifies to nothing — refusing rather than inventing a "
            f"fallback skill directory name",
        )
    if scope == "project" and name.strip().lower() in global_skill_names:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"frontmatter name {name!r} matches an existing GLOBAL skill of the same name — Claude "
            f"Code's personal/global skills take precedence over project ones, so writing this would "
            f"install a project skill that is never actually reachable (silently shadowed). Refused "
            f"regardless of `overwrite`: rename the draft, or apply it with scope='global' instead if "
            f"the intent is to replace the existing skill everywhere.",
        )

    target, refusal = _skill_target(root, slug, overwrite=overwrite)
    if target is None:
        return _outcome(index, candidate, STATUS_REFUSED, refusal)

    # Empirically confirmed: an EXISTING skills root picks up a new skill mid-session, but a
    # project's very FIRST top-level skills directory being created needs a Claude Code restart to
    # be discovered at all. Checked before mkdir, since mkdir is what would make `root` exist.
    is_new_root = not root.exists()
    try:
        # The skill DIRECTORY is part of the artifact here, unlike the flat memory path — but its own
        # try, for the same reason `_promote` isolates the store's mkdir: a FileExistsError from a
        # file sitting where the directory belongs must not be reported as the write's collision.
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _outcome(
            index, candidate, STATUS_REFUSED, f"could not create the skill directory {target.parent}: {exc}"
        )
    try:
        with open(target, "w" if overwrite else "x", encoding="utf-8") as handle:
            handle.write(candidate.draft or "")
    except FileExistsError:
        return _outcome(
            index,
            candidate,
            STATUS_REFUSED,
            f"name collision: {target} was created between the check and the write "
            f"(exclusive-create refused it). Rename the draft, or approve this ONE candidate for "
            f"overwrite explicitly.",
        )
    except OSError as exc:
        return _outcome(index, candidate, STATUS_REFUSED, f"could not write {target}: {exc}")
    if scope == "global":
        # Grows the shadow set live: a LATER candidate in this same apply_plan call that promotes a
        # `project` skill under this same name must see this write, not just what the pre-call
        # re-scan already knew about.
        global_skill_names.add(name.strip().lower())
    restart_note = (
        f" — this is the FIRST skill in {root}, so Claude Code needs a restart before it is "
        f"discovered (an existing skills root picks up a new skill without one)"
        if is_new_root
        else ""
    )
    return _outcome(
        index,
        candidate,
        STATUS_APPLIED,
        f"wrote {'(overwriting) ' if overwrite else ''}{len(candidate.draft or '')} chars to a "
        f"{scope} skill{restart_note}",
        path=str(target),
    )


def _skill_target(root: Path, slug: str, *, overwrite: bool) -> tuple[Path | None, str]:
    """`(<root>/<slug>/SKILL.md, "")`, or `(None, reason)` — the SKILL containment check.

    A DIFFERENT check from the flat-file one (`CLAUDE.md` invariant (9)), and in this order
    deliberately (an escape attempt is diagnosed before a mere collision):

    1. `slug` is non-blank, carries no path separator, and is not a `.`/`..` traversal segment.
       `slugify` already makes all of that impossible by character class (and `_promote_skill`
       refuses an empty slug before ever getting here); checked anyway, because this function's whole
       job is to be the check — a future caller deriving the slug differently must still hit a wall
       here. A blank slug would otherwise name the ROOT itself (`root / ""`), and a whitespace-only
       one a directory nobody can address.
    2. `<root>/<slug>` resolves to a DIRECT child of `root`. This is what catches an existing
       symlinked skill directory pointing outside the store, the nested analogue of the flat check.
    3. the file itself resolves to `<that directory>/SKILL.md` — so a symlinked `SKILL.md` inside an
       otherwise-legitimate skill directory cannot redirect the write either.
    4. `<root>/<slug>` does not already exist as something else. A NON-directory there is refused
       outright (`overwrite` means "replace this draft's file", never "replace a file with a
       directory"); an existing skill directory is a collision the caller may explicitly overwrite.
    """
    if not slug.strip():
        return None, (
            f"skill slug {slug!r} is blank — a blank slug names the skills root itself, not a skill "
            f"directory inside it"
        )
    if slug in (".", "..") or any(sep and sep in slug for sep in (os.sep, os.altsep, "/", "\\")):
        return None, (
            f"skill slug {slug!r} contains a path separator or traversal segment — a skill "
            f"directory name must be a single path component"
        )
    skill_dir = root / slug
    try:
        resolved_dir = skill_dir.resolve()
        resolved_root = root.resolve()
    # ValueError as well as OSError: an embedded NUL makes `Path.resolve()` raise ValueError, and a
    # refusal is the right answer to an unusable slug — never an exception escaping `apply_plan`
    # halfway through a run of candidates.
    except (OSError, ValueError) as exc:
        return None, f"could not resolve {skill_dir}: {exc}"
    if resolved_dir.parent != resolved_root or resolved_dir.name != slug:
        return None, (
            f"refusing to write outside {resolved_root}: skill directory {slug!r} resolves to "
            f"{resolved_dir}"
        )
    target = skill_dir / SKILL_FILENAME
    try:
        resolved = target.resolve()
    except (OSError, ValueError) as exc:  # pragma: no cover — as above
        return None, f"could not resolve {target}: {exc}"
    if resolved.parent != resolved_dir or resolved.name != SKILL_FILENAME:
        return None, (
            f"refusing to write outside {resolved_dir}: {SKILL_FILENAME} there resolves to {resolved}"
        )
    if skill_dir.is_symlink() or (skill_dir.exists() and not skill_dir.is_dir()):
        return None, (
            f"{skill_dir} already exists and is not a skill directory — refusing to replace it "
            f"(overwrite only ever replaces a drafted {SKILL_FILENAME}, never a different kind of "
            f"file)"
        )
    if skill_dir.is_dir() and not overwrite:
        return None, (
            f"name collision: the skill directory {skill_dir} already exists. Rename the draft, or "
            f"approve this ONE candidate for overwrite explicitly."
        )
    return resolved, ""


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


def _rescan(root: Path, skills_roots: dict[str, Path | None]) -> dict[str, ArtifactRef]:
    """`{resolved_path: ArtifactRef}` from a FRESH `list_targets()` — the sole authority (gap #2).

    Skills roots are passed through so the re-scan also carries skill refs (both scopes, whichever
    are set) — needed for the cross-scope shadow check, not just the flat memory/index collision
    check this originally existed for.
    """
    refs = ClaudeCodeAdapter(
        root,
        global_skills_dir=skills_roots.get("global"),
        project_skills_dir=skills_roots.get("project"),
    ).list_targets()
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


# -- the CLI: `ctx-distillery-apply` --------------------------------------------------------------
#
# See the module docstring for WHY the entry point lives here rather than in `cli.py`. What follows
# is the shell-level expression of the same stance `apply_plan` takes in Python: approval is
# explicit and per-candidate, there is no "apply everything", and the default does nothing.

_CLI_DESCRIPTION = """\
Apply the candidates YOU approve from a finished distillation plan. The only thing that writes.

    ctx-distillery show traces/<run-id>.jsonl                       # read the plan first
    ctx-distillery-apply traces/<run-id>.jsonl --project . --approve 0,3
    ctx-distillery-apply traces/<run-id>.jsonl --project . --approve 0,3 --confirm

WITHOUT --confirm nothing is written: the default is a dry run that shows the plan, names what you
approved, and stops. Approval is per candidate, by the list index `ctx-distillery show` prints in
front of each one; there is deliberately no flag that approves everything. A prune is ARCHIVED to a
sibling `_ctx_distillery_archive/` directory, never deleted.
"""

#: Which skills root a `promote_to_skill` may install into unless `--allow-skill-scope` says more.
#: `apply_plan` refuses a scope whose root the caller did not pass, and the CLI is that caller — so
#: this is a real decision, not a default that costs nothing. `project` is allowed because its blast
#: radius is the repository the operator just named; `~/.claude/skills` reaches every project they
#: will ever open (and a global skill SHADOWS a project one of the same name), so it earns its own
#: opt-in even though `--confirm` is already a second deliberate act.
DEFAULT_SKILL_SCOPES: tuple[str, ...] = ("project",)

#: One glyph per outcome status, matching the sibling projects' run reporting.
_STATUS_GLYPH = {
    STATUS_APPLIED: "✔",
    STATUS_REFUSED: "✗",
    STATUS_SKIPPED: "·",
    STATUS_NOOP: "-",
}


def _parse_indices(values: Iterable[str] | None, flag: str) -> set[int]:
    """Flatten repeated and/or comma-separated index arguments (`--approve 0,3 --approve 7`).

    Only the SHAPE is checked here (is it an integer?); whether the index addresses a real candidate
    is `_indices`' job, against the plan that was actually loaded.
    """
    found: set[int] = set()
    for chunk in values or ():
        for token in str(chunk).replace(",", " ").split():
            try:
                found.add(int(token))
            except ValueError:
                raise SystemExit(f"{flag}: {token!r} is not a candidate index") from None
    return found


def _format_outcome(outcome: ApplyOutcome) -> str:
    glyph = _STATUS_GLYPH.get(outcome.status, "?")
    lines = [f"{glyph} [{outcome.index}] {outcome.action:<18} {outcome.status:<8} {outcome.reason}"]
    if outcome.source_path:
        lines.append(f"      from {outcome.source_path}")
    if outcome.path:
        lines.append(f"      -> {outcome.path}")
    return "\n".join(lines)


def _roots_for(project: Path, scopes: Collection[str], home: str | None) -> dict[str, Path | None]:
    """The per-kind roots `apply_plan` takes, derived by the SAME functions `for_project` uses.

    A scope the operator did not allow is passed as None on purpose rather than omitted from the
    report: `apply_plan` then refuses that candidate with a message naming the missing root, which
    is a better outcome than a skill quietly landing somewhere they did not ask for.
    """
    return {
        "memory": memory_dir_for_project(project, home=home),
        "global": global_skills_root(home=home) if "global" in scopes else None,
        "project": project_skills_root(project) if "project" in scopes else None,
    }


def _dry_run_report(
    plan: AssembledPlan, approved: set[int], overwrite: set[int], roots: dict[str, Path | None]
) -> str:
    """What `--confirm` WOULD do, without doing any of it.

    Deliberately prints the WHOLE plan (via the shared `render_plan`, the same rendering
    `ctx-distillery show` and the eval judge read) rather than only the approved slice: a reviewer
    about to write to their own memory store should see what they are NOT approving too, in the same
    breath. The exact target path is not predicted here — deriving a filename a second time, outside
    `_promote`/`_skill_target`, is precisely the kind of duplicate that drifts.
    """
    lines = [
        render_plan(plan),
        "",
        "DRY RUN - nothing has been written.",
        f"  approved:  {sorted(approved) or 'nothing'}",
    ]
    if overwrite:
        lines.append(f"  overwrite: {sorted(overwrite)}  (these may replace an existing file)")
    lines.append(f"  memory store:      {roots['memory']}")
    for scope in ARTIFACT_SCOPES:
        root = roots.get(scope)
        lines.append(
            f"  {scope + ' skills:':<18} {root}"
            if root is not None
            else f"  {scope + ' skills:':<18} not allowed (pass --allow-skill-scope {scope})"
        )
    lines.append("")
    lines.append("Re-run the same command with --confirm to apply the approved candidates.")
    return "\n".join(lines)


def _cmd_apply(args: argparse.Namespace) -> int:
    project = Path(args.project_dir).expanduser().resolve()
    if not project.is_dir():
        print(f"no such project directory: {project}", file=sys.stderr)
        return 1
    try:
        events = load_trace(args.trace, run_id=args.run_id)
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.trace}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    plan = assemble(events, plan_from_events(events))
    if plan.problems:
        print("this trace carries no usable plan: " + "; ".join(plan.problems), file=sys.stderr)
        return 1
    if not plan.candidates:
        print("this run's plan proposed no candidates - there is nothing to apply.", file=sys.stderr)
        return 1

    approved = _parse_indices(args.approve, "--approve")
    overwrite = _parse_indices(args.overwrite, "--overwrite")
    if not approved:
        print("--approve named no candidates; nothing to do.", file=sys.stderr)
        return 2
    try:
        # Validate BOTH here, so a typo is caught in the dry run rather than only once --confirm is
        # added. `apply_plan` re-validates regardless — it never trusts a caller to have filtered.
        _indices(approved, len(plan.candidates), "--approve")
        _indices(overwrite, len(plan.candidates), "--overwrite")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    unapproved = sorted(overwrite - approved)
    if unapproved:
        print(
            f"--overwrite {unapproved} are not in --approve; an overwrite is an escape hatch on an "
            f"approved candidate, never a way to approve one",
            file=sys.stderr,
        )
        return 2

    roots = _roots_for(project, set(args.allow_skill_scope or DEFAULT_SKILL_SCOPES), args.claude_home)
    if not args.confirm:
        print(_dry_run_report(plan, approved, overwrite, roots))
        return 0

    outcomes = apply_plan(
        roots["memory"],
        plan,
        approved,
        overwrite_ids=overwrite,
        global_skills_dir=roots["global"],
        project_skills_dir=roots["project"],
    )
    for outcome in outcomes:
        print(_format_outcome(outcome))
    applied = [o for o in outcomes if o.status == STATUS_APPLIED]
    refused = [o for o in outcomes if o.index in approved and o.status == STATUS_REFUSED]
    print(f"\n{len(applied)} applied, {len(refused)} refused, of {len(approved)} approved.")
    if any(o.action == "prune" for o in applied):
        print("pruned files were ARCHIVED, not deleted - they are still recoverable.")
    return 1 if refused else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ctx-distillery-apply",
        description=_CLI_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("trace", help="path to the trace JSONL file of the run whose plan you reviewed")
    p.add_argument("--run-id", default=None, help="only read events for this run id")
    p.add_argument(
        "--project",
        dest="project_dir",
        required=True,
        help="the project whose store is written; derives the memory store and the skills roots",
    )
    p.add_argument(
        "--approve",
        action="append",
        required=True,
        metavar="IDX[,IDX...]",
        help="candidate INDICES you approve, as printed by `ctx-distillery show`. Repeatable. "
             "There is deliberately no flag that approves the whole plan.",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="actually write. Without it this is a dry run and nothing is written.",
    )
    p.add_argument(
        "--overwrite",
        action="append",
        default=None,
        metavar="IDX[,IDX...]",
        help="per-candidate escape hatch for a name collision; every index must also be approved",
    )
    p.add_argument(
        "--allow-skill-scope",
        action="append",
        choices=ARTIFACT_SCOPES,
        default=None,
        help="which skills root a promote_to_skill may install into (repeatable). Default: "
             "project only - a project skill's blast radius is the repository you just named, "
             "while ~/.claude/skills reaches every project you ever open (and a global skill of "
             "the same name shadows a project one), so global earns its own opt-in.",
    )
    p.add_argument(
        "--claude-home",
        default=None,
        help="override ~/.claude (a non-default install; also what keeps the tests hermetic)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    return _cmd_apply(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
