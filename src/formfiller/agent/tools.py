from __future__ import annotations

import logging
import re
from typing import Any, Callable

from formfiller.agent.models import PageSnapshot, ToolCall, ToolResult
from formfiller.agent.perception import read_snapshot
from formfiller.confidence import FillInstruction, evaluate_gate
from formfiller.form_filler import fill_form, submit_form, take_screenshot
from formfiller.models import FormSchema, MappingResult

logger = logging.getLogger(__name__)

_NEXT_TEXTS = ("Next", "Suivant", "Continue", "Continuer")


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "name": "read_snapshot", "strict": False,
     "description": "Return the current page as a list of interactive elements with stable "
                    "refs, plus url/title and a blocker hint (login/consent/captcha).",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "extract_questions", "strict": False,
     "description": "Extract the form's questions (id, label, type, required, options) using the "
                    "Microsoft-Forms-aware extractor. Use the returned ids with answer_question.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "lookup_profile", "strict": False,
     "description": "Map the current form's questions to the company profile (one Azure call) and "
                    "return proposed values, confidence and status. Choice values are snapped to "
                    "exact options deterministically.",
     "parameters": {"type": "object",
                    "properties": {"question_ids": {"type": "array", "items": {"type": "string"},
                                   "description": "Optional subset; omit to map all questions."}},
                    "additionalProperties": False}},
    {"type": "function", "name": "answer_question", "strict": False,
     "description": "Fill a form field (text/textarea) or select the matching choice for the "
                    "question with this id (from extract_questions), reusing deterministic logic.",
     "parameters": {"type": "object",
                    "properties": {"question_id": {"type": "string"},
                                   "value": {"type": "string"}},
                    "required": ["question_id", "value"], "additionalProperties": False}},
    {"type": "function", "name": "click", "strict": False,
     "description": "Click the element with this ref (from read_snapshot). Use for buttons, "
                    "radios, links, consent buttons, intro 'Start' buttons.",
     "parameters": {"type": "object", "properties": {"ref": {"type": "string"}},
                    "required": ["ref"], "additionalProperties": False}},
    {"type": "function", "name": "fill", "strict": False,
     "description": "Type text into the input/textarea with this ref (from read_snapshot).",
     "parameters": {"type": "object",
                    "properties": {"ref": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["ref", "text"], "additionalProperties": False}},
    {"type": "function", "name": "select_choice", "strict": False,
     "description": "Select an option by visible label in the <select> dropdown with this ref.",
     "parameters": {"type": "object",
                    "properties": {"ref": {"type": "string"}, "option": {"type": "string"}},
                    "required": ["ref", "option"], "additionalProperties": False}},
    {"type": "function", "name": "scroll", "strict": False,
     "description": "Scroll the page up or down by roughly one viewport.",
     "parameters": {"type": "object",
                    "properties": {"direction": {"type": "string", "enum": ["up", "down"]}},
                    "required": ["direction"], "additionalProperties": False}},
    {"type": "function", "name": "navigate_next", "strict": False,
     "description": "Click the visible Next/Continue button to advance a multi-page form.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "goto", "strict": False,
     "description": "Navigate the browser to an absolute URL.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                    "required": ["url"], "additionalProperties": False}},
    {"type": "function", "name": "detect_blocker", "strict": False,
     "description": "Explicitly check whether the page is a login/consent/captcha blocker.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "request_human", "strict": False,
     "description": "Hand off to a human and end the run (use for captcha, login walls, or "
                    "anything you cannot safely complete).",
     "parameters": {"type": "object", "properties": {"reason": {"type": "string"}},
                    "required": ["reason"], "additionalProperties": False}},
    {"type": "function", "name": "submit", "strict": False,
     "description": "Request submission. A guard re-runs the confidence gate, honours dry_run, and "
                    "requires human confirmation before any real submit; it may refuse.",
     "parameters": {"type": "object", "properties": {"summary": {"type": "string"}},
                    "required": ["summary"], "additionalProperties": False}},
    {"type": "function", "name": "finish", "strict": False,
     "description": "End the run without submitting (e.g. nothing to do, or deferring to a human).",
     "parameters": {"type": "object",
                    "properties": {"ready_to_submit": {"type": "boolean"},
                                   "summary": {"type": "string"}},
                    "required": ["ready_to_submit", "summary"], "additionalProperties": False}},
]


class ToolExecutor:
    def __init__(self, *, page, url: str,
                 schema_reader: Callable[[], FormSchema],
                 mapper: Callable[[FormSchema], MappingResult],
                 threshold: float, dry_run: bool,
                 confirm: Callable[[str], bool]) -> None:
        self.page = page
        self.url = url
        self._schema_reader = schema_reader   # injected for testing; prod uses schema_from_page
        self.mapper = mapper
        self.threshold = threshold
        self.dry_run = dry_run
        self.confirm = confirm

    # --- public entry ---------------------------------------------------
    def dispatch(self, call: ToolCall) -> ToolResult:
        handler = getattr(self, f"_tool_{call.name}", None)
        if handler is None:
            return ToolResult(call_id=call.call_id, name=call.name,
                              output={"error": f"unknown tool '{call.name}'"})
        try:
            return handler(call)
        except Exception as exc:  # noqa: BLE001 — a bad tool call must not crash the loop
            logger.warning("tool %s failed: %s", call.name, exc)
            return ToolResult(call_id=call.call_id, name=call.name,
                              output={"error": str(exc)})

    def _read_schema(self) -> FormSchema:
        return self._schema_reader()

    def page_signature(self) -> str:
        return read_snapshot(self.page).signature()

    def _locator(self, ref: str):
        return self.page.locator(f'[data-ff-ref="{ref}"]')

    # --- perception -----------------------------------------------------
    def _tool_read_snapshot(self, call: ToolCall) -> ToolResult:
        snap: PageSnapshot = read_snapshot(self.page)
        return ToolResult(call_id=call.call_id, name=call.name, output=snap.model_dump())

    def _tool_detect_blocker(self, call: ToolCall) -> ToolResult:
        snap = read_snapshot(self.page)
        return ToolResult(call_id=call.call_id, name=call.name,
                          output={"blocker": snap.blocker})

    # --- power tools ----------------------------------------------------
    def _tool_extract_questions(self, call: ToolCall) -> ToolResult:
        schema = self._read_schema()
        return ToolResult(call_id=call.call_id, name=call.name,
                          output={"title": schema.title,
                                  "questions": [q.model_dump(mode="json") for q in schema.questions]})

    def _tool_lookup_profile(self, call: ToolCall) -> ToolResult:
        schema = self._read_schema()
        ids = call.arguments.get("question_ids")
        if ids:
            wanted = set(ids)
            schema = FormSchema(url=schema.url, title=schema.title,
                                questions=tuple(q for q in schema.questions if q.id in wanted))
        mapping = self.mapper(schema)
        return ToolResult(call_id=call.call_id, name=call.name,
                          output={"answers": [a.model_dump() for a in mapping.answers]})

    def _tool_answer_question(self, call: ToolCall) -> ToolResult:
        qid = call.arguments["question_id"]
        value = call.arguments["value"]
        fill_form(self.page, [FillInstruction(question_id=qid, value=value)])
        return ToolResult(call_id=call.call_id, name=call.name,
                          output={"status": "filled", "question_id": qid})

    # --- primitives -----------------------------------------------------
    def _tool_click(self, call: ToolCall) -> ToolResult:
        self._locator(call.arguments["ref"]).click(timeout=5000)
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    def _tool_fill(self, call: ToolCall) -> ToolResult:
        self._locator(call.arguments["ref"]).fill(call.arguments["text"])
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    def _tool_select_choice(self, call: ToolCall) -> ToolResult:
        self._locator(call.arguments["ref"]).select_option(label=call.arguments["option"])
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    def _tool_scroll(self, call: ToolCall) -> ToolResult:
        delta = 600 if call.arguments.get("direction") == "down" else -600
        self.page.evaluate(f"window.scrollBy(0, {delta})")
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    def _tool_navigate_next(self, call: ToolCall) -> ToolResult:
        clicked = self._click_visible_by_text(_NEXT_TEXTS)
        return ToolResult(call_id=call.call_id, name=call.name,
                          output={"ok": True, "clicked": clicked})

    def _tool_goto(self, call: ToolCall) -> ToolResult:
        self.page.goto(call.arguments["url"], wait_until="load")
        self.url = call.arguments["url"]
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    # --- control --------------------------------------------------------
    def _tool_request_human(self, call: ToolCall) -> ToolResult:
        reason = call.arguments.get("reason", "agent requested human")
        return ToolResult(call_id=call.call_id, name=call.name,
                          output={"control": "request_human", "reason": reason},
                          terminal="review", reason=reason)

    def _tool_finish(self, call: ToolCall) -> ToolResult:
        summary = call.arguments.get("summary", "")
        ready = call.arguments.get("ready_to_submit", False)
        return ToolResult(call_id=call.call_id, name=call.name,
                          output={"control": "finish", "ready_to_submit": ready,
                                  "summary": summary},
                          terminal="review",
                          reason=summary or "agent finished without submitting")

    def _tool_submit(self, call: ToolCall) -> ToolResult:
        summary = call.arguments.get("summary", "")
        schema = self._read_schema()
        mapping = self.mapper(schema)
        decision = evaluate_gate(schema, mapping, self.threshold)

        # Fill the gate-approved (or proposed) values, then screenshot the form.
        fill_form(self.page, list(decision.fields_to_fill))
        shot = take_screenshot(self.page)

        if decision.action == "review":
            return ToolResult(call_id=call.call_id, name=call.name,
                              output={"control": "refused", "reason": decision.reason},
                              terminal="review", reason=decision.reason,
                              screenshot=shot, schema=schema, mapping=mapping)

        if self.dry_run:
            return ToolResult(call_id=call.call_id, name=call.name,
                              output={"control": "dry_run",
                                      "detail": "would submit; dry_run is on"},
                              terminal="dry_run", reason="dry-run: filled but not submitted",
                              screenshot=shot, schema=schema, mapping=mapping)

        if not self.confirm(summary):
            return ToolResult(call_id=call.call_id, name=call.name,
                              output={"control": "declined", "reason": "human declined submit"},
                              terminal="review", reason="human declined confirmation",
                              screenshot=shot, schema=schema, mapping=mapping)

        submitted = submit_form(self.page, dry_run=False)
        return ToolResult(call_id=call.call_id, name=call.name,
                          output={"control": "submitted", "submitted": bool(submitted)},
                          terminal="submitted" if submitted else "fail",
                          reason="submitted" if submitted else "submit button not found",
                          screenshot=shot, schema=schema, mapping=mapping)

    # --- helpers --------------------------------------------------------
    def _click_visible_by_text(self, texts) -> bool:
        for txt in texts:
            loc = self.page.get_by_role("button", name=re.compile(re.escape(txt), re.I))
            try:
                count = loc.count()
            except Exception:  # noqa: BLE001
                count = 0
            for i in range(count):
                el = loc.nth(i)
                try:
                    if el.is_visible():
                        el.click(timeout=5000)
                        return True
                except Exception:  # noqa: BLE001
                    continue
        return False
