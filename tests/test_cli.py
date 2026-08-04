"""`ctx_distillery.cli` + `ctx_distillery.config` — the planner-side CLI, driven fully OFFLINE.

`show` needs nothing but a trace file, so it runs for real against traces written here by a genuine
`TraceRecorder` (the same "build the real artifact, don't mock the reader" stance `tests/conftest.py`
takes for the memory store). `distill` is orchestration, not judgement — ingest, wire, run, assemble
— so the RLM is stubbed at the seam the sibling projects established: `cli.run_distillation` is
resolved as a module global at call time, which makes `monkeypatch.setattr(cli, ...)` with an
`async def` fake the right hook, with no production signature bent to accommodate a test.

Nothing here reads this machine's real `~/.claude`: every `distill` case passes `--claude-home` at a
`tmp_path` fake, per `CLAUDE.md` invariant 6.
"""

from __future__ import annotations

import json

import pytest
from rlm_harness.trace import TraceRecorder, record_tool_call

from ctx_distillery import cli, config, task
from ctx_distillery.adapters.claude_code import project_storage_dir
from ctx_distillery.session import AssembledPlan
from ctx_distillery.task import DistillCandidate, DistillPlan
from tests.test_no_write_capability import IMPORTS_APPLY, _code_lines

MEMORY_DRAFT = (
    "---\n"
    "name: merge-freeze-policy\n"
    "description: Merges are frozen during a release.\n"
    "metadata:\n"
    "  type: project\n"
    "---\n"
    "Merges into main are frozen for the duration of a release.\n"
)

