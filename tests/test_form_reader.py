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


def test_read_ms_forms_fixture_extracts_questions():
    schema = read_form(_file_url("ms_forms_rendered.html"))
    assert len(schema.questions) == 6
    by_label = {q.label: q for q in schema.questions}
    siren = by_label["Quel est votre SIREN (9 caractères) ?"]   # ordinal + star stripped
    assert siren.type == QuestionType.TEXT
    assert siren.required is True
    assert siren.id.startswith("ms:")
    assert by_label["Qui est notre contact dans vos équipes comptables ?"].type == QuestionType.TEXT  # textarea
    choice = by_label["Quel choix de format d'adressage avez-vous choisi ?"]
    assert choice.type == QuestionType.CHOICE_SINGLE
    assert choice.options == ("EDI", "PDF signé", "Portail")
    assert choice.required is True
    assert by_label["Avez-vous plusieurs lignes d'adressage ?"].required is False
    # labels must be clean of MS Forms screen-reader hint text and ordinals
    for q in schema.questions:
        assert "line text" not in q.label.lower()
        assert "choice." not in q.label.lower()
        assert not q.label.strip()[:2].rstrip().isdigit()  # no leading "1." ordinal
