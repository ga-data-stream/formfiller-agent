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
