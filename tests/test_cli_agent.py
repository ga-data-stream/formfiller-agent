from formfiller.cli import choose_pipeline


def test_choose_pipeline_deterministic():
    assert choose_pipeline("deterministic") == "deterministic"


def test_choose_pipeline_agent():
    assert choose_pipeline("agent") == "agent"


def test_build_agent_run_assembles_executor_and_loop(monkeypatch, tmp_path):
    from formfiller.agent.llm import LLMTurn
    import formfiller.cli as cli

    class _FakeLLM:
        def __init__(self, *a, **k): pass
        def respond(self, *, previous_response_id, input, tools):
            from formfiller.agent.models import ToolCall
            return LLMTurn(response_id="r1",
                           tool_calls=(ToolCall(call_id="c1", name="finish",
                                       arguments={"ready_to_submit": False, "summary": "stop"}),))

    monkeypatch.setattr(cli, "OpenAIResponsesAgentLLM", _FakeLLM)

    from formfiller.config import AppConfig, ProfileField
    cfg = AppConfig(excel_log_path=str(tmp_path / "l.xlsx"), traces_dir=str(tmp_path / "t"),
                    fill_strategy="agent", azure_openai_deployment="dep")
    profile = (ProfileField(name="siren", value="123", aliases=()),)

    run = cli.build_agent_run(client=object(), config=cfg, profile=profile)

    class _Trace:
        def write(self, r): pass
    class _Page:
        def goto(self, *a, **k): pass
        def evaluate(self, js): return {"url": "u", "title": "t", "elements": [],
                                        "has_captcha_frame": False}

    outcome = run(page=_Page(), url="https://forms.office.com/r/x", config=cfg,
                  profile=profile, trace=_Trace())
    assert outcome.status == "review"   # finish(ready=False) → review


def test_build_agent_run_writes_decisions_log(monkeypatch, tmp_path):
    # When the agent maps the form (lookup_profile), the run writes a per-form
    # markdown reasoning log to decisions_dir, keyed by the trace run id.
    from formfiller.agent.llm import LLMTurn
    from formfiller.agent.models import ToolCall
    import formfiller.cli as cli

    def _fake_map_and_verify(client, deployment, schema, profile, verify=True,
                             max_output_tokens=16000, reasoning_effort="medium",
                             verifier_deployment="", verifier_reasoning_effort=None):
        from formfiller.models import (
            MappedAnswer, MappingResult, MappingOutcome, DecisionRecord,
        )
        res = MappingResult(answers=(MappedAnswer(
            question_id="q1", profile_field="siren", value="123",
            confidence=0.9, status="matched", rationale="r"),))
        rec = DecisionRecord(
            question_id="q1", label="SIREN", type="text", required=True,
            profile_field="siren", value="123", propose_status="matched",
            propose_confidence=0.9, propose_rationale="p", final_status="matched",
            final_confidence=0.9, verify_rationale="v", final_action="fill")
        return MappingOutcome(result=res, decisions=(rec,))

    # patch BEFORE build_agent_run runs (it imports map_and_verify at call time)
    monkeypatch.setattr("formfiller.field_mapper.map_and_verify", _fake_map_and_verify)

    class _FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        def respond(self, *, previous_response_id, input, tools):
            self.n += 1
            if self.n == 1:
                return LLMTurn(response_id="r1", tool_calls=(
                    ToolCall(call_id="c1", name="lookup_profile", arguments={}),))
            return LLMTurn(response_id="r2", tool_calls=(
                ToolCall(call_id="c2", name="finish",
                         arguments={"ready_to_submit": False, "summary": "done"}),))

    monkeypatch.setattr(cli, "OpenAIResponsesAgentLLM", _FakeLLM)

    from formfiller.config import AppConfig, ProfileField
    cfg = AppConfig(excel_log_path=str(tmp_path / "l.xlsx"), traces_dir=str(tmp_path / "t"),
                    decisions_dir=str(tmp_path / "decisions"),
                    fill_strategy="agent", azure_openai_deployment="dep")
    profile = (ProfileField(name="siren", value="123", aliases=()),)
    run = cli.build_agent_run(client=object(), config=cfg, profile=profile)

    class _Trace:
        run_id = "E1"
        def write(self, r): pass

    class _Loc:
        def count(self): return 0

    class _Page:
        def goto(self, *a, **k): pass
        def locator(self, sel): return _Loc()
        def evaluate(self, js):
            if "querySelectorAll('label')" in js:   # generic _EXTRACT_JS
                return {"title": "Fake Form", "recs": []}
            return {"url": "u", "title": "t", "elements": [],
                    "has_captcha_frame": False}

    # non-MS url so schema_from_page uses the generic extractor (no FormRenderError)
    run(page=_Page(), url="https://example.com/form", config=cfg,
        profile=profile, trace=_Trace())

    log = tmp_path / "decisions" / "E1.md"
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "SIREN" in text and "siren" in text


def test_build_hooks_passes_verifier_config_to_map(monkeypatch, tmp_path):
    # The deterministic hook's mapper must forward the verifier model + effort
    # from config to map_and_verify.
    import formfiller.cli as cli
    from formfiller.config import AppConfig, ProfileField
    from formfiller.models import MappingResult, MappingOutcome, FormSchema

    captured = {}

    def _capture(client, deployment, schema, profile, verify=True,
                 max_output_tokens=16000, reasoning_effort="medium",
                 verifier_deployment="", verifier_reasoning_effort=None):
        captured.update(
            deployment=deployment, reasoning_effort=reasoning_effort,
            verifier_deployment=verifier_deployment,
            verifier_reasoning_effort=verifier_reasoning_effort,
        )
        return MappingOutcome(result=MappingResult(answers=()), decisions=())

    # Patch BEFORE _build_hooks runs (it does `from ... import map_and_verify` at call time).
    monkeypatch.setattr("formfiller.field_mapper.map_and_verify", _capture)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://r.services.ai.azure.com")

    cfg = AppConfig(excel_log_path=str(tmp_path / "l.xlsx"),
                    azure_openai_deployment="main-dep", reasoning_effort="low",
                    verifier_model_deployment="verify-dep",
                    verifier_reasoning_effort="high")
    profile = (ProfileField(name="siren", value="123"),)
    hooks = cli._build_hooks(cfg, profile)

    schema = FormSchema(url="https://forms.office.com/r/x", title="T", questions=())
    hooks.map_fields(schema)

    assert captured["deployment"] == "main-dep"
    assert captured["reasoning_effort"] == "low"
    assert captured["verifier_deployment"] == "verify-dep"
    assert captured["verifier_reasoning_effort"] == "high"


def test_build_agent_run_accepts_confirm_param():
    import inspect
    from formfiller.cli import build_agent_run
    params = inspect.signature(build_agent_run).parameters
    assert "confirm" in params   # paramétrable pour le mode non-interactif
