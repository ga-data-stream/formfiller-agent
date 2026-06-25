import pytest
from formfiller.models import (
    QuestionType,
    FormQuestion,
    FormSchema,
    MappedAnswer,
    MappingResult,
    EmailMessage,
)


def test_form_question_defaults():
    q = FormQuestion(id="q1", label="Company name", type=QuestionType.TEXT, required=True)
    assert q.options == ()
    assert q.required is True


def test_form_schema_holds_questions():
    q = FormQuestion(id="q1", label="VAT", type=QuestionType.TEXT, required=False)
    schema = FormSchema(url="https://forms.office.com/x", title="Vendor", questions=(q,))
    assert schema.questions[0].label == "VAT"


def test_mapped_answer_carries_confidence_and_status():
    a = MappedAnswer(
        question_id="q1",
        profile_field="vat_number",
        value="FR123",
        confidence=0.92,
        status="matched",
    )
    assert a.confidence == 0.92
    assert a.status == "matched"


def test_mapping_result_lookup_by_question_id():
    a = MappedAnswer(question_id="q1", profile_field=None, value=None, confidence=0.0, status="no_data")
    result = MappingResult(answers=(a,))
    assert result.by_id("q1") is a
    assert result.by_id("missing") is None


def test_email_message_is_frozen():
    msg = EmailMessage(
        entry_id="abc",
        sender="client@x.com",
        subject="Please fill",
        received="2026-06-10T09:00:00",
        body_text="link: https://forms.gle/x",
        body_html="<a href='https://forms.gle/x'>form</a>",
    )
    with pytest.raises(Exception):
        msg.entry_id = "changed"


def test_mapped_answer_has_rationale_default():
    from formfiller.models import MappedAnswer
    a = MappedAnswer(question_id="q", profile_field="f", value="v",
                     confidence=0.9, status="matched")
    assert a.rationale == ""
    assert a.model_copy(update={"rationale": "because"}).rationale == "because"


def test_mapping_outcome_carries_result_and_decisions():
    from formfiller.models import (
        MappedAnswer, MappingResult, DecisionRecord, MappingOutcome,
    )
    rec = DecisionRecord(
        question_id="q", label="L", type="text", required=True,
        profile_field="f", value="v",
        propose_status="matched", propose_confidence=0.9, propose_rationale="p",
        final_status="matched", final_confidence=0.95, verify_rationale="v",
        final_action="fill",
    )
    res = MappingResult(answers=(MappedAnswer(question_id="q", profile_field="f",
                        value="v", confidence=0.95, status="matched"),))
    out = MappingOutcome(result=res, decisions=(rec,))
    assert out.result.by_id("q").value == "v"
    assert out.decisions[0].final_action == "fill"
