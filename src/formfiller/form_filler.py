from __future__ import annotations

import logging
import re
from typing import Sequence

from playwright.sync_api import Page

from formfiller.choices import match_choice as _match_choice
from formfiller.confidence import FillInstruction

logger = logging.getLogger(__name__)


def fill_form(page: Page, instructions: Sequence[FillInstruction]) -> None:
    """Fill each instruction's value. Microsoft Forms questions (ids prefixed
    'ms:') are addressed by question index; generic forms by element id."""
    for ins in instructions:
        if ins.question_id.startswith("ms:"):
            _fill_ms_question(page, int(ins.question_id[3:]), ins.value)
            continue
        locator = page.locator(f"#{ins.question_id}")
        if locator.count() == 0:
            continue
        tag = locator.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            locator.select_option(label=ins.value)
        else:
            locator.fill(ins.value)


def _fill_ms_question(page: Page, index: int, value: str) -> None:
    item = page.locator('[data-automation-id="questionItem"]').nth(index)
    textarea = item.locator("textarea")
    if textarea.count() > 0:
        textarea.first.fill(value)
        return
    text_input = item.locator('input[data-automation-id="textInput"]')
    if text_input.count() > 0:
        text_input.first.fill(value)
        return
    # choice question: click the option whose label matches the value
    choices = item.locator('[data-automation-id="choiceItem"]')
    labels = [
        (choices.nth(i).get_attribute("aria-label") or choices.nth(i).inner_text() or "").strip()
        for i in range(choices.count())
    ]
    match = _match_choice(labels, value)
    if match is None:
        logger.warning(
            "No choice option matched value %r (question %d); available options: %s",
            value, index, labels,
        )
        return
    choices.nth(match).click()


def submit_form(page: Page, dry_run: bool) -> bool:
    """Click the VISIBLE submit button unless dry_run. MS Forms renders hidden
    duplicate buttons, so skip non-visible matches. Returns True if clicked."""
    if dry_run:
        return False
    for txt in ("Submit", "Envoyer", "Soumettre"):
        loc = page.get_by_role("button", name=re.compile(re.escape(txt), re.I))
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    el.click(timeout=5000)
                    return True
            except Exception:  # noqa: BLE001
                continue
    for sel in ("button[type=submit]", "input[type=submit]"):
        loc = page.locator(sel)
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    el.click(timeout=5000)
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def take_screenshot(page: Page) -> bytes:
    """Full-page PNG screenshot as bytes (for the review queue)."""
    return page.screenshot(full_page=True)
