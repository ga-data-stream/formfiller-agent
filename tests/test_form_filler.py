from pathlib import Path
import pytest
from formfiller.confidence import FillInstruction
from formfiller.form_reader import open_page
from formfiller.form_filler import fill_form, take_screenshot

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
