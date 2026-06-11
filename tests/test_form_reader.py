from pathlib import Path
import pytest
from formfiller.models import QuestionType
from formfiller.form_reader import read_form

FIXTURES = Path(__file__).parent / "fixtures"


def _file_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


@pytest.mark.parametrize("name,title,first_label,n", [
    ("ms_form.html", "Vendor Onboarding", "Company legal name", 4),
    ("google_form.html", "Supplier Form", "VAT number", 2),
])
def test_read_form_extracts_questions(name, title, first_label, n):
    schema = read_form(_file_url(name))
    assert schema.title == title
    assert len(schema.questions) == n
    assert schema.questions[0].label == first_label


def test_read_form_detects_types_and_required():
    schema = read_form(_file_url("ms_form.html"))
    by_label = {q.label: q for q in schema.questions}
    assert by_label["Company legal name"].type == QuestionType.TEXT
    assert by_label["Company legal name"].required is True
    assert by_label["Contact email"].type == QuestionType.EMAIL
    assert by_label["Preferred contact method"].type == QuestionType.CHOICE_SINGLE
    assert by_label["Preferred contact method"].options == ("Email", "Phone")
    assert by_label["Comments"].required is False
