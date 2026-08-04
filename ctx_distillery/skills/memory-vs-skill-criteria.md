---
name: memory-vs-skill-criteria
description: Criteria for deciding whether a distillation finding is a memory candidate (a fact/decision/constraint about the user or project) or a skill candidate (a reusable procedure/how-to) — placeholder, not yet filled in with concrete examples.
---

# Memory vs. skill criteria (placeholder)

This is a placeholder — to be filled with concrete criteria/examples for when a finding is a
memory candidate vs. a skill candidate, once real distillation runs accumulate examples. The
current design-level distinction is that a fact about the user/project is a memory, while a
reusable how-to/procedure discovered during a session is a skill; see
`ctx_distillery/task.py`'s instructions for how the planner is currently told to draw that line.

This file follows the Agent-Skills convention used elsewhere in the rlm-harness ecosystem
(`name`/`description` frontmatter, progressive disclosure via `skills.py`'s `list_skills`/
`read_skill`) — but note that ctx-distillery does not currently load skills this way for its own
planner LM; see `VENDOR.md` for why. This file exists as a drafting-target reference, not as
something `ctx_distillery.task.DistillSession` reads via `load_skills_as_tools`.
