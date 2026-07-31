# One stable verification entrypoint. `make check` runs everything CI runs, in CI's order.
#
# Why this file exists: full verification is FIVE commands across FOUR suites (root, eval/, studio/
# Python, studio/ frontend) plus lint, and until this file landed they existed only as prose in
# `CLAUDE.md ## Verify` — an agent or a new contributor had to reassemble the list by reading ~40
# lines before they could run anything. CI has always had them as five separate jobs; this is the
# local mirror.
#
# NOT a hole in invariant 1. `tests/test_no_write_capability.py` scans `ctx_distillery/**.py` for
# write/delete calls; a Makefile is not a module and nothing on the RLM path can reach it. It only
# invokes test runners, which is what a human already does by hand.
#
# The raw commands are ALSO documented in `CLAUDE.md ## Verify` and encoded in
# `.github/workflows/ci.yml`. `tests/test_doc_claims.py` pins the parts that must not drift apart.

# bash, not sh: `static-test` needs `shopt -s nullglob` and an array.
SHELL := /bin/bash

.DEFAULT_GOAL := check
.PHONY: check lint test eval-test studio-test static-test help

## check: everything CI runs, in CI's order
check: lint test eval-test studio-test static-test

## lint: ruff, PINNED — an unpinned `uvx ruff` resolves the latest at run time and reddens CI
##       with nobody having touched a line of code. Bump on purpose, fix in the same commit.
lint:
	uvx ruff@0.16.0 check .

## test: the root suite (offline; the dspy-bearing tests importorskip). `uv run`, not the bare
##       `pytest -q` CLAUDE.md documents — that spelling assumes an ACTIVATED venv and dies with
##       "No such file or directory" without one. Every other target here already goes through uv,
##       and so does CI.
test:
	uv run python -m pytest -q

## eval-test: the eval workspace member. `--directory` is load-bearing — `--package` alone
##            selects the ENVIRONMENT but not which pyproject.toml's `testpaths` resolves, which
##            once made this silently re-run the root suite instead.
eval-test:
	uv run --directory eval --package ctx-distillery-eval --extra dev python -m pytest -q

## studio-test: the studio workspace member's Python suite (same `--directory` reason)
studio-test:
	uv run --directory studio --package ctx-distillery-studio --extra dev python -m pytest -q

## static-test: the studio's frontend static contracts. Plain CommonJS — no npm, no package.json,
##              no node_modules. `nullglob` guards the empty case: without it the loop would run
##              `node 'studio/tests/*.test.js'` literally and fail module-not-found.
static-test:
	@shopt -s nullglob; \
	files=(studio/tests/*.test.js); \
	if [ $${#files[@]} -eq 0 ]; then echo "no frontend tests"; exit 0; fi; \
	for f in "$${files[@]}"; do echo "▸ $$f"; node "$$f"; done

## help: list the targets
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## //'
