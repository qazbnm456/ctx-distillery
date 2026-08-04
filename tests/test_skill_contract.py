"""Pin the contract the SHIPPED skills under `skills/` have to keep.

`skills/ctx-distillery-plan/` is this repo's own Claude Code Skill, published two ways: indexed by
skills.sh via `npx skills add qazbnm456/ctx-distillery`, and installable natively through
`.claude-plugin/marketplace.json`. Both routes hand a stranger a file that tells an agent how to
drive this project's CLI, so the file is a distributed artifact with the same drift problem
`tests/test_doc_claims.py` exists for — except worse, because an installed copy is detached from
this repo and pinned to nothing.

Three properties are worth a test, and the shape of each was decided by an adversarial review of
the plan that produced these files:

* **No skill pre-approves a tool.** `allowed-tools` grants run without a permission prompt for the
  turn that invoked the skill. Written as an ALLOWLIST (the key must be absent), never a blocklist
  naming the spellings someone thought of — the review's first draft checked "does any
  `allowed-tools` mention `ctx-distillery-apply`", which `Bash(uv run *)`, `Bash(*)` and a bare
  `Bash` all walk straight through. `test_the_preapproval_check_would_catch_*` guards the guard,
  the same way `test_no_write_capability.py` pairs its scan with a synthetic writer.
* **Every command the skill prints really parses.** Not "every `--flag` token exists somewhere" —
  that check cannot see subcommand scoping (`--json` is on `distill` and `show` but not `export`),
  cannot see `required=True` (`--project`), and cannot see `choices`. Feeding the line to the REAL
  `build_parser().parse_args()` sees all three at once.
* **The frontmatter is what BOTH consumers need.** `vercel-labs/skills` (the CLI behind
  `npx skills add`) SKIPS a skill whose frontmatter lacks `name` or `description`, and it derives
  the INSTALL DIRECTORY from `name`, falling back to the directory basename. So `name` must equal
  the directory name or the published command silently changes for everyone.

Importing `ctx_distillery.apply` here is deliberate and safe: the invariant-8 reachability scan
covers `ctx_distillery/**.py` only, and `tests/test_doc_claims.py` already imports it.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from ctx_distillery import apply as apply_module
from ctx_distillery import cli as cli_module
from ctx_distillery.frontmatter import parse as parse_frontmatter
from ctx_distillery.tools.drafting import make_skill_validator

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"

#: The console-script names a fenced line may start with, mapped to the parser that owns it. Both
#: `build_parser`s are public.
_PARSERS = {
    "ctx-distillery": cli_module.build_parser,
    "ctx-distillery-apply": apply_module.build_parser,
}


def _skill_dirs() -> list[Path]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").is_file())


SKILL_DIRS = _skill_dirs()

#: Every markdown file shipped inside a skill — `SKILL.md` and its supporting files alike. A wrong
#: command in `reference.md` misleads exactly as much as one in `SKILL.md`.
SKILL_DOCS = sorted(p for d in SKILL_DIRS for p in d.rglob("*.md"))


def test_at_least_one_skill_is_shipped() -> None:
    """Guards every parametrized test below from passing vacuously on an empty collection."""
    assert SKILL_DIRS, f"no `skills/<name>/SKILL.md` found under {SKILLS_DIR}"


# --------------------------------------------------------------------------------------------------
# Frontmatter: what the two publication routes each require.
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_frontmatter_carries_name_and_description(skill_dir: Path) -> None:
    """Both are REQUIRED by the `npx skills add` CLI — it warns and SKIPS the skill otherwise, so a
    missing field is not a degraded install but no install at all. (Claude Code itself treats both
    as optional; this is the stricter of the two consumers, which is the one that binds.)"""
    front, _ = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    for field in ("name", "description"):
        assert isinstance(front.get(field), str) and front[field].strip(), (
            f"{skill_dir.name}/SKILL.md frontmatter needs a non-empty string `{field}`. The "
            f"`npx skills add` CLI skips a skill missing either one."
        )


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_frontmatter_name_equals_the_directory_name(skill_dir: Path) -> None:
    """The install directory is `sanitizeName(frontmatter.name || basename(dir))`, so `name` — not
    the directory — is what a stranger ends up typing. Keeping them equal is what stops a rename of
    one from silently moving the published command.

    This also forecloses a tempting trick: setting `name: plan` would give the prettier plugin path
    `/ctx-distillery:plan`, and would ALSO install as `~/.agents/skills/plan`, putting a bare
    `/plan` in every project the installer ever opens. Verified in `vercel-labs/skills`
    `src/installer.ts`; do not reintroduce it.
    """
    front, _ = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    assert front.get("name") == skill_dir.name, (
        f"{skill_dir.name}/SKILL.md declares `name: {front.get('name')!r}`, so it would install "
        f"as `{front.get('name')}` rather than `{skill_dir.name}`."
    )


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_shipped_skills_pass_this_projects_own_validator(skill_dir: Path) -> None:
    """Dogfood: the format check this project applies to a skill the PLANNER drafts must also pass
    on the skills this project itself ships. Cheap, and it ties the two together so a change to the
    validator's required set surfaces here too (`CLAUDE.md` invariant 7)."""
    check = make_skill_validator([])((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    assert check.ok, f"{skill_dir.name}/SKILL.md fails make_skill_validator: {check.errors}"


# --------------------------------------------------------------------------------------------------
# Tool pre-approval: an ALLOWLIST, plus a guard on the guard.
# --------------------------------------------------------------------------------------------------

#: Frontmatter keys that hand an agent a tool without a permission prompt. Both are checked because
#: `disallowed-tools` shipping alone would read as "we thought about tool scoping" while granting
#: nothing — harmless, but its presence means someone edited this area and should re-read the note.
_PREAPPROVAL_KEYS = ("allowed-tools", "allowed_tools")


def _preapproval_keys(front: dict) -> list[str]:
    return [key for key in _PREAPPROVAL_KEYS if key in front]


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_no_shipped_skill_pre_approves_any_tool(skill_dir: Path) -> None:
    """Nothing is pre-approved, so every command this skill suggests prompts the operator.

    This is the whole of the skill's safety posture, and it is deliberately uniform rather than
    "read-only commands are pre-approved". `ctx-distillery distill` is NOT inert: it bills the
    operator, writes a trace, and ships their redacted project history to a remote endpoint. A
    `Bash(... *)` grant would also cover every OTHER project on the machine, since `*` matches
    spaces. Approving the run they just asked for costs one prompt.
    """
    front, _ = parse_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    found = _preapproval_keys(front)
    assert not found, (
        f"{skill_dir.name}/SKILL.md declares {found}. Shipped skills pre-approve nothing — see this "
        f"test's docstring for why `distill` in particular must not be granted."
    )


@pytest.mark.parametrize(
    "grant",
    [
        "Bash(ctx-distillery-apply *)",
        "Bash(uv run *)",
        "Bash(uvx *)",
        "Bash(*)",
        "Bash",
        "Read Grep",
        "",
    ],
)
def test_the_preapproval_check_would_catch_a_real_grant(grant: str) -> None:
    """Guard the guard. An earlier draft of this check asked whether any `allowed-tools` string
    MENTIONED the apply binary; every entry above except the first defeats that, and the last two
    show why the check is on the KEY rather than its value — an empty or unrelated grant still means
    somebody reopened this decision."""
    front, _ = parse_frontmatter(f"---\nname: x\ndescription: y\nallowed-tools: {grant}\n---\n\nb\n")
    assert _preapproval_keys(front), f"the pre-approval check missed `allowed-tools: {grant!r}`"


# --------------------------------------------------------------------------------------------------
# Every command the skill prints must parse through the REAL parser.
# --------------------------------------------------------------------------------------------------


def _fenced_commands(text: str) -> list[str]:
    """Command lines inside fenced code blocks that invoke one of this project's console scripts.

    Prose outside a fence is ignored on purpose: `reference.md` names flags mid-sentence, and those
    are not invocations. A trailing `>` redirect and a trailing `#` comment are stripped — both are
    shell syntax argparse never sees.
    """
    commands: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            continue
        line = raw.strip()
        if line.split(" ")[0] not in _PARSERS:
            continue
        line = line.split(" #", 1)[0].split(" >", 1)[0].rstrip(" \\")
        commands.append(line)
    return commands


ALL_COMMANDS = sorted(
    {
        (doc.relative_to(ROOT).as_posix(), command)
        for doc in SKILL_DOCS
        for command in _fenced_commands(doc.read_text(encoding="utf-8"))
    }
)


def test_the_skills_actually_show_some_commands() -> None:
    """Otherwise the parametrized check below is vacuous — which is exactly how a rewrite that
    reformatted the fences would neuter it silently."""
    assert ALL_COMMANDS, "no console-script invocations found in any shipped skill's code fences"


@pytest.mark.parametrize(("doc", "command"), ALL_COMMANDS)
def test_every_documented_command_parses(doc: str, command: str) -> None:
    """`parse_args` on the real parser, so subcommand scoping, `required=True` and `choices` are all
    checked at once. Placeholders (`<run-id>`, `/path/to/project`) parse fine as plain strings.

    `--version`/`--help` exit 0 by design; anything else exiting non-zero is a broken example.
    """
    argv = shlex.split(command)
    parser = _PARSERS[argv[0]]()
    try:
        parser.parse_args(argv[1:])
    except SystemExit as exc:
        assert not exc.code, f"{doc} documents a command argparse rejects: `{command}`"


@pytest.mark.parametrize(
    "broken",
    [
        "ctx-distillery export traces/x.jsonl --json",  # --json is not on `export`
        "ctx-distillery-apply traces/x.jsonl --approve 0",  # --project is required
        "ctx-distillery-apply traces/x.jsonl --project . --approve 0 --allow-skill-scope everywhere",
        "ctx-distillery distill --nonexistent-flag",
        "ctx-distillery",  # a subcommand is required
    ],
)
def test_the_command_check_would_catch_a_broken_example(broken: str) -> None:
    """Guard the guard, covering each failure class the flag-name scan this replaced could not see:
    subcommand scoping, a missing required option, an out-of-vocabulary `choices` value, an unknown
    flag, and a missing subcommand."""
    argv = shlex.split(broken)
    with pytest.raises(SystemExit) as caught:
        _PARSERS[argv[0]]().parse_args(argv[1:])
    assert caught.value.code, f"argparse accepted `{broken}` — this check is not discriminating"


def test_the_version_the_skill_names_is_this_package_s_version() -> None:
    """An INSTALLED skill is detached from this repo and pinned to nothing, so it tells the operator
    which version it was written against and how to check theirs. That number is a doc claim like
    any other: if it drifts, the skill confidently vouches for flags it never saw."""
    from ctx_distillery import __version__

    prose = " ".join(doc.read_text(encoding="utf-8") for doc in SKILL_DOCS)
    assert f"**{__version__}**" in prose, (
        f"no shipped skill states the version it was written against as `**{__version__}**` — bump "
        f"it together with `ctx_distillery.__version__`, or the published copy vouches for a CLI it "
        f"has never seen"
    )


def test_the_default_skill_scope_is_project_only_and_the_skill_says_so() -> None:
    """A semantic pin the command check cannot express: `--allow-skill-scope` existing says nothing
    about what happens when it is OMITTED. If the default ever widened to include `global`, the
    skill's prose ("the default is project-only") would be silently wrong about the one flag whose
    blast radius is every project the operator opens."""
    assert apply_module.DEFAULT_SKILL_SCOPES == ("project",)
    prose = " ".join(doc.read_text(encoding="utf-8") for doc in SKILL_DOCS)
    assert "--allow-skill-scope global" in prose and "project-only" in prose, (
        "the shipped skills must state that installing a GLOBAL skill needs "
        "`--allow-skill-scope global` and that the default is project-only"
    )


# --------------------------------------------------------------------------------------------------
# The marketplace manifest.
# --------------------------------------------------------------------------------------------------


def test_marketplace_manifest_is_well_formed() -> None:
    """Shape, not just "valid JSON and the path exists" — that weaker check passes on a manifest
    whose entries point nowhere useful. Required by Claude Code: top-level `name`/`owner`/`plugins`,
    and `name`/`source` on every entry."""
    manifest = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert isinstance(manifest.get("name"), str) and manifest["name"]
    assert isinstance(manifest.get("owner"), dict) and manifest["owner"].get("name")
    assert isinstance(manifest.get("plugins"), list) and manifest["plugins"]
    for entry in manifest["plugins"]:
        assert isinstance(entry.get("name"), str) and entry["name"], f"unnamed plugin entry: {entry}"
        source = entry.get("source")
        assert isinstance(source, str) and (ROOT / source).is_dir(), (
            f"plugin {entry.get('name')!r} has source {source!r}, which is not a directory here"
        )
        for skills_path in entry.get("skills", []):
            resolved = ((ROOT / source) / skills_path).resolve()
            assert resolved.is_dir(), (
                f"plugin {entry.get('name')!r} declares skills path {skills_path!r}, which does not "
                f"exist under its source"
            )
            # It must be the skill DIRECTORY itself, not a parent holding several. This is what
            # makes the `npx skills` CLI attribute the skill to this plugin: `plugin-manifest.ts`
            # keys its grouping map on `resolve(join(source, skillPath))`, and `skills.ts` looks it
            # up by the SKILL's own resolved directory. The likelier-looking `source: "./"` +
            # `skills: ["./skills"]` misses by exactly one level — it installs and dedupes fine, but
            # the skill shows up ungrouped, and `source: "./"` also copies the whole repository into
            # the plugin cache on every install and update.
            assert (resolved / "SKILL.md").is_file(), (
                f"plugin {entry.get('name')!r}'s skills path {skills_path!r} is not a skill "
                f"directory (no SKILL.md directly inside it)"
            )
            assert resolved in {d.resolve() for d in SKILL_DIRS}, (
                f"plugin {entry.get('name')!r} points at {resolved}, which is not one of the "
                f"shipped skills under {SKILLS_DIR}"
            )


def test_marketplace_entries_are_non_strict_because_no_plugin_manifest_is_shipped() -> None:
    """`strict` defaults to TRUE, which makes `.claude-plugin/plugin.json` the authority for a
    plugin's components. This repo ships no such file — the marketplace entry IS the whole
    definition — so every entry must opt out explicitly. Adding a `plugin.json` later is fine; this
    test then tells you to drop the `strict: false` that would conflict with it."""
    manifest = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    shipped = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob(".claude-plugin/plugin.json"))
    assert not shipped, f"a plugin manifest now exists ({shipped}) — revisit `strict` on every entry"
    for entry in manifest["plugins"]:
        assert entry.get("strict") is False, (
            f"plugin {entry.get('name')!r} does not set `strict: false`, but no plugin.json is "
            f"shipped for it. `strict: false` is the documented combination for a marketplace entry "
            f"that IS the whole definition; leaving the default in place makes an absent plugin.json "
            f"the authority for this plugin's components, which is not what is meant here."
        )


#: Marketplace-entry keys this repo ships. Deliberately CLOSED, and deliberately not just the
#: executable ones: a marketplace entry may also declare `hooks`, `mcpServers`, `lspServers` and
#: `agents`, every one of which is code a stranger runs on install. The "pre-approves nothing"
#: property is stated about SKILL.md frontmatter and would be quietly false if any of those appeared
#: here, so the check is on the whole key set rather than a list of the dangerous ones.
_ALLOWED_ENTRY_KEYS = frozenset(
    {"name", "description", "source", "skills", "strict", "category", "homepage"}
)


def test_the_marketplace_ships_no_executable_component() -> None:
    """Guards the other half of "this package pre-approves nothing". An unexpected key is a refusal
    rather than an oversight: adding one means re-arguing the safety posture, not editing a list."""
    manifest = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    for entry in manifest["plugins"]:
        unexpected = sorted(set(entry) - _ALLOWED_ENTRY_KEYS)
        assert not unexpected, (
            f"plugin {entry.get('name')!r} declares {unexpected}. `hooks`/`mcpServers`/`lspServers`/"
            f"`agents` ship executable code with the plugin; if one is genuinely wanted, say so in "
            f"`CLAUDE.md` invariant 8 and widen this set in the same commit."
        )


def test_the_marketplace_does_not_pin_a_version() -> None:
    """`CHANGELOG.md` has `## [Unreleased]` as its only heading, and `CLAUDE.md ## Versioning`
    records that cutting `0.1.0` prematurely was already done once and reverted: a version number is
    the owner's statement that something is usable. A git-hosted marketplace that omits `version`
    treats every commit as the current one, which is the honest shape until a release is cut."""
    manifest = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    assert "version" not in manifest
    for entry in manifest["plugins"]:
        assert "version" not in entry, (
            f"plugin {entry.get('name')!r} pins a version while the project has cut none"
        )
