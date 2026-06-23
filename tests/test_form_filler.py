from pathlib import Path
import pytest
from formfiller.confidence import FillInstruction
from formfiller.form_reader import open_page
from formfiller.form_filler import fill_form, submit_form, take_screenshot, _match_choice

ADDR_OPTIONS = ["SIREN", "SIREN_SIRET", "SIREN_SIRET_Code_Routage", "SIREN_Suffixe"]

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


def test_fill_form_returns_count_of_fields_actually_filled():
    instructions = [
        FillInstruction(question_id="i1", value="Ginesis Finance SAS"),
        FillInstruction(question_id="i2", value="contact@ginesis-finance.com"),
    ]
    with open_page() as page:
        page.goto(_file_url("ms_form.html"), wait_until="load")
        assert fill_form(page, instructions) == 2


def test_fill_form_does_not_count_selectors_that_miss():
    instructions = [
        FillInstruction(question_id="i1", value="Ginesis Finance SAS"),
        FillInstruction(question_id="does-not-exist", value="ignored"),
    ]
    with open_page() as page:
        page.goto(_file_url("ms_form.html"), wait_until="load")
        # only #i1 lands; the bogus selector is skipped and must not be counted
        assert fill_form(page, instructions) == 1


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


def test_match_choice_exact():
    assert _match_choice(ADDR_OPTIONS, "SIREN_SIRET") == 1


def test_match_choice_normalized_separators_and_case():
    # LLM paraphrase: spaces, a plus sign, different case -> the underscore option.
    assert _match_choice(ADDR_OPTIONS, "siren + siret") == 1


def test_match_choice_no_false_positive_substring():
    # "SIREN" is a substring of "SIREN_SIRET" but must resolve to the exact option.
    assert _match_choice(ADDR_OPTIONS, "SIREN") == 0


def test_match_choice_returns_none_when_no_match():
    assert _match_choice(ADDR_OPTIONS, "EDI") is None


def test_fill_ms_choice_logs_when_no_option_matches(caplog):
    from formfiller.form_reader import read_form, prepare_form
    url = _file_url("ms_forms_rendered.html")
    schema = read_form(url)
    by_label = {q.label: q for q in schema.questions}
    instructions = [
        FillInstruction(
            question_id=by_label["Quel choix de format d'adressage avez-vous choisi ?"].id,
            value="Format inexistant",
        ),
    ]
    with open_page() as page:
        prepare_form(page, url)
        with caplog.at_level("WARNING"):
            fill_form(page, instructions)
        items = page.locator('[data-automation-id="questionItem"]')
        selected = items.nth(3).locator('[data-automation-id="choiceItem"][data-selected="true"]')
        assert selected.count() == 0  # nothing wrongly clicked
    assert any("Format inexistant" in r.getMessage() for r in caplog.records)


def test_submit_form_clicks_visible_submit():
    from formfiller.form_reader import open_page as _op, prepare_form
    url = _file_url("ms_forms_rendered.html")
    with _op() as page:
        prepare_form(page, url)
        assert submit_form(page, dry_run=False) is True
        assert submit_form(page, dry_run=True) is False  # dry-run never clicks
