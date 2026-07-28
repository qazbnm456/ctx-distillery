"""`python -m ctx_distillery ...` -> the planner CLI (`distill` / `show`).

The WRITER has no `python -m` shim on purpose: `ctx-distillery-apply` is `ctx_distillery.apply:main`,
reached by its own console script. Adding `python -m ctx_distillery apply` would mean this module
importing the writer, which is exactly what `CLAUDE.md` invariant 8's reachability test forbids.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
