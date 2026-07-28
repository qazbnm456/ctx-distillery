"""Shared setup for the studio suite — HERMETIC BY CONSTRUCTION, and that starts before collection.

This member had no `conftest.py` at all. It gets one for exactly one reason: `ctx_distillery.redact`
resolves the `CD_REDACTIONS` env var at MODULE IMPORT time (fail-closed — a broken operator rule file
must stop the process, never leave a silently weaker redactor running), and this suite imports
`ctx_distillery` during COLLECTION, through `app.py` -> `ctx_distillery.schema`/`rubric`. A developer
with that variable exported therefore ran this member against their own private redaction rules: a
BROKEN file turns collection into an INTERNALERROR, and a VALID one just makes the suite quietly
non-hermetic in the same way reading a real `~/.claude` would be.

Popping it here, at conftest import, is the whole fix — an autouse fixture is far too late, because
fixtures run after collection has already imported everything. `tests/conftest.py` in the root member
does the same thing for the same reason, and `eval/tests/conftest.py` now does too; an adversarial
review found that the fix had landed in ONE of the three suites while `CHANGELOG.md` claimed it for
all of them.

Nothing else belongs here. The studio never reads the `CD_*`/`CDEVAL_*` model configuration — it
replays a finished trace file and never drives a live run (`CLAUDE.md` invariant 10) — so there is no
live-endpoint surface to scrub, which is precisely why `eval/tests/conftest.py` is bigger.
"""

from __future__ import annotations

import os

os.environ.pop("CD_REDACTIONS", None)