#: The `CD_*` variables every test in this file must control rather than inherit — a developer with
#: a real `.env` sourced into their shell would otherwise see different results than CI.
CD_VARS = (
    "CD_ROOT_LM", "CD_SUB_LM", "CD_API_KEY", "CD_BASE_URL", "CD_DRAFT_LM",
    "CD_DRAFT_API_KEY", "CD_DRAFT_BASE_URL", "CD_INTERPRETER",
    "CD_MAX_ITERATIONS", "CD_MAX_LLM_CALLS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No `CD_*` (or `CTXD_TRACES_DIR`) leaks in from the developer's own shell."""
    for name in (*CD_VARS, "CTXD_TRACES_DIR"):
        monkeypatch.delenv(name, raising=False)


def write_trace(path, *, run_id="r0", candidates=None, draft=MEMORY_DRAFT, ok=True, with_result=True,
                tool="draft_memory_file"):
    """Lay down a REAL trace the way a finished run would: a drafting tool_call, then the plan.

    `assemble` re-sources a promotion's bytes from the drafting event whose `artifact_id` the
    candidate names (`CLAUDE.md` invariant 2), so both halves have to be genuine for `show` (or
    `ctx-distillery-apply`, which imports this helper) to see anything — a hand-built plan alone
    would assemble into a problem, not a draft. `tool` picks which of the two drafting tools
    authored it, because the action and the tool must MATCH or `assemble` reports a mismatch.
    """
    plan = DistillPlan(candidates=[DistillCandidate(**c) for c in (candidates or [])])
    kind = "skill" if tool == "draft_skill_file" else "memory"
    with TraceRecorder(str(path), run_id=run_id, meta={"transcripts": 1}) as rec:
        record_tool_call(
            tool,
            args={"topic": "the merge freeze", "memory_type": "project"},
            ok=ok,
            artifact_id="artifact-1",
            kind=kind,
            draft=draft,
            errors=[],
        )
        if with_result:
            rec.record_result(plan)
    return str(path)


PROMOTION = {"action": "promote_to_memory", "artifact_id": "artifact-1", "key_fields": {"transcripts": [0]}}
KEEP = {"action": "keep", "key_fields": {"reason": "still relevant"}}


# -- the parser ------------------------------------------------------------------------------


def test_parser_has_both_subcommands():
    parser = cli.build_parser()
    assert parser.parse_args(["distill"]).func is cli._cmd_distill
    assert parser.parse_args(["show", "t.jsonl"]).func is cli._cmd_show


def test_distill_defaults_to_the_current_directory():
    assert cli.build_parser().parse_args(["distill"]).project_dir == "."


# -- the invariant-8 split: this module may never reach the writer ----------------------------


def test_the_planner_cli_does_not_import_the_writer():
    """The whole reason `apply` is a SECOND console script (`CLAUDE.md` invariant 8).

    Asserted with the very regex `test_apply_is_unreachable_from_the_planner_path` uses, imported
    rather than re-typed: a lookalike pattern here could drift and quietly stop covering the case
    the real tripwire cares about. `cli.py` is already inside that test's `SOURCES`, so this is a
    readable restatement aimed at the CLI specifically, not a substitute for it.
    """
    source = __import__("pathlib").Path(cli.__file__)
    offences = [line for _n, line in _code_lines(source) if IMPORTS_APPLY.search(line)]
    assert not offences, offences
    assert not hasattr(cli, "apply_plan")


def test_the_module_entry_point_does_not_import_the_writer_either():
    from ctx_distillery import __main__

    source = __import__("pathlib").Path(__main__.__file__)
    assert not [line for _n, line in _code_lines(source) if IMPORTS_APPLY.search(line)]


# -- config: the `CD_*` surface `.env.example` documents ---------------------------------------


def test_the_pinned_interpreter_constants_agree():
    """`config.py` restates the pin instead of importing `task` (which would drag in dspy) — so the
    two are pinned to agree HERE, or the refusal below could out-live the thing it guards."""
    assert config.PINNED_INTERPRETER == task.PINNED_INTERPRETER == "pyodide"


def test_from_env_refuses_without_a_planner_model():
    with pytest.raises(SystemExit) as excinfo:
        config.DistillConfig.from_env()
    assert "CD_ROOT_LM" in str(excinfo.value)


def test_from_env_refuses_a_non_pyodide_interpreter(monkeypatch):
    """`task._forced_config` would coerce it back anyway — refusing is about the operator SEEING
    that what they configured is not what runs, not about the sandbox guarantee itself."""
    monkeypatch.setenv("CD_ROOT_LM", "some-model")
    monkeypatch.setenv("CD_INTERPRETER", "local")
    with pytest.raises(SystemExit) as excinfo:
        config.DistillConfig.from_env()
    assert "local" in str(excinfo.value) and "pyodide" in str(excinfo.value)


def test_from_env_falls_back_from_draft_to_sub_to_root(monkeypatch):
    monkeypatch.setenv("CD_ROOT_LM", "planner")
    cfg = config.DistillConfig.from_env()
    assert (cfg.main_model, cfg.sub_model, cfg.draft_model) == ("planner", "planner", "planner")

    monkeypatch.setenv("CD_SUB_LM", "specialist")
    assert config.DistillConfig.from_env().draft_model == "specialist"

    monkeypatch.setenv("CD_DRAFT_LM", "drafter")
    assert config.DistillConfig.from_env().draft_model == "drafter"


def test_from_env_reads_the_endpoint_and_budget_vars(monkeypatch):
    monkeypatch.setenv("CD_ROOT_LM", "planner")
    monkeypatch.setenv("CD_API_KEY", "sk-test")
    monkeypatch.setenv("CD_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("CD_MAX_ITERATIONS", "7")
    monkeypatch.setenv("CD_MAX_LLM_CALLS", "2")
    cfg = config.DistillConfig.from_env()
    assert cfg.api_key == "sk-test" and cfg.base_url == "https://proxy.example/v1"
    assert (cfg.max_iterations, cfg.max_llm_calls) == (7, 2)


def test_the_planner_generation_cap_and_adapter_are_reachable_from_the_env(monkeypatch):
    """Both were UNPASSED to `RLMConfig` and unreachable from `CD_*` — a real, reported defect.

    `max_tokens` is not a tuning knob here, it is a failure mode. rlm-harness defaults it to 8192, and
    dspy reads `content` while DISCARDING `reasoning_content` — so a reasoning planner's
    chain-of-thought is billed against a cap it never appears in. It then dies one of two ways
    (reasoning exhausts the cap -> empty `content`; or the answer is cut mid-JSON), and BOTH are
    terminal because `setup` pins `max_retries=1` on purpose. A sibling project hit exactly this on
    its first live turn. The default is the recommended planner value, not rlm-harness's.
    """
    monkeypatch.setenv("CD_ROOT_LM", "planner")
    assert config.DistillConfig.from_env().planner_max_tokens == 16384
    assert config.DistillConfig.from_env().adapter == "json"

    monkeypatch.setenv("CD_PLANNER_MAX_TOKENS", "32768")
    monkeypatch.setenv("CD_ADAPTER", "chat")
    cfg = config.DistillConfig.from_env()
    assert (cfg.planner_max_tokens, cfg.adapter) == (32768, "chat")


def test_a_bogus_adapter_is_refused_in_from_env_like_every_other_CD_mistake(monkeypatch):
    """`CD_ADAPTER` refuses HERE, with the same clean `SystemExit` the rest of the surface gives.

    Two rounds. This test first pinned only that `setup` PASSED `adapter` to `RLMConfig` at all —
    while it went unpassed, any value of `CD_ADAPTER` was silently inert, so reaching rlm-harness's own
    `KNOWN_ADAPTERS` validation was the property worth having. A review then pointed out that
    `CD_ADAPTER` was the ONE variable on this surface whose typo produced a raw traceback from deep
    inside `RLMConfig.__post_init__`, where `CD_ROOT_LM`, `CD_INTERPRETER` and every non-integer
    budget give a clean, actionable `SystemExit`. Consistency on an env surface is not cosmetic —
    it is the difference between "I typed it wrong" and "the tool is broken".

    rlm-harness still validates independently; this is a second gate, not a replacement, and the mirrored
    vocabulary is pinned by `test_the_known_adapters_match_rlm_harnesss` below.
    """
    monkeypatch.setenv("CD_ROOT_LM", "planner")
    monkeypatch.setenv("CD_ADAPTER", "nonsense")
    with pytest.raises(SystemExit) as excinfo:
        config.DistillConfig.from_env()
    assert "nonsense" in str(excinfo.value) and "json" in str(excinfo.value)

    # Case matters, and saying so beats a mysterious failure: rlm-harness's set is lower-case.
    monkeypatch.setenv("CD_ADAPTER", "JSON")
    with pytest.raises(SystemExit):
        config.DistillConfig.from_env()


def test_the_known_adapters_match_rlm_harnesss(monkeypatch):
    """Our mirrored vocabulary must equal rlm-harness's, or we would refuse a value the kit accepts."""
    from rlm_harness.config import KNOWN_ADAPTERS

    assert set(config._KNOWN_ADAPTERS) == set(KNOWN_ADAPTERS)


def test_from_env_refuses_a_non_integer_budget(monkeypatch):
    monkeypatch.setenv("CD_ROOT_LM", "planner")
    monkeypatch.setenv("CD_MAX_ITERATIONS", "lots")
    with pytest.raises(SystemExit) as excinfo:
        config.DistillConfig.from_env()
    assert "CD_MAX_ITERATIONS" in str(excinfo.value)


def test_make_chat_fn_returns_a_callable_without_importing_openai():
    """`openai` lives behind the `cli` extra and is imported INSIDE the closure — building the
    chat_fn must not require it, or an offline `show`/`apply` install would break on import."""
    chat_fn = config.make_chat_fn(config.DistillConfig(main_model="planner"))
    assert callable(chat_fn)


# -- show ------------------------------------------------------------------------------------


def test_show_renders_a_real_trace(tmp_path, capsys):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION, KEEP])
    assert cli.main(["show", trace]) == 0
    out = capsys.readouterr().out
    assert "[0] action=promote_to_memory" in out
    assert "[1] action=keep" in out
    # The DRAFTED BYTES, re-sourced from the tool_call event rather than from the plan's own claim.
    assert "Merges into main are frozen" in out


