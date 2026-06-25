import pytest
from formfiller.models import QuestionType, FormQuestion, FormSchema, MappedAnswer, MappingResult
from formfiller.confidence import evaluate_gate


def _schema(*questions):
    return FormSchema(url="https://forms.office.com/r/x", title="t", questions=tuple(questions))


def _q(qid, required=True, qtype=QuestionType.TEXT):
    return FormQuestion(id=qid, label=qid, type=qtype, required=required)


def _ans(qid, value="v", confidence=0.95, status="matched", field="f"):
    return MappedAnswer(question_id=qid, profile_field=field, value=value, confidence=confidence, status=status)


def test_submits_when_all_required_matched_above_threshold():
    schema = _schema(_q("q1"), _q("q2"))
    result = MappingResult(answers=(_ans("q1"), _ans("q2")))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "submit"
    assert {f.question_id for f in decision.fields_to_fill} == {"q1", "q2"}
    assert decision.fields_blank_flagged == ()


def test_optional_no_data_is_left_blank_and_flagged_still_submits():
    schema = _schema(_q("q1"), _q("q2", required=False))
    result = MappingResult(answers=(
        _ans("q1"),
        MappedAnswer(question_id="q2", profile_field=None, value=None, confidence=0.0, status="no_data"),
    ))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "submit"
    assert decision.fields_blank_flagged == ("q2",)
    assert {f.question_id for f in decision.fields_to_fill} == {"q1"}


def test_required_no_data_routes_to_review():
    schema = _schema(_q("q1"), _q("q2"))
    result = MappingResult(answers=(
        _ans("q1"),
        MappedAnswer(question_id="q2", profile_field=None, value=None, confidence=0.0, status="no_data"),
    ))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert "required" in decision.reason.lower()


def test_low_confidence_matched_now_fills_not_routes():
    # Confidence is no longer a gate: a 'matched' answer fills regardless of score.
    schema = _schema(_q("q1"))
    result = MappingResult(answers=(_ans("q1", confidence=0.3),))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "submit"
    assert {f.question_id for f in decision.fields_to_fill} == {"q1"}


def test_ambiguous_field_routes_to_review():
    schema = _schema(_q("q1"))
    result = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="f", value="v", confidence=0.9, status="ambiguous"),
    ))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert "ambiguous" in decision.reason.lower()


def test_unsupported_question_type_routes_to_review():
    schema = _schema(_q("q1", qtype=QuestionType.UNSUPPORTED))
    result = MappingResult(answers=(_ans("q1"),))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert "type" in decision.reason.lower()


def test_required_question_with_no_answer_at_all_routes_to_review():
    schema = _schema(_q("q1"), _q("q2"))
    result = MappingResult(answers=(_ans("q1"),))  # nothing for q2
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert "missing" in decision.reason.lower()


def test_review_decision_still_carries_fillable_fields_for_screenshot():
    schema = _schema(_q("q1"), _q("q2", required=False))
    result = MappingResult(answers=(
        # q1 fills; q2 ambiguous -> routes whole form to review
        _ans("q1"),
        MappedAnswer(question_id="q2", profile_field="f", value="v2",
                     confidence=0.9, status="ambiguous"),
    ))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert {f.question_id for f in decision.fields_to_fill} == {"q1"}
