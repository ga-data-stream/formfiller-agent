from __future__ import annotations

from typing import Sequence

from playwright.sync_api import Page

from formfiller.confidence import FillInstruction


def fill_form(page: Page, instructions: Sequence[FillInstruction]) -> None:
    """Type/select each instruction's value into the control with that id.

    Uses Playwright's `fill` for text-like inputs and `select_option` for
    <select>. Unknown controls are skipped silently (the gate already vetted
    types; this is defensive).
    """
    for ins in instructions:
        selector = f"#{ins.question_id}"
        locator = page.locator(selector)
        if locator.count() == 0:
            continue
        tag = locator.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            locator.select_option(label=ins.value)
        else:
            locator.fill(ins.value)


def submit_form(page: Page, dry_run: bool) -> bool:
    """Click the form's submit control unless dry_run is True.

    Returns True if a submit was actually performed. Looks for common submit
    affordances (button[type=submit], a 'Submit'/'Envoyer' button).
    """
    if dry_run:
        return False
    candidates = [
        "button[type=submit]",
        "input[type=submit]",
        "button:has-text('Submit')",
        "button:has-text('Envoyer')",
        "div[role=button]:has-text('Submit')",
    ]
    for sel in candidates:
        loc = page.locator(sel)
        if loc.count() > 0:
            loc.first.click()
            return True
    return False


def take_screenshot(page: Page) -> bytes:
    """Full-page PNG screenshot as bytes (for the review queue)."""
    return page.screenshot(full_page=True)