def test_show_emits_json_with_the_assembled_draft(tmp_path, capsys):
    trace = write_trace(tmp_path / "r0.jsonl", candidates=[PROMOTION])
    assert cli.main(["show", trace, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"][0]["action"] == "promote_to_memory"
    assert payload["candidates"][0]["draft"] == MEMORY_DRAFT
    assert payload["problems"] == []


def test_show_filters_by_run_id(tmp_path, capsys):
    path = tmp_path / "mixed.jsonl"
    write_trace(path, run_id="r0", candidates=[PROMOTION])
    write_trace(path, run_id="r1", candidates=[KEEP, KEEP])
    assert cli.main(["show", str(path), "--run-id", "r1"]) == 0
    out = capsys.readouterr().out
    assert "[1] action=keep" in out and "promote_to_memory" not in out


def test_show_reports_a_trace_that_never_finalized(tmp_path, capsys):
    """A run that died before SUBMIT has no result event — `assemble` reports it, never raises.

    This is also the regression test for the renderer bug found while moving `render_plan` into the
    shared module: the run-level problem used to be dropped whenever a plan had no candidates, so
    exactly this case rendered as the bare "proposed no candidates" and said nothing about why.
    """
    trace = write_trace(tmp_path / "r0.jsonl", with_result=False)
    assert cli.main(["show", trace]) == 1
    assert "no plan was produced by this run" in capsys.readouterr().out


def test_show_survives_a_non_dict_line(tmp_path, capsys):
    """`CLAUDE.md` invariant 11 at a NEW caller: `show` reads through `trace_io.load_trace`, so a
    JSON-valid non-object line is dropped before anything calls `.get()` on it."""
    path = tmp_path / "r0.jsonl"
    write_trace(path, candidates=[PROMOTION])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("42\n[1, 2, 3]\nnull\n")
    assert cli.main(["show", str(path)]) == 0
    assert "[0] action=promote_to_memory" in capsys.readouterr().out


def test_show_of_a_missing_file_is_a_message_not_a_traceback(tmp_path, capsys):
    assert cli.main(["show", str(tmp_path / "nope.jsonl")]) == 1
    assert "cannot read" in capsys.readouterr().err


# -- distill: run ids, trace paths, and the empty-project case ---------------------------------


def test_default_run_id_names_the_project_and_carries_a_utc_timestamp(tmp_path):
    run_id = cli.default_run_id(tmp_path / "my repo")
    assert run_id.startswith("my-repo-") and run_id.endswith("Z")


@pytest.mark.parametrize("raw", ["../../etc/passwd", "a/b/c", "..", "  weird  name "])
def test_a_run_id_can_never_escape_the_trace_directory(raw):
    slug = cli._slug(raw)
    assert "/" not in slug and not slug.startswith(".")


def test_distill_says_so_when_a_project_has_no_transcripts(tmp_path, claude_home, capsys):
    """The `CLAUDE.md`-invariant-6 case a first-time user actually hits: storage may not exist yet.
    Proposing an empty plan here would look broken; naming the directory that is empty does not."""
    project = tmp_path / "proj"
    project.mkdir()
    rc = cli.main(["distill", str(project), "--claude-home", str(claude_home)])
    assert rc == 1
    assert str(project_storage_dir(project, home=claude_home)) in capsys.readouterr().err


def test_distill_refuses_a_project_directory_that_does_not_exist(tmp_path, capsys):
    assert cli.main(["distill", str(tmp_path / "absent")]) == 1
    assert "no such project directory" in capsys.readouterr().err


# -- distill: the orchestration, with the RLM stubbed ------------------------------------------


@pytest.fixture
def seeded_project(tmp_path, claude_home, monkeypatch):
    """A project whose Claude Code storage holds one real transcript file."""
    project = tmp_path / "proj"
    project.mkdir()
    storage = project_storage_dir(project, home=claude_home)
    storage.mkdir(parents=True)
    (storage / "session-1.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CD_ROOT_LM", "planner")
    # `setup` would build real dspy LMs from placeholder credentials; the seam under test is the
    # wiring `_cmd_distill` does, not rlm-harness's own configure.
    monkeypatch.setattr(cli, "setup", lambda cfg: cfg)
    return project


def _fake_run(captured, *, plan=None, boom=None):
    async def _run_distillation(adapter, chat_fn, trace_path, *, run_id=None, meta=None, **kw):
        captured.update(adapter=adapter, chat_fn=chat_fn, trace_path=trace_path, run_id=run_id, meta=meta)
        if boom is not None:
            raise boom
        return plan if plan is not None else AssembledPlan()

    return _run_distillation


def test_distill_wires_the_adapter_chat_fn_and_trace_path(seeded_project, claude_home, tmp_path,
                                                          monkeypatch, capsys):
    from ctx_distillery.adapters.claude_code import ClaudeCodeAdapter

    captured: dict = {}
    monkeypatch.setattr(cli, "run_distillation", _fake_run(captured))
    traces = tmp_path / "traces"

    rc = cli.main([
        "distill", str(seeded_project),
        "--claude-home", str(claude_home),
        "--trace-dir", str(traces),
        "--run-id", "demo",
    ])

    assert rc == 0
    assert isinstance(captured["adapter"], ClaudeCodeAdapter)
    assert callable(captured["chat_fn"])
    assert captured["trace_path"] == str(traces / "demo.jsonl")
    assert captured["run_id"] == "demo"
    # A self-describing trace: which project and which models this run actually used.
    assert captured["meta"]["project_dir"] == str(seeded_project.resolve())
    assert captured["meta"]["planner"] == "planner"
    assert captured["meta"]["interpreter"] == "pyodide"
    assert "1 transcript(s)" in capsys.readouterr().out


def test_distill_prints_the_plan_and_the_follow_up_commands(seeded_project, claude_home, tmp_path,
                                                            monkeypatch, capsys):
    from ctx_distillery.session import AssembledCandidate

    plan = AssembledPlan(candidates=[AssembledCandidate(action="keep", key_fields={"why": "fresh"})])
    monkeypatch.setattr(cli, "run_distillation", _fake_run({}, plan=plan))
    rc = cli.main(["distill", str(seeded_project), "--claude-home", str(claude_home),
                   "--trace-dir", str(tmp_path / "traces"), "--run-id", "demo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[0] action=keep" in out
    assert "ctx-distillery show" in out
    # The apply hint names the SECOND binary, and never offers a way to approve everything.
    assert "ctx-distillery-apply" in out and "--approve <indices>" in out


def test_distill_uses_the_ctxd_traces_dir_the_studio_reads(seeded_project, claude_home, tmp_path,
                                                           monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(cli, "run_distillation", _fake_run(captured))
    monkeypatch.setenv("CTXD_TRACES_DIR", str(tmp_path / "shared"))
    cli.main(["distill", str(seeded_project), "--claude-home", str(claude_home), "--run-id", "demo"])
    assert captured["trace_path"] == str(tmp_path / "shared" / "demo.jsonl")


def test_distill_refuses_to_append_onto_an_existing_trace(seeded_project, claude_home, tmp_path,
                                                          monkeypatch, capsys):
    """`TraceRecorder` appends and this package may not delete, so a re-used run id would interleave
    two runs under one id. Refusing beats silently mixing them — and beats a `--force` that deletes."""
    captured: dict = {}
    monkeypatch.setattr(cli, "run_distillation", _fake_run(captured))
    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "demo.jsonl").write_text("{}\n", encoding="utf-8")

    rc = cli.main(["distill", str(seeded_project), "--claude-home", str(claude_home),
                   "--trace-dir", str(traces), "--run-id", "demo"])

    assert rc == 1
    assert "already exists" in capsys.readouterr().err
    assert not captured, "the run must not start when its trace file is already there"


def test_distill_refuses_a_run_id_that_reduces_to_nothing(seeded_project, claude_home, tmp_path,
                                                          monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_distillation", _fake_run({}))
    rc = cli.main(["distill", str(seeded_project), "--claude-home", str(claude_home),
                   "--trace-dir", str(tmp_path / "traces"), "--run-id", "..."])
    assert rc == 1
    assert "empty token" in capsys.readouterr().err


def test_distill_reports_a_failed_run_and_points_at_the_partial_trace(seeded_project, claude_home,
                                                                     tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_distillation", _fake_run({}, boom=RuntimeError("planner exploded")))
    rc = cli.main(["distill", str(seeded_project), "--claude-home", str(claude_home),
                   "--trace-dir", str(tmp_path / "traces"), "--run-id", "demo"])
    assert rc == 1
    assert "planner exploded" in capsys.readouterr().err


def test_distill_exit_code_reports_a_run_level_problem(seeded_project, claude_home, tmp_path,
                                                       monkeypatch):
    plan = AssembledPlan(problems=["no plan was produced by this run"])
    monkeypatch.setattr(cli, "run_distillation", _fake_run({}, plan=plan))
    rc = cli.main(["distill", str(seeded_project), "--claude-home", str(claude_home),
                   "--trace-dir", str(tmp_path / "traces"), "--run-id", "demo"])
    assert rc == 1


# -- distill --include-subagents ---------------------------------------------------------------


@pytest.fixture
def seeded_with_subagents(seeded_project, claude_home):
    """The seeded project, plus two subagents beside its one session — one of them NESTED."""
    from tests.test_adapters_claude_code import FLAT_META, NESTED_META, WORKFLOW_RUN, write_subagent

    storage = project_storage_dir(seeded_project, home=claude_home)
    sub = {"type": "user", "message": {"role": "user", "content": "sub"}, "isSidechain": True}
    write_subagent(storage / "session-1" / "subagents", "flat1", FLAT_META, [sub])
    write_subagent(
        storage / "session-1" / "subagents" / "workflows" / WORKFLOW_RUN,
        "nested1", NESTED_META, [sub],
    )
    return seeded_project


def test_distill_ignores_subagents_unless_the_flag_is_passed(seeded_with_subagents, claude_home,
                                                             tmp_path, monkeypatch, capsys):
    captured: dict = {}
    monkeypatch.setattr(cli, "run_distillation", _fake_run(captured))
    cli.main(["distill", str(seeded_with_subagents), "--claude-home", str(claude_home),
              "--trace-dir", str(tmp_path / "traces"), "--run-id", "demo"])
    assert captured["adapter"].ingest().transcripts == ["user: hello"]
    assert "1 transcript(s)" in capsys.readouterr().out


def test_distill_with_the_flag_counts_and_ingests_both_kinds(seeded_with_subagents, claude_home,
                                                             tmp_path, monkeypatch, capsys):
    """The count must include subagents too — a project with 19 sessions and 351 subagents reporting
    "distilling 19 transcript(s)" would be describing a different run than the one it is starting."""
    captured: dict = {}
    monkeypatch.setattr(cli, "run_distillation", _fake_run(captured))
    cli.main(["distill", str(seeded_with_subagents), "--claude-home", str(claude_home),
              "--trace-dir", str(tmp_path / "traces"), "--run-id", "demo", "--include-subagents"])

    transcripts = captured["adapter"].ingest().transcripts
    assert len(transcripts) == 3
    # Line 0 shows an 8-character SHORT id (a disambiguator); line 1 carries the full one.
    assert transcripts[0].split("\n")[:2] == ["[0] session session-", "session=session-1"]
    assert "3 transcript(s)" in capsys.readouterr().out


def test_distill_warns_when_the_entry_count_passes_the_index_scan_ceiling(
    seeded_with_subagents, claude_home, tmp_path, monkeypatch, capsys
):
    """The ceiling is stated at the point of use rather than discovered inside a truncated scan.

    Both numbers come from one constant (`INDEX_LINE_MAX`), so the warning cannot claim a ceiling
    the header format does not actually imply. Squeezed here by shrinking the BUDGET rather than by
    seeding 460 subagents.
    """
    monkeypatch.setattr(cli, "run_distillation", _fake_run({}))
    monkeypatch.setenv("CD_MAX_OUTPUT_CHARS", "174")            # exactly 2 entries' worth
    cli.main(["distill", str(seeded_with_subagents), "--claude-home", str(claude_home),
              "--trace-dir", str(tmp_path / "traces"), "--run-id", "demo", "--include-subagents"])

    err = capsys.readouterr().err
    assert "3 transcript entries exceeds the ~2" in err
    assert "CD_MAX_OUTPUT_CHARS=174" in err


def test_distill_does_not_warn_below_the_ceiling(seeded_with_subagents, claude_home, tmp_path,
                                                 monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_distillation", _fake_run({}))
    cli.main(["distill", str(seeded_with_subagents), "--claude-home", str(claude_home),
              "--trace-dir", str(tmp_path / "traces"), "--run-id", "demo", "--include-subagents"])
    assert "exceeds" not in capsys.readouterr().err
