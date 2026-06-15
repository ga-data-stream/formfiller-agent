from pathlib import Path
import pytest
from formfiller.confidence import FillInstruction
from formfiller.form_reader import open_page
from formfiller.form_filler import fill_form, submit_form, take_screenshot

FIXTURES = Path(__file__).parent / "fixtures"


def _file_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


def test_fill_form_sets_input_values():
    instructions = [
        FillInstruction(question_id="i1", value="Ginesis Finance SAS"),
        FillInstruction(question_id="i2", value="contact@ginesis-finance.com"),
    ]
    with open_page() as page:
        page.goto(_file_url("ms_form.html"), wait_until="load")
        fill_form(page, instructions)
        assert page.input_value("#i1") == "Ginesis Finance SAS"
        assert page.input_value("#i2") == "contact@ginesis-finance.com"


def test_take_screenshot_returns_png_bytes():
    with open_page() as page:
        page.goto(_file_url("ms_form.html"), wait_until="load")
        data = take_screenshot(page)
        assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_fill_ms_forms_text_and_choice():
    from formfiller.form_reader import read_form, prepare_form
    url = _file_url("ms_forms_rendered.html")
    schema = read_form(url)
    by_label = {q.label: q for q in schema.questions}
    instructions = [
        FillInstruction(question_id=by_label["Quel est votre SIREN (9 caractères) ?"].id, value="123456789"),
        FillInstruction(question_id=by_label["Qui est notre contact dans vos équipes comptables ?"].id, value="Marie Comptable"),
        FillInstruction(question_id=by_label["Quel choix de format d'adressage avez-vous choisi ?"].id, value="PDF signé"),
    ]
    with open_page() as page:
        prepare_form(page, url)
        fill_form(page, instructions)
        items = page.locator('[data-automation-id="questionItem"]')
        assert items.nth(0).locator("input").input_value() == "123456789"
        assert items.nth(2).locator("textarea").input_value() == "Marie Comptable"
        # the matching radio choiceItem was clicked
        selected = items.nth(3).locator('[data-automation-id="choiceItem"][data-selected="true"]')
        assert selected.count() == 1
        assert (selected.first.get_attribute("aria-label")) == "PDF signé"


def test_submit_form_clicks_visible_submit():
    from formfiller.form_reader import open_page as _op, prepare_form
    url = _file_url("ms_forms_rendered.html")
    with _op() as page:
        prepare_form(page, url)
        assert submit_form(page, dry_run=False) is True
        assert submit_form(page, dry_run=True) is False  # dry-run never clicks
