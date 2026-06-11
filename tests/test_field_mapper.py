import pytest
from formfiller.config import ProfileField
from formfiller.models import QuestionType, FormQuestion, FormSchema
from formfiller.field_mapper import map_fields, LLMMapping, LLMMappedAnswer


# Stubs mimic the openai SDK shape: client.beta.chat.completions.parse(...)
# returns a completion whose choices[0].message.parsed is the Pydantic object.
class _StubMessage:
    def __init__(self, parsed):
        self.parsed = parsed
        self.refusal = None


class _StubChoice:
    def __init__(self, parsed):
        self.message = _StubMessage(parsed)


class _StubCompletion:
    def __init__(self, parsed):
        self.choices = [_StubChoice(parsed)]


class _StubCompletions:
    def __init__(self, parsed):
        self._parsed = parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _StubCompletion(self._parsed)


class _StubChat:
    def __init__(self, parsed):
        self.completions = _StubCompletions(parsed)


class _StubBeta:
    def __init__(self, parsed):
        self.chat = _StubChat(parsed)


class _StubClient:
    def __init__(self, parsed):
        self.beta = _StubBeta(parsed)


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
    result = map_fields(client, "gpt-4o", _schema(), _profile())
    assert result.by_id("q1").value == "Ginesis Finance SAS"
    assert result.by_id("q1").confidence == 0.95
    assert result.by_id("q2").status == "no_data"


def test_map_fields_passes_deployment_and_response_format():
    parsed = LLMMapping(answers=[])
    client = _StubClient(parsed)
    map_fields(client, "gpt-4o", _schema(), _profile())
    call = client.beta.chat.completions.calls[0]
    assert call["model"] == "gpt-4o"
    assert call["response_format"] is LLMMapping
