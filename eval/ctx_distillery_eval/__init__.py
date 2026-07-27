"""`ctx-distillery-eval` — an offline, reward-free evaluation harness for `ctx-distillery`.

Scores an assembled distillation plan (`ctx_distillery.session.AssembledPlan`), together with the
transcript excerpt(s) it was drawn from, against the ATLAS TF/TA/TG/PA (0-10) LLM-as-judge, and
renders a terminal scorecard. A ONE-WAY reader of `ctx_distillery`'s public surface: this package is
never imported back by `ctx_distillery` itself (`tests/test_boundary.py` pins this both here and in
the root package). Reward-free, matching every sibling eval member — per-category means only, never
a composite score, never a training signal computed here.
"""

from __future__ import annotations

__version__ = "0.1.0"
