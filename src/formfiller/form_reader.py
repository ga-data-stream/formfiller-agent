from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from formfiller.models import FormQuestion, FormSchema, QuestionType

logger = logging.getLogger(__name__)


class FormRenderError(Exception):
    """Raised when a known Microsoft Forms page yields no question items, i.e.
    it never rendered (still on the intro/spinner). Surfacing this prevents a
    silent empty-schema fallback that would be reported downstream as success."""

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


_MS_HOSTS = ("forms.office.com", "forms.microsoft.com", "forms.cloud.microsoft")
_START_TEXTS = ("Start now", "Commencer", "Get started", "Démarrer")

_MS_TYPE = {
    "text": QuestionType.TEXT,
    "choice_single": QuestionType.CHOICE_SINGLE,
    "choice_multi": QuestionType.CHOICE_MULTI,
    "unsupported": QuestionType.UNSUPPORTED,
}

_MS_EXTRACT_JS = r"""
() => {
  const items = Array.from(document.querySelectorAll('[data-automation-id="questionItem"]'));
  return items.map((item, idx) => {
    const headingEl = item.querySelector('[role=heading]');
    const titleEl = headingEl || item.querySelector('[data-automation-id="questionTitle"]');
    let title = '';
    if (titleEl) {
      const c = titleEl.cloneNode(true);
      c.querySelectorAll('[data-automation-id="questionOrdinal"],[data-automation-id="requiredStar"]').forEach(e => e.remove());
      title = (c.textContent || '').replace(/\s+/g, ' ').trim();
    }
    title = title.replace(/^\s*\d+\s*\.?\s*/, '');
    title = title.replace(/\s*(Single line text|Multi(?:ple)? Line Text|Single choice|Multiple choice|Date|Rating|Ranking|File upload|Net Promoter Score)\.?\s*$/i, '').trim();
    const required = !!item.querySelector('[data-automation-id="requiredStar"]');
    const choiceItems = Array.from(item.querySelectorAll('[data-automation-id="choiceItem"]'));
    let type = 'unsupported';
    let options = [];
    if (choiceItems.length) {
      const multi = !!item.querySelector('[role=checkbox], input[type=checkbox]');
      type = multi ? 'choice_multi' : 'choice_single';
      options = choiceItems.map(c => (c.getAttribute('aria-label') || c.textContent || '').replace(/\s+/g, ' ').trim());
    } else if (item.querySelector('textarea') || item.querySelector('input')) {
      type = 'text';
    }
    return { id: 'ms:' + idx, title, type, required, options };
  });
}
"""


def _is_ms_forms_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(h in host for h in _MS_HOSTS)


def _click_visible_start(page) -> bool:
    """Click the first VISIBLE Microsoft Forms intro/start button. MS Forms
    renders hidden duplicates, so we must skip non-visible matches."""
    for txt in _START_TEXTS:
        loc = page.get_by_role("button", name=re.compile(re.escape(txt), re.I))
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    el.scroll_into_view_if_needed(timeout=2000)
                    el.click(timeout=5000)
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def prepare_form(page, url: str) -> None:
    """Navigate to the form; for Microsoft Forms, click past the intro page and
    wait for questions to render. Tolerant for non-MS pages (e.g. fixtures)."""
    page.goto(url, wait_until="load")
    clicked = _click_visible_start(page)
    if clicked or _is_ms_forms_host(url):
        try:
            page.wait_for_selector('[data-automation-id="questionItem"]', timeout=15000)
        except Exception:  # noqa: BLE001 — surfaced later by schema_from_page
            logger.warning(
                "Microsoft Forms questions did not render within timeout for %s "
                "(intro page may not have been dismissed).", url,
            )


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
    """Extract a FormSchema from an already-navigated page. Uses Microsoft
    Forms-aware extraction when MS question items are present; otherwise falls
    back to the generic <label>-based extractor."""
    if page.locator('[data-automation-id="questionItem"]').count() > 0:
        recs = page.evaluate(_MS_EXTRACT_JS)
        questions = tuple(
            FormQuestion(
                id=rec["id"],
                label=rec["title"],
                type=_MS_TYPE.get(rec["type"], QuestionType.UNSUPPORTED),
                required=bool(rec["required"]),
                options=tuple(rec["options"]),
            )
            for rec in recs
        )
        return FormSchema(url=url, title=page.title(), questions=questions)

    if _is_ms_forms_host(url):
        # Known MS Forms host but no question items rendered: do NOT silently fall
        # back to the generic <label> extractor (which finds nothing on the SPA
        # shell and yields an empty schema). Fail loudly so the caller logs it.
        raise FormRenderError(
            f"Microsoft Forms page rendered no questions: {url}. The form likely "
            "did not load (intro page, login wall, or slow render)."
        )

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
    """Open `url` in Chromium (clicking past a Microsoft Forms intro page if
    present) and return its FormSchema."""
    with open_page(headless=headless) as page:
        prepare_form(page, url)
        return schema_from_page(page, url)
