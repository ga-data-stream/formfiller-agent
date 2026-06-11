import json
import pytest
from formfiller.models import QuestionType, FormQuestion, FormSchema, MappedAnswer, MappingResult
from formfiller.review_queue import park_for_review


def _schema():
    return FormSchema(
        url="https://forms.office.com/r/x",
        title="Vendor",
        questions=(FormQuestion(id="q1", label="VAT", type=QuestionType.TEXT, required=True),),
    )


def _result():
    return MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="vat_number", value="FR1", confidence=0.5, status="matched"),
    ))


def test_park_writes_payload_json_and_returns_dir(tmp_path):
    out_dir = park_for_review(
        queue_dir=tmp_path,
        job_id="job-123",
        schema=_schema(),
        result=_result(),
        reason="Low confidence",
        screenshot_bytes=b"\x89PNG fake",
    )
    payload = json.loads((out_dir / "payload.json").read_text(encoding="utf-8"))
    assert payload["reason"] == "Low confidence"
    assert payload["form_url"] == "https://forms.office.com/r/x"
    assert payload["answers"][0]["question_id"] == "q1"
    assert (out_dir / "screenshot.png").read_bytes() == b"\x89PNG fake"


def test_park_without_screenshot_skips_image(tmp_path):
    out_dir = park_for_review(
        queue_dir=tmp_path,
        job_id="job-456",
        schema=_schema(),
        result=_result(),
        reason="Required field missing",
        screenshot_bytes=None,
    )
    assert (out_dir / "payload.json").exists()
    assert not (out_dir / "screenshot.png").exists()
