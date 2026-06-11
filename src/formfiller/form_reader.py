from __future__ import annotations

from contextlib import contextmanager

from playwright.sync_api import Page, sync_playwright

from formfiller.models import FormQuestion, FormSchema, QuestionType

# JS evaluated in the page: walk each <label>, find its control, build a record.
_EXTRACT_JS = r"""
() => {
  const recs = [];
  const labels = Array.from(document.querySelectorAll('label'));
  labels.forEach((label, idx) => {
    const forId = label.getAttribute('for');
    let control = forId ? document.getElementById(forId) : null;
    if (!control) control = label.parentElement.querySelector('input,select,textarea');
    if (!control) return;
    const tag = control.tagName.toLowerCase();
    const inputType = (control.getAttribute('type') || '').toLowerCase();
    const ariaReq = control.getAttribute('aria-required');
    const required = ariaReq === 'true' || control.required === true;
    let options = [];
    if (tag === 'select') {
      options = Array.from(control.querySelectorAll('option')).map(o => o.textContent.trim());
    }
    recs.push({
      id: control.id || ('q' + idx),
      label: label.textContent.trim(),
      tag, inputType, required, options,
    });
  });
  return { title: document.title, recs };
}
"""


def _classify(tag: str, input_type: str) -> QuestionType:
    if tag == "select":
        return QuestionType.CHOICE_SINGLE
    if tag == "textarea":
        return QuestionType.TEXT
    if tag == "input":
        return {
            "text": QuestionType.TEXT,
            "email": QuestionType.EMAIL,
            "date": QuestionType.DATE,
            "number": QuestionType.NUMBER,
            "tel": QuestionType.TEXT,
            "url": QuestionType.TEXT,
            "": QuestionType.TEXT,
        }.get(input_type, QuestionType.TEXT)
    return QuestionType.UNSUPPORTED


@contextmanager
def open_page(headless: bool = True):
    """Yield a fresh Playwright Page, cleaning up the browser afterward."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()


def schema_from_page(page: Page, url: str) -> FormSchema:
    """Extract a FormSchema from an already-navigated page."""
    data = page.evaluate(_EXTRACT_JS)
    questions = tuple(
        FormQuestion(
            id=rec["id"],
            label=rec["label"],
            type=_classify(rec["tag"], rec["inputType"]),
            required=bool(rec["required"]),
            options=tuple(rec["options"]),
        )
        for rec in data["recs"]
    )
    return FormSchema(url=url, title=data["title"], questions=questions)


def read_form(url: str, headless: bool = True) -> FormSchema:
    """Open `url` in Chromium and return its FormSchema."""
    with open_page(headless=headless) as page:
        page.goto(url, wait_until="load")
        return schema_from_page(page, url)
