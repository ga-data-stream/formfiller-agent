import pytest

from formfiller.agent.models import ToolCall
from formfiller.agent.tools import ToolExecutor, TOOL_SCHEMAS
from formfiller.models import FormQuestion, FormSchema, MappingResult, MappedAnswer, QuestionType


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        return 1

    def click(self, **kw):
        self.page.calls.append(("click", self.selector))

    def fill(self, text, **kw):
        self.page.calls.append(("fill", self.selector, text))

    def select_option(self, label=None, **kw):
        self.page.calls.append(("select", self.selector, label))


class FakePage:
    def __init__(self, raw=None, title="Form", url="https://forms.office.com/r/x"):
        self.calls = []
        self._raw = raw or {"url": url, "title": title, "has_captcha_frame": False,
                            "elements": [{"ref": "e0", "role": "button", "name": "Next",
                                          "value": "", "type": "", "state": {}}]}
        self._title = title
        self._url = url

    def evaluate(self, js):
        if "scrollBy" in js:
            self.calls.append(("scroll",))
            return None
        return self._raw

    def locator(self, selector):
        return FakeLocator(self, selector)

    def title(self):
        return self._title

    def goto(self, url, **kw):
        self.calls.append(("goto", url))

    def get_by_role(self, role, name=None):
        return FakeLocator(self, f"role={role}")


_SCHEMA = FormSchema(
    url="https://forms.office.com/r/x", title="Form",
    questions=(FormQuestion(id="ms:0", label="SIREN", type=QuestionType.TEXT, required=True),),
)


def _executor(page, mapper=None):
    return ToolExecutor(
        page=page, url="https://forms.office.com/r/x",
        schema_reader=lambda: _SCHEMA,
        mapper=mapper or (lambda schema: MappingResult(answers=(
            MappedAnswer(question_id="ms:0", profile_field="siren", value="123456789",
                         confidence=0.95, status="matched"),))),
        threshold=0.8, dry_run=True, confirm=lambda s: False,
    )


def test_tool_schemas_have_required_keys():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {"read_snapshot", "extract_questions", "lookup_profile", "answer_question",
            "click", "fill", "select_choice", "scroll", "navigate_next", "goto",
            "detect_blocker", "request_human", "submit", "finish"} <= names
    for t in TOOL_SCHEMAS:
        assert t["type"] == "function"
        assert "parameters" in t and t["parameters"]["type"] == "object"


def test_read_snapshot_tool():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="read_snapshot", arguments={}))
    assert res.terminal is None
    assert res.output["elements"][0]["ref"] == "e0"


def test_extract_questions_tool():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="extract_questions", arguments={}))
    assert res.output["questions"][0]["id"] == "ms:0"


def test_answer_question_calls_fill_form(monkeypatch):
    captured = {}
    import formfiller.agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "fill_form",
                        lambda page, instr: captured.setdefault("instr", list(instr)))
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="answer_question",
                               arguments={"question_id": "ms:0", "value": "123456789"}))
    assert res.output["status"] == "filled"
    assert captured["instr"][0].question_id == "ms:0"
    assert captured["instr"][0].value == "123456789"


def test_click_primitive_uses_ref_selector():
    page = FakePage()
    ex = _executor(page)
    ex.dispatch(ToolCall(call_id="c", name="click", arguments={"ref": "e0"}))
    assert ("click", '[data-ff-ref="e0"]') in page.calls


def test_request_human_is_terminal_review():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="request_human",
                               arguments={"reason": "captcha"}))
    assert res.terminal == "review"
    assert "captcha" in res.reason


def test_finish_not_ready_is_terminal_review():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="finish",
                               arguments={"ready_to_submit": False, "summary": "nothing to do"}))
    assert res.terminal == "review"


def test_unknown_tool_returns_error_not_raises():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="nope", arguments={}))
    assert res.terminal is None
    assert "error" in res.output
