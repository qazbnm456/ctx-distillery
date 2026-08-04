"""`ctx_distillery_eval.judge` — the strict 0-10 x 4 validator, the three unscored paths, the stub.

Parity pass 4 built the LIVE judge that `eval/pyproject.toml`'s `judge = ["openai>=1.0"]` extra had
been promising (and nothing had been importing) since this package existed. This file is its gate,
and it is deliberately the deepest test module in this suite — mirroring toolscout's, the deepest of
the three siblings' — because the live path is the one path CI can never actually exercise.

**Every model call here goes through an INJECTED `chat_fn`.** No network, no monkeypatching of
`openai`, no `openai` installed. That is not a testing convenience: `make_eval_judge(chat_fn=...)` is
a real, documented seam (it is also how a non-OpenAI provider would be wired), so testing through it
exercises the same code path a live run takes, minus the socket. The one thing an injected callable
cannot check — that `openai` is not imported at module scope — is checked structurally instead, by
parsing this module's own AST (see the last test).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import ctx_distillery_eval.judge as judge_mod
from ctx_distillery_eval.judge import (
    JUDGE_MAX_PLAN_CHARS,
    JUDGE_MAX_TOTAL_CHARS,
    JUDGE_MAX_TRANSCRIPT_CHARS,
    PROMPT_VERSION,
    EvalJudgeConfig,
    JudgeVerdict,
    StubJudge,
    build_prompt,
    make_eval_judge,
    parse_eval_json,
)

GOOD = json.dumps({"scores": {"TF": 7, "TA": 5, "TG": 6, "PA": 8}, "notes": "fine"})

PLAN_TEXT = "candidate 0: action=promote_to_memory artifact_id=abc123"
TRANSCRIPTS = ["transcript A content", "transcript B content"]


# -- the validator -------------------------------------------------------------------------------


def test_validator_parses_good_json():
    v = parse_eval_json(GOOD)
    assert v.ok
    assert v.scores == {"TF": 7.0, "TA": 5.0, "TG": 6.0, "PA": 8.0}
    assert v.notes == "fine"


def test_validator_clamps_out_of_range_to_0_10():
    """A judge that says 15 clearly meant "the top of the scale" — clamp, don't discard the run."""
    v = parse_eval_json(json.dumps({"scores": {"TF": 15, "TA": -3, "TG": 5, "PA": 10.5}}))
    assert v.ok
    assert v.scores == {"TF": 10.0, "TA": 0.0, "TG": 5.0, "PA": 10.0}


def test_validator_rejects_a_missing_category():
    """A missing category is NOT defaulted to 5 — the run lands unscored, never a guessed score."""
    v = parse_eval_json(json.dumps({"scores": {"TF": 5, "TA": 5, "TG": 5}}))
    assert not v.ok and any("PA" in e for e in v.errors)


def test_validator_rejects_non_numeric_and_bool_scores():
    """`bool` is checked BEFORE the numeric test, and the ORDER is the whole point of this test:
    `bool` subclasses `int` in Python, so `isinstance(True, int)` is True and a `{"TF": true}` reply
    would otherwise clamp to a real-looking score of 1.0."""
    assert not parse_eval_json(json.dumps({"scores": {"TF": "high", "TA": 5, "TG": 5, "PA": 5}})).ok
    assert not parse_eval_json(json.dumps({"scores": {"TF": True, "TA": 5, "TG": 5, "PA": 5}})).ok
    assert not parse_eval_json(json.dumps({"scores": {"TF": None, "TA": 5, "TG": 5, "PA": 5}})).ok


def test_validator_rejects_non_json_and_a_missing_scores_object():
    assert not parse_eval_json("the plan was fine, 8/10").ok
    assert not parse_eval_json("").ok
    assert not parse_eval_json(json.dumps({"TF": 5, "TA": 5, "TG": 5, "PA": 5})).ok  # no `scores`


