"""`python -m ctx_distillery_eval` — the same entry as the `ctx-distillery-eval` console script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
