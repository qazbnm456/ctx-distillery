"""Shared pytest fixtures for ctx-distillery.

Empty for now — this is the first scaffolding pass, and there is no tool or adapter
implementation yet to share fixtures across (see `ctx_distillery/task.py` and
`ctx_distillery/adapters/base.py`). Shared fixtures (e.g. a `ScriptedInterpreter`-backed
`DistillSession`, a fake `HarnessAdapter`, sample transcripts/memory indices) will land here
once real tools/adapters are implemented.
"""
