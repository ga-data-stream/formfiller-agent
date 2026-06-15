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
