from __future__ import annotations

from typing import Any

from formfiller.agent.blockers import detect_blocker
from formfiller.agent.models import PageSnapshot, SnapshotElement

# Tags every interactive element with a stable data-ff-ref and returns a compact
# record per element. Refs are assigned in document order each call; primitives
# locate elements via [data-ff-ref="..."]. Keep this list focused on actionable
# controls so the snapshot stays small.
SNAPSHOT_JS = r"""
() => {
  const sel = 'input,textarea,select,button,[role=button],[role=radio],[role=checkbox],a[href]';
  const nodes = Array.from(document.querySelectorAll(sel));
  const elements = [];
  let i = 0;
  for (const el of nodes) {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const ref = 'e' + (i++);
    el.setAttribute('data-ff-ref', ref);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = el.getAttribute('role') || (tag === 'input' ? (type || 'textbox') : tag);
    const name = (el.getAttribute('aria-label') || el.getAttribute('placeholder')
                  || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 200);
    const state = {};
    if (el.getAttribute('aria-required') === 'true' || el.required) state.required = true;
    if (el.disabled) state.disabled = true;
    if (el.getAttribute('aria-checked') === 'true' || el.checked) state.checked = true;
    elements.push({ ref, role, name, value: el.value || '', type, state });
  }
  const frames = Array.from(document.querySelectorAll('iframe[src]'));
  const has_captcha_frame = frames.some(f => /recaptcha|hcaptcha|turnstile/i.test(f.src));
  return { url: document.location.href, title: document.title, elements, has_captcha_frame };
}
"""


def build_snapshot(raw: dict[str, Any]) -> PageSnapshot:
    """Pure: turn the JS-evaluate dict into a PageSnapshot (+ blocker hint)."""
    elements = tuple(
        SnapshotElement(
            ref=e["ref"], role=e.get("role", ""), name=e.get("name", ""),
            value=e.get("value", "") or "", state=e.get("state", {}) or {},
        )
        for e in raw.get("elements", [])
    )
    return PageSnapshot(
        url=raw.get("url", ""), title=raw.get("title", ""),
        elements=elements, blocker=detect_blocker(raw),
    )


def read_snapshot(page) -> PageSnapshot:
    """Tag the live page and build its snapshot."""
    raw = page.evaluate(SNAPSHOT_JS)
    return build_snapshot(raw)
