import pytest
from formfiller.config import ProfileField
from formfiller.models import QuestionType, FormQuestion, FormSchema
from formfiller.field_mapper import map_fields, LLMMapping, LLMMappedAnswer


# Stub mimics the openai Responses API: client.responses.parse(...) returns an
# object whose .output_parsed is the validated Pydantic model.
class _StubResponse:
    def __init__(self, parsed):
        self.output_parsed = parsed
        self.status = "completed"


class _StubResponses:
    def __init__(self, parsed):
        self._parsed = parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _StubResponse(self._parsed)


class _StubClient:
    def __init__(self, parsed):
        self.responses = _StubResponses(parsed)


def _schema():
    return FormSchema(
        url="https://forms.office.com/r/x", title="Vendor",
        questions=(
            FormQuestion(id="q1", label="Company name", type=QuestionType.TEXT, required=True),
            FormQuestion(id="q2", label="Random one-off", type=QuestionType.TEXT, required=False),
        ),
    )


def _profile():
    return (
        ProfileField(name="company_legal_name", value="Ginesis Finance SAS", aliases=("company name",)),
    )


def test_map_fields_returns_mapping_result_from_llm_output():
    parsed = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.95, status="matched"),
        LLMMappedAnswer(question_id="q2", profile_field=None, value=None,
                        confidence=0.0, status="no_data"),
    ])
    client = _StubClient(parsed)
    result = map_fields(client, "gpt-5.4-nano", _schema(), _profile())
    assert result.by_id("q1").value == "Ginesis Finance SAS"
    assert result.by_id("q1").confidence == 0.95
    assert result.by_id("q2").status == "no_data"


def test_map_fields_passes_deployment_and_text_format():
    parsed = LLMMapping(answers=[])
    client = _StubClient(parsed)
    map_fields(client, "gpt-5.4-nano", _schema(), _profile())
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.4-nano"
    assert call["text_format"] is LLMMapping
    # system prompt goes via `instructions`, the form payload via `input`
    assert "instructions" in call and "input" in call