def test_validator_tolerates_fences_and_extra_fields():
    """A ```-fenced reply, a fifth category this package does not read, and an unknown top-level
    field are all fine — tolerance costs nothing here, and none of them is a missing number."""
    fenced = "```json\n" + json.dumps({
        "scores": {"TF": 4, "TA": 4, "TG": 4, "PA": 4, "redaction_fidelity": 9},
        "notes": "n", "confidence": 0.3}) + "\n```"
    v = parse_eval_json(fenced)
    assert v.ok and set(v.scores) == {"TF", "TA", "TG", "PA"}


def test_validator_truncates_a_runaway_notes_field():
    v = parse_eval_json(json.dumps({"scores": {"TF": 5, "TA": 5, "TG": 5, "PA": 5}, "notes": "x" * 9000}))
    assert v.ok and len(v.notes) == 2000


# -- the prompt ----------------------------------------------------------------------------------


def test_build_prompt_carries_the_plan_and_every_transcript():
    prompt = build_prompt(PLAN_TEXT, TRANSCRIPTS)
    assert PLAN_TEXT in prompt
    assert "transcript A content" in prompt and "transcript B content" in prompt
    assert "--- transcript 0 ---" in prompt and "--- transcript 1 ---" in prompt
    for question in judge_mod.JUDGE_QUESTIONS.values():
        assert question in prompt  # the rendered wording is the DATA in JUDGE_QUESTIONS, not a paraphrase


def test_build_prompt_fences_untrusted_data_and_states_the_output_contract():
    """Both bodies the judge reads are untrusted (somebody else's session; raw model output), and a
    live judge that is not told what JSON to emit is a judge whose every reply is off-schema."""
    prompt = build_prompt(PLAN_TEXT, TRANSCRIPTS)
    assert "UNTRUSTED DATA" in prompt
    assert "Return STRICT JSON and nothing else" in prompt
    assert '"scores"' in prompt


def test_build_prompt_states_the_absence_when_no_transcripts_are_supplied():
    """Defensive only — `cli._read_transcripts` refuses an empty transcript before this is reached —
    but an empty section must read as "none", never as a section that merely looks truncated."""
    assert "(none supplied)" in build_prompt(PLAN_TEXT, [])


# -- the length caps (v3) --------------------------------------------------------------------------


def test_an_input_under_every_cap_renders_exactly_as_it_did_before_the_caps():
    """The caps are provenance, not a rewrite: nothing under budget may move by a byte, which is
    what keeps every existing prompt assertion in this file meaningful."""
    prompt = build_prompt(PLAN_TEXT, TRANSCRIPTS)
    assert f"--- transcript 0 ---\n{TRANSCRIPTS[0]}" in prompt
    assert "characters omitted" not in prompt
    assert PLAN_TEXT in prompt


def test_an_over_long_transcript_is_elided_head_and_tail_with_a_VISIBLE_marker():
    """dspy's own `REPLEntry.format_output` convention, deliberately — a judge should see the same
    elision marker the planner does, and a silent truncation reads as the end of the input."""
    huge = "A" * (JUDGE_MAX_TRANSCRIPT_CHARS + 5_000) + "TAIL"
    prompt = build_prompt(PLAN_TEXT, [huge])

    assert "... (5004 characters omitted) ..." in prompt
    assert "TAIL" in prompt                                  # the TAIL really survived, not just the head
    assert len(prompt) < len(huge)
    # The exact accounting, on the helper (the prompt's own fixed text contains "A"s, so counting
    # characters across the whole prompt would be measuring the wrong thing): head + tail total
    # exactly the cap, and the marker is the only thing added.
    elided = judge_mod._elide(huge, JUDGE_MAX_TRANSCRIPT_CHARS)
    assert elided in prompt
    assert len(elided) == JUDGE_MAX_TRANSCRIPT_CHARS + len("\n... (5004 characters omitted) ...\n")


def test_an_over_long_PLAN_is_capped_too_because_it_is_model_controlled():
    """`build_prompt`'s FIRST argument is `render_plan` output over a plan whose candidate count and
    drafted-body sizes are BOTH model-controlled. Capping only the transcripts closes the instance
    and leaves the surface open."""
    huge_plan = "P" * (JUDGE_MAX_PLAN_CHARS + 1_000)
    prompt = build_prompt(huge_plan, TRANSCRIPTS)
    assert "... (1000 characters omitted) ..." in prompt
    assert judge_mod._elide(huge_plan, JUDGE_MAX_PLAN_CHARS) in prompt
    assert huge_plan not in prompt


