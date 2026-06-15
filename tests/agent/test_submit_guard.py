from formfiller.agent.models import ToolCall
from formfiller.agent.tools import ToolExecutor
from formfiller.models import FormQuestion, FormSchema, MappingResult, MappedAnswer, QuestionType
import formfiller.agent.tools as tools_mod


_SCHEMA = FormSchema(
    url="https://forms.office.com/r/x", title="Form",
    questions=(FormQuestion(id="ms:0", label="SIREN", type=QuestionType.TEXT, required=True),),
)
_GOOD = MappingResult(answers=(MappedAnswer(question_id="ms:0", profile_field="siren",
        value="123456789", confidence=0.95, status="matched"),))
_BAD = MappingResult(answers=(MappedAnswer(question_id="ms:0", profile_field=None,
        value=None, confidence=0.0, status="no_data"),))


class _Page:
    def title(self):
        return "Form"


def _executor(mapping, dry_run, confirm, monkeypatch):
    monkeypatch.setattr(tools_mod, "fill_form", lambda page, instr: None)
    monkeypatch.setattr(tools_mod, "take_screenshot", lambda page: b"\x89PNG")
    monkeypatch.setattr(tools_mod, "submit_form", lambda page, dry_run: True)
    return ToolExecutor(page=_Page(), url="https://forms.office.com/r/x",
                        schema_reader=lambda: _SCHEMA, mapper=lambda s: mapping,
                        threshold=0.8, dry_run=dry_run, confirm=confirm)


def test_gate_review_refuses_submit(monkeypatch):
    ex = _executor(_BAD, dry_run=False, confirm=lambda s: True, monkeypatch=monkeypatch)
    res = ex.dispatch(ToolCall(call_id="c", name="submit", arguments={"summary": "go"}))
    assert res.terminal == "review"
    assert res.screenshot == b"\x89PNG"
    assert res.schema is not None and res.mapping is not None


def test_dry_run_does_not_submit(monkeypatch):
    submitted = {"called": False}
    monkeypatch.setattr(tools_mod, "submit_form",
                        lambda page, dry_run: submitted.__setitem__("called", True))
    ex = _executor(_GOOD, dry_run=True, confirm=lambda s: True, monkeypatch=monkeypatch)
    res = ex.dispatch(ToolCall(call_id="c", name="submit", arguments={"summary": "go"}))
    assert res.terminal == "dry_run"
    assert submitted["called"] is False
    assert res.screenshot == b"\x89PNG"


def test_real_submit_requires_confirmation(monkeypatch):
    ex = _executor(_GOOD, dry_run=False, confirm=lambda s: False, monkeypatch=monkeypatch)
    res = ex.dispatch(ToolCall(call_id="c", name="submit", arguments={"summary": "go"}))
    assert res.terminal == "review"
    assert "declin" in res.reason.lower() or "confirm" in res.reason.lower()


def test_confirmed_submit_is_terminal_submitted(monkeypatch):
    ex = _executor(_GOOD, dry_run=False, confirm=lambda s: True, monkeypatch=monkeypatch)
    res = ex.dispatch(ToolCall(call_id="c", name="submit", arguments={"summary": "go"}))
    assert res.terminal == "submitted"
