# ctx-distillery — reference

Companion to `SKILL.md`. Load this when reading a plan in detail, when a run refuses to start, or
when the operator asks what will happen if they apply something.

**If the operator has never seen a plan and is unsure what this proposes**, show them a real one
instead of describing it. A finished run ships with the project and needs no credentials:

```
curl -sO https://raw.githubusercontent.com/qazbnm456/ctx-distillery/main/examples/demo-run.jsonl
ctx-distillery show demo-run.jsonl
```

## The plan rendering, field by field

`ctx-distillery show <trace>` prints one block per candidate. Every field is assembled by *re-reading
the trace*, never taken from the plan's own claim about what it drafted.

```
[0] action=promote_to_skill artifact_id='a1'
    key_fields={'scope': 'project'}
    draft (ok=True):
<the drafted markdown + frontmatter, flush-left>
    extra file references/api.md (ok=True):
<the drafted supplementary file, flush-left>
    problems: [...]
(run-level problems: [...])
```

Note the indentation: the per-candidate header lines are indented four spaces, but a drafted BODY
is emitted flush-left. A draft's own frontmatter therefore starts with `---` at column 0.

| Field | What it means |
|---|---|
| `[0]` | The **list index**. This is what `ctx-distillery-apply --approve` takes. |
| `action` | One of `keep`, `prune`, `promote_to_memory`, `promote_to_skill`. There is no "revise" action. |
| `artifact_id` | Links the candidate to the drafting call that authored its text. |
| `key_fields` | Action-specific data. `prune` carries `target_path` (which file); `promote_to_skill` carries `scope` (`project` or `global`). |
| `draft (ok=…)` | The actual bytes that would be written. `ok=False` means the drafting call did not produce usable text — **the apply step will refuse it even if approved**. |
| `extra file <path>` | A skill's supplementary `references/`/`scripts/` file, drafted separately and written alongside `SKILL.md`. |
| `problems` | Candidate-level defects. Any non-empty value is a hard refusal at apply time. |
| `(run-level problems: …)` | The run itself did not finish cleanly. A plan with no candidates *and* a problems line means the run died before it submitted anything. |

A leading line saying the run's harness is not `claude_code` means every non-`keep` candidate will be
refused: the write path understands only Claude Code's on-disk layout.

## What the apply step refuses, no matter what was approved

Four checks, re-run at apply time rather than trusted from the plan:

1. the candidate carries `problems`;
2. `draft_ok is False`;
3. a promotion whose drafted text is empty;
4. a non-`keep` action from a run whose harness is not `claude_code`.

Beyond those: a promotion whose name collides with an existing file is refused unless that index is
*also* named in `--overwrite` (which never approves a candidate on its own); a `project`-scope skill
whose name a **global** skill already holds is refused outright with no override, because the global
one would shadow it and the project skill could never be reached; and a `prune` is **archived** to
`_ctx_distillery_archive/`, never deleted.

`--allow-skill-scope` defaults to `project` only. Installing into `~/.claude/skills` requires
`--allow-skill-scope global` explicitly. Do not add that flag unless the operator asks for a global
install and understands it reaches every project they open.

Two timing caveats worth telling the operator after a skill is installed: a project's *first*
`.claude/skills/` directory needs a Claude Code restart before it is discovered, and a project
skill's `allowed-tools` frontmatter only takes effect once the workspace-trust dialog is accepted.

## Memory or skill?

The two promotion targets are not interchangeable, and the run has already committed to one — but
the operator is the one deciding whether it chose correctly.

- **Memory** — a *fact* about the user or the project. A decision that was made, a constraint that
  was discovered, a piece of state worth remembering. "This project froze merges on 2026-03-11."
- **Skill** — a reusable *procedure*. A workflow or technique worth documenting once and reusing on
  demand. "When doing Y, always check Z first, because of incident W."

A skill also declares a scope: `project` when it is tied to this repository's own tooling and would
be noise elsewhere, `global` when the technique is genuinely portable. The two scopes are separate
namespaces — the same name existing in the other one is not a collision.

A skill file requires `name` and `description` in its frontmatter and nothing else. `when_to_use`
and `dispatch_intent` are accepted when offered, never demanded.

## What the planner actually sees

The transcript rendering is deliberately lossy, and it matters when judging whether a plan missed
something:

- assistant `tool_use` blocks collapse to a short `[used tool: X]` label — on a real agent
  transcript that is most of the assistant's output by volume;
- assistant **thinking blocks render to nothing**, because Claude Code does not persist the thinking
  text (the block exists, its `thinking` field is empty);
- subagent conversations are stored separately and are read only with `--include-subagents`.

The practical consequence: a rendered transcript is mostly the *user's* messages. If a plan seems to
have missed something the assistant worked out silently, this is usually why.

## Environment

| Variable | Purpose |
|---|---|
| `CD_ROOT_LM` | The planner model. Accepts a `claude-agent-sdk/<id>` sentinel to run on a Claude subscription. |
| `CD_API_KEY`, `CD_BASE_URL` | Credentials/endpoint for the planner. |
| `CD_DRAFT_LM`, `CD_DRAFT_API_KEY`, `CD_DRAFT_BASE_URL` | The drafting model. **The drafter cannot ride a Claude subscription** — it talks to an OpenAI-compatible endpoint directly. Leaving `CD_DRAFT_LM` unset while `CD_ROOT_LM` carries the subscription sentinel makes the run refuse to start, on purpose. |
| `CD_REDACTIONS` | Path to a JSON file of extra redaction rules. Additive only — it can never disable a built-in rule. |
| `CTXD_TRACES_DIR` | Where traces are written and looked for. Default `./traces`. |

A full annotated list lives in the repository's `.env.example`.

## Refusals you may hit when starting a run

| Message | Meaning |
|---|---|
| `no transcripts found under …` | Claude Code has no stored conversations for that project. Nothing to distil. |
| `<path> already exists, and a trace is appended to rather than replaced` | Re-used run id. Pass a different `--run-id`; there is deliberately no flag that deletes a trace. |
| `no trace files matched …` | Quote the glob so the shell does not expand it first. |
| a warning about transcript entries exceeding what fits one REPL cell | Informational. The planner still sees every entry and can page through them; only its one-shot overview is truncated. |