def test_entries_past_the_TOTAL_budget_become_index_preserving_stubs():
    """**The stub is mandatory, not an optimisation.** `--- transcript {i} ---` labels are how the
    judge refers to an entry and how a reader pairs a verdict back to one, so an entry past the
    budget must still APPEAR. Dropping entries instead would renumber every entry after it.
    """
    big = "B" * JUDGE_MAX_TRANSCRIPT_CHARS
    entries = [big] * (JUDGE_MAX_TOTAL_CHARS // JUDGE_MAX_TRANSCRIPT_CHARS + 2)
    prompt = build_prompt(PLAN_TEXT, entries)

    for index in range(len(entries)):
        assert f"--- transcript {index} " in prompt or f"--- transcript {index} ---" in prompt
    assert f"--- transcript {len(entries) - 1} (elided: {len(big)} chars) ---" in prompt
    assert len(prompt) < JUDGE_MAX_TOTAL_CHARS + JUDGE_MAX_PLAN_CHARS + 10_000


def test_a_short_entry_after_the_budget_is_exhausted_is_still_rendered_in_full():
    """The budget bounds BULK, not entry count: an entry that fits in what is left is rendered."""
    entries = ["B" * JUDGE_MAX_TRANSCRIPT_CHARS] * 3 + ["a short but important one"]
    prompt = build_prompt(PLAN_TEXT, entries)
    assert "--- transcript 3 ---\na short but important one" in prompt


# -- the judge-only reference slot (the taskset pass) ----------------------------------------------

REFERENCE = "Expected plan: promote the merge-freeze rule; prune the debugging session."


def test_build_prompt_renders_the_reference_section_when_there_is_one():
    prompt = build_prompt(PLAN_TEXT, TRANSCRIPTS, REFERENCE)
    assert "=== REFERENCE (judge-only; the planner never saw this) ===" in prompt
    assert REFERENCE in prompt


def test_build_prompt_omits_the_reference_section_entirely_when_there_is_none():
    """THE decision this pass made explicitly, where the siblings render an unconditional
    "(no reference provided; …)" fallback: every sibling ALWAYS has a taskset, and this package's
    primary path (`score` without `--taskset`) usually does not. So the no-reference prompt must be
    byte-identical to `atlas-ctxd-eval-v1`'s rather than gaining a permanent placeholder line."""
    prompt = build_prompt(PLAN_TEXT, TRANSCRIPTS)
    assert "REFERENCE" not in prompt
    assert "no reference provided" not in prompt
    assert prompt == build_prompt(PLAN_TEXT, TRANSCRIPTS, "")
    assert prompt == build_prompt(PLAN_TEXT, TRANSCRIPTS, "   \n ")  # whitespace-only is absent too


def test_the_untrusted_data_fence_accounts_for_the_reference_only_when_it_is_rendered():
    """`UNTRUSTED_DATA_RULE` enumerates exactly two bodies ("The PLAN and the TRANSCRIPT(S) below").
    A third section appearing under it unaccounted for would leave the fence ambiguous about the one
    thing it does not name — so the trust sentence rides along with the section, and only with it.

    And it says TRUSTED, deliberately: a taskset reference is human-authored, reviewed in a diff, and
    is the only body in this prompt that is neither model output nor a recording of someone else's
    session. That is a different provenance from the other two, and the fence must not flatten them.
    """
    with_ref = build_prompt(PLAN_TEXT, TRANSCRIPTS, REFERENCE)
    without = build_prompt(PLAN_TEXT, TRANSCRIPTS)
    assert judge_mod.UNTRUSTED_DATA_RULE in with_ref and judge_mod.UNTRUSTED_DATA_RULE in without
    assert judge_mod.REFERENCE_TRUST_RULE.strip() in with_ref
    assert judge_mod.REFERENCE_TRUST_RULE.strip() not in without
    assert "TRUSTED input" in with_ref


def test_every_judge_in_this_package_accepts_the_third_argument():
    """The `Judge` protocol widened to three positional arguments, so all three implementations had
    to widen identically — `score.score_run` passes the reference POSITIONALLY."""
    assert StubJudge()(PLAN_TEXT, TRANSCRIPTS, REFERENCE).ok
    live = make_eval_judge(EvalJudgeConfig(), chat_fn=lambda prompt: GOOD)
    assert live(PLAN_TEXT, TRANSCRIPTS, REFERENCE).ok


def test_the_live_judge_forwards_the_reference_into_the_rendered_prompt():
    seen = {}

    def chat(prompt: str) -> str:
        seen["prompt"] = prompt
        return GOOD

    make_eval_judge(EvalJudgeConfig(), chat_fn=chat)(PLAN_TEXT, TRANSCRIPTS, REFERENCE)
    assert seen["prompt"] == build_prompt(PLAN_TEXT, TRANSCRIPTS, REFERENCE)
    assert REFERENCE in seen["prompt"]


# -- the three unscored paths, and the happy one ---------------------------------------------------


def test_make_eval_judge_scores_via_an_injected_chat_fn():
    judge = make_eval_judge(EvalJudgeConfig(), chat_fn=lambda prompt: GOOD)
    verdict = judge(PLAN_TEXT, TRANSCRIPTS)
    assert isinstance(verdict, JudgeVerdict) and verdict.ok
    assert verdict.score is not None
    assert verdict.score.TF == 7.0 and verdict.score.notes == "fine"


def test_the_judge_sends_the_rendered_prompt_not_a_paraphrase():
    seen = {}

    def chat(prompt: str) -> str:
        seen["prompt"] = prompt
        return GOOD

    make_eval_judge(EvalJudgeConfig(), chat_fn=chat)(PLAN_TEXT, TRANSCRIPTS)
    assert seen["prompt"] == build_prompt(PLAN_TEXT, TRANSCRIPTS)


def test_a_never_valid_json_reply_degrades_to_an_off_schema_unscored_verdict():
    """The commonest live failure by far — a chatty model that narrates instead of emitting JSON.
    It must produce `ok=False` with a reason naming the schema, NEVER an exception and NEVER a 0."""
    judge = make_eval_judge(EvalJudgeConfig(), chat_fn=lambda prompt: "Overall I'd say this plan is solid.")
    verdict = judge(PLAN_TEXT, TRANSCRIPTS)
    assert not verdict.ok and verdict.score is None
    assert verdict.reason.startswith("judge output off-schema: ")


def test_an_endpoint_error_yields_an_unscored_verdict_never_a_fake_zero():
    def chat(prompt: str) -> str:
        raise RuntimeError("connection refused")

    judge = make_eval_judge(EvalJudgeConfig(transient_retries=0), chat_fn=chat)
    verdict = judge(PLAN_TEXT, TRANSCRIPTS)
    assert not verdict.ok and verdict.score is None
    assert verdict.reason == "judge endpoint error: connection refused"


def test_the_circuit_breaker_short_circuits_a_hopeless_judge():
    """The breaker's state lives in `make_model_tool`'s closure, which is why `cli._pick_judge`
    builds ONE judge per batch. The load-bearing assertion is the LAST one: after the breaker trips,
    the model is not called again — a systematically off-schema judge stops costing money mid-glob
    instead of being paid for once per trace."""
    calls = {"n": 0}

    def chat(prompt: str) -> str:
        calls["n"] += 1
        return "not json"

    judge = make_eval_judge(EvalJudgeConfig(max_consecutive_invalid=2), chat_fn=chat)
    assert not judge(PLAN_TEXT, TRANSCRIPTS).ok
    assert not judge(PLAN_TEXT, TRANSCRIPTS).ok
    third = judge(PLAN_TEXT, TRANSCRIPTS)
    assert not third.ok and third.reason == "judge circuit breaker: too many unusable replies in a row"
    assert calls["n"] == 2  # the third call never reached the model


def test_a_recovering_judge_resets_the_breaker():
    """The counter resets on any valid reply (rlm-harness's contract) — one bad reply mid-batch must not
    doom the runs after it."""
    replies = iter(["not json", GOOD, "not json", GOOD])
    judge = make_eval_judge(EvalJudgeConfig(max_consecutive_invalid=2), chat_fn=lambda p: next(replies))
    assert not judge(PLAN_TEXT, TRANSCRIPTS).ok
    assert judge(PLAN_TEXT, TRANSCRIPTS).ok
    assert not judge(PLAN_TEXT, TRANSCRIPTS).ok
    assert judge(PLAN_TEXT, TRANSCRIPTS).ok


# -- the stub, the config, the pins -----------------------------------------------------------------


def test_stub_judge_is_deterministic_offline_and_returns_a_verdict():
    """The shape guard for pass 4: `StubJudge` used to return a bare `EvalScore`. It must now satisfy
    the same `Judge` protocol the live judge does, or `score_run` would branch on `.ok` of a score."""
    stub = StubJudge()
    a = stub(PLAN_TEXT, TRANSCRIPTS)
    b = stub("an entirely different plan", ["an entirely different transcript"])
    assert isinstance(a, JudgeVerdict) and a.ok and b.ok
    assert a.score == b.score
    assert "stub" in a.score.notes  # the notes say plainly that it is not a model verdict


def test_stub_judge_scores_are_parameterizable():
    """Why this stayed a CLASS where the siblings expose a module-level `stub_judge` function:
    `tests/test_score.py`'s means test needs two rows with DIFFERENT scores."""
    verdict = StubJudge(tf=8, ta=6, tg=7, pa=9)(PLAN_TEXT, TRANSCRIPTS)
    assert (verdict.score.TF, verdict.score.TA, verdict.score.TG, verdict.score.PA) == (8, 6, 7, 9)


def test_config_reads_the_cdeval_env(monkeypatch):
    """`CDEVAL_*`, matching the family convention (`TSEVAL_`/`CREVAL_`/`DSEVAL_`) and deliberately
    NOT sharing the root package's `CD_*` surface — the judge must be pointable at a different model,
    endpoint and key from the run it is scoring."""
    monkeypatch.setenv("CDEVAL_MODEL", "judge-model-x")
    monkeypatch.setenv("CDEVAL_BASE_URL", "http://localhost:9")
    monkeypatch.setenv("CDEVAL_API_KEY", "k")
    monkeypatch.setenv("CDEVAL_TIMEOUT", "12.5")
    c = EvalJudgeConfig.from_env()
    assert (c.model, c.base_url, c.api_key, c.timeout) == ("judge-model-x", "http://localhost:9", "k", 12.5)


def test_config_defaults_to_no_live_judge_and_a_60s_hard_timeout():
    """An empty `model` is the signal `cli._pick_judge` reads to fall back to the stub, so the
    offline path is the DEFAULT rather than an opt-in. (`conftest._offline_judge_env` guarantees the
    `CDEVAL_*` surface is unset here even on a live-configured developer machine.)"""
    c = EvalJudgeConfig.from_env()
    assert c.model == "" and c.base_url is None and c.api_key == "" and c.timeout == 60.0


def test_a_nan_score_is_refused_rather_than_silently_awarded_full_marks():
    """NaN is a WORSE bug than the bool case above, and the clamp is what makes it worse.

    Reported from a sibling project and reproduced HERE before fixing. Two layers conspire:
    `isinstance(nan, (int, float))` is True (NaN *is* a float, so the type gate passes it), and then
    EVERY comparison with NaN is False — so `min(10.0, nan)` returns 10.0 and `max(0.0, 10.0)`
    returns 10.0. The clamp does not merely fail to reject NaN, it AWARDS FULL MARKS. Measured on
    this module before the fix: `_clamp(float("nan")) == 10.0`.

    The trigger is real. `json.loads` accepts a bare `NaN` literal by default (it is not standard
    JSON, but Python's decoder emits `float('nan')` for it), so a provider's JSON mode does not stop
    it. And the damage propagates: one poisoned row dragged a two-row TF mean from a truthful 2.0 to
    6.0, because `compute_means` cannot tell a fabricated 10 from a real one.

    This contradicted this module's own stated position — "there is no default-to-5 anywhere: the run
    lands `unscored`, never a guessed score". A NaN was not landing on 5. It was landing on 10.

    `inf` is deliberately NOT special-cased: it clamps to 10.0, which is the correct reading of
    "larger than the top of the scale". Only NaN is meaningless, and only NaN defeats the clamp.
    """
    import math

    assert math.isnan(json.loads('{"x": NaN}')["x"]), "the trigger path itself must still be real"

    # Note `-NaN` is NOT a second case to add here: Python's decoder accepts `NaN`, `Infinity` and
    # `-Infinity` but rejects `-NaN` outright, so it would fail at the JSON layer and never reach
    # the check under test — a hollow assertion dressed as extra coverage.
    for payload in (
        '{"scores": {"TF": NaN, "TA": 5, "TG": 5, "PA": 5}}',
        '{"scores": {"TF": 5, "TA": 5, "TG": 5, "PA": NaN}}',
    ):
        v = parse_eval_json(payload)
        assert v.ok is False, "a NaN score must be refused, never clamped to full marks"
        assert any("NaN" in e for e in v.errors), v.errors
        assert v.scores == {}, "no partial scores survive a refused parse"

    # ...and the neighbouring infinities still CLAMP, which is the right answer for them.
    fine = parse_eval_json('{"scores": {"TF": Infinity, "TA": -Infinity, "TG": 5, "PA": 5}}')
    assert fine.ok is True
    assert (fine.scores["TF"], fine.scores["TA"]) == (10.0, 0.0)


def test_prompt_version_is_pinned():
    """Pinned so a scorecard's number is attributable to the prompt that produced it. Bumping this is
    the deliberate cost of changing `build_prompt`.

    **v1 -> v2, and this assertion was updated DELIBERATELY, not to make a red test green.** The v1
    docstring here named the judge-only `reference` slot as the change that would have to bump the
    constant; the taskset pass added it (`build_prompt`'s third argument, the `=== REFERENCE ===`
    section, and `REFERENCE_TRUST_RULE`), so it bumped. Note the subtlety this pin cannot express and
    the next test can: with NO reference the rendered v2 prompt is byte-identical to v1's. The
    version still moves, because provenance is per-REPORT — a scorecard cannot say "v1 for some rows,
    v2 for others".

    **v2 -> v3, updated on the same deliberate footing.** `build_prompt` gained the length caps
    (`JUDGE_MAX_PLAN_CHARS` / `JUDGE_MAX_TRANSCRIPT_CHARS` / `JUDGE_MAX_TOTAL_CHARS`), which is an
    ASSEMBLY change and squarely inside this constant's contract. The same subtlety applies again:
    an input under every cap renders byte-identically to v2 (the test below pins that), and the
    version moves anyway. What did NOT move it is worth recording here too, because it is the more
    likely future mistake: feeding the judge a different SET of transcripts — subagent transcripts
    included, say — changes no line of the prompt's text and must never bump this constant. That
    comparability is per-row and lives in the trace's own `transcript_index`.
    """
    assert PROMPT_VERSION == "atlas-ctxd-eval-v3"


def test_openai_is_imported_lazily_inside_the_chat_closure_never_at_module_level():
    """The structural half of `eval/tests/test_boundary.py`'s fresh-subprocess check, done here by
    AST so the failure names the offending line rather than a subprocess's stderr.

    The second assertion keeps this test honest: deleting the live client entirely would satisfy the
    first assertion vacuously, so the file must still CONTAIN the lazy import it is asserting about.
    """
    source = Path(judge_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = [
        node.lineno
        for node in tree.body  # TOP LEVEL only — a nested import is exactly what is wanted
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and "openai" in (getattr(node, "module", None) or "") + "".join(a.name for a in node.names)
    ]
    assert offenders == [], f"`openai` must not be imported at module scope; found at line(s) {offenders}"
    assert "from openai import OpenAI" in source  # ...and the lazy import must still exist
