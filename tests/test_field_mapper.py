import pytest
from formfiller.config import ProfileField
from formfiller.models import QuestionType, FormQuestion, FormSchema
from formfiller.field_mapper import map_fields, LLMMapping, LLMMappedAnswer, _SYSTEM
from formfiller.field_mapper import map_and_verify
from formfiller.models import MappingOutcome


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


def _choice_schema():
    return FormSchema(
        url="https://forms.office.com/r/x", title="Vendor",
        questions=(
            FormQuestion(
                id="c1", label="Format d'adressage", type=QuestionType.CHOICE_SINGLE,
                required=True,
                options=("SIREN", "SIREN_SIRET", "SIREN_SIRET_Code_Routage", "SIREN_Suffixe"),
            ),
        ),
    )


def test_map_fields_snaps_paraphrased_choice_to_exact_option():
    # The nano model echoes the descriptive profile value and flags it ambiguous;
    # deterministic resolution must snap it to the exact option and confirm it.
    parsed = LLMMapping(answers=[
        LLMMappedAnswer(question_id="c1", profile_field="addressing_format",
                        value="SIREN + SIRET", confidence=0.45, status="ambiguous"),
    ])
    result = map_fields(_StubClient(parsed), "gpt-5.4-nano", _choice_schema(), _profile())
    ans = result.by_id("c1")
    assert ans.value == "SIREN_SIRET"
    assert ans.status == "matched"
    assert ans.confidence == 1.0


def test_map_fields_leaves_unresolvable_choice_untouched():
    # A value that matches no option must stay as-is so the gate routes it to review.
    parsed = LLMMapping(answers=[
        LLMMappedAnswer(question_id="c1", profile_field="addressing_format",
                        value="quelque chose d'autre", confidence=0.4, status="ambiguous"),
    ])
    result = map_fields(_StubClient(parsed), "gpt-5.4-nano", _choice_schema(), _profile())
    ans = result.by_id("c1")
    assert ans.status == "ambiguous"
    assert ans.value == "quelque chose d'autre"


def test_system_prompt_requires_verbatim_option_for_choices():
    low = _SYSTEM.lower()
    # The LLM must be told to answer choice questions with one of the exact options.
    assert "option" in low
    assert "verbatim" in low or "exactly" in low


def test_map_fields_passes_deployment_and_text_format():
    parsed = LLMMapping(answers=[])
    client = _StubClient(parsed)
    map_fields(client, "gpt-5.4-nano", _schema(), _profile())
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.4-nano"
    assert call["text_format"] is LLMMapping
    # system prompt goes via `instructions`, the form payload via `input`
    assert "instructions" in call and "input" in call


def test_map_fields_propagates_rationale():
    parsed = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.95,
                        status="matched", rationale="company name -> legal name"),
    ])
    result = map_fields(_StubClient(parsed), "gpt-5.4", _schema(), _profile())
    assert result.by_id("q1").rationale == "company name -> legal name"


def test_user_prompt_includes_field_description():
    from formfiller.field_mapper import _build_user_prompt
    prof = (ProfileField(name="addressing_line", value="X",
                         description="e-invoicing routing line, NOT postal"),)
    prompt = _build_user_prompt(_schema(), prof)
    assert "e-invoicing routing line" in prompt


class _SeqResponses:
    def __init__(self, parsed_seq):
        self._seq = list(parsed_seq)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _StubResponse(self._seq.pop(0))


class _SeqClient:
    def __init__(self, parsed_seq):
        self.responses = _SeqResponses(parsed_seq)


def test_verify_can_rescue_timid_match():
    from formfiller.field_mapper import LLMVerification, LLMVerifiedAnswer
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.4,
                        status="ambiguous", rationale="unsure"),
    ])
    verify = LLMVerification(answers=[
        LLMVerifiedAnswer(question_id="q1", profile_field="company_legal_name",
                          value="Ginesis Finance SAS", confidence=0.97,
                          status="matched", rationale="clearly the legal name"),
    ])
    out = map_and_verify(_SeqClient([propose, verify]), "gpt-5.4", _schema(), _profile())
    assert isinstance(out, MappingOutcome)
    ans = out.result.by_id("q1")
    assert ans.status == "matched"
    assert ans.value == "Ginesis Finance SAS"
    rec = next(d for d in out.decisions if d.question_id == "q1")
    assert rec.propose_status == "ambiguous"
    assert rec.final_status == "matched"
    assert rec.final_action == "fill"
    assert "legal name" in rec.verify_rationale


def test_verify_rejects_value_not_in_profile_keeps_pass1():
    from formfiller.field_mapper import LLMVerification, LLMVerifiedAnswer
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.9,
                        status="matched", rationale="ok"),
    ])
    verify = LLMVerification(answers=[
        LLMVerifiedAnswer(question_id="q1", profile_field="company_legal_name",
                          value="Some Hallucinated Name", confidence=0.9,
                          status="matched", rationale="changed"),
    ])
    out = map_and_verify(_SeqClient([propose, verify]), "gpt-5.4", _schema(), _profile())
    assert out.result.by_id("q1").value == "Ginesis Finance SAS"  # pass-1 value kept


def test_verify_failure_falls_back_to_pass1():
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.9,
                        status="matched", rationale="ok"),
    ])

    class _BoomResponses(_SeqResponses):
        def parse(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _StubResponse(propose)
            raise RuntimeError("verify boom")

    client = _SeqClient([propose])
    client.responses = _BoomResponses([propose])
    out = map_and_verify(client, "gpt-5.4", _schema(), _profile())
    assert out.result.by_id("q1").value == "Ginesis Finance SAS"
    rec = next(d for d in out.decisions if d.question_id == "q1")
    assert "unavailable" in rec.verify_rationale.lower()


def test_verify_false_skips_second_call():
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.9,
                        status="matched", rationale="ok"),
    ])
    client = _SeqClient([propose])
    out = map_and_verify(client, "gpt-5.4", _schema(), _profile(), verify=False)
    assert len(client.responses.calls) == 1
    assert out.result.by_id("q1").status == "matched"
