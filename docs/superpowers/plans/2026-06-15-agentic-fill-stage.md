# Agentic Fill Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in agent loop that fills/navigates a form after the link is opened, perceiving the page as an accessibility/DOM snapshot, acting through tools that reuse the existing deterministic modules, and committing the irreversible submit only through a guard (gate + dry_run + human confirm).

**Architecture:** A new `src/formfiller/agent/` subpackage holds the loop. `config.fill_strategy` selects `deterministic` (today's `process_email`, untouched) or `agent` (new `run_agent_pipeline`). The agent loop is a native function-calling loop on the Azure v1 Responses API; perception is a text snapshot with stable `data-ff-ref` ids; tools are layered (power tools wrap `form_reader`/`field_mapper`/`choices`/`form_filler`, primitives drive raw Playwright, control/safety tools end the loop); `submit` runs the existing `evaluate_gate` + `submit_form` behind `dry_run` and a human-confirm callback. On loop abort/fail the orchestrator falls back to the deterministic `process_email`. Every step is written to a JSONL trace.

**Tech Stack:** Python 3.14, `openai` 2.40.0 (Azure v1 Responses API via `OpenAI(base_url=.../openai/v1/)`), Playwright sync API, Pydantic v2, pytest. Tests use a FakeLLM + FakePage; no network or browser in CI.

---

## Design invariants (read before starting)

- **Two addressing schemes, by design.** Power tools speak `FormQuestion.id` (e.g. `ms:0`) and reuse `form_filler.fill_form` — used for the actual form fields. Primitives speak `data-ff-ref` ids from `read_snapshot` — used for page chrome the deterministic extractor doesn't model (intro/Start buttons, consent, login fields, Next buttons, odd layouts).
- **Submit is deterministic at the moment of commit.** The `submit` tool recomputes `schema_from_page` → `map_fields` → `evaluate_gate` on the *current* page and only proceeds when the gate says `submit`. The agent's own fills matter for navigation/blockers; the final commit is gated by proven code. This is the safety contract.
- **The deterministic path is never modified.** `process_email` and its tests stay byte-for-byte the same. The agent path is additive and falls back to `process_email`.
- **Exactly one Excel row per run.** `run_agent_pipeline` logs once for agent-handled outcomes; on fallback it returns `process_email(...)`'s result (which logs once) and does not log itself.

---

## Task 1: Config — add agent settings

**Files:**
- Modify: `src/formfiller/config.py:10-18` (AppConfig)
- Modify: `config.yaml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_appconfig_agent_defaults():
    from formfiller.config import AppConfig
    cfg = AppConfig(excel_log_path="x.xlsx")
    assert cfg.fill_strategy == "deterministic"
    assert cfg.agent_model_deployment == ""   # falls back to azure_openai_deployment when blank
    assert cfg.max_steps == 20
    assert cfg.no_progress_limit == 5
    assert cfg.traces_dir == "./traces"


def test_appconfig_agent_overrides():
    from formfiller.config import AppConfig
    cfg = AppConfig(
        excel_log_path="x.xlsx", fill_strategy="agent",
        agent_model_deployment="gpt-5.4", max_steps=12,
        no_progress_limit=3, traces_dir="./t",
    )
    assert cfg.fill_strategy == "agent"
    assert cfg.agent_model_deployment == "gpt-5.4"
    assert cfg.max_steps == 12
    assert cfg.no_progress_limit == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_appconfig_agent_defaults -v`
Expected: FAIL (`AppConfig` has no field `fill_strategy`).

- [ ] **Step 3: Add the fields**

In `src/formfiller/config.py`, extend `AppConfig` (keep `model_config = ConfigDict(frozen=True)`), adding after `azure_api_version`:

```python
    # --- agent fill strategy (additive; deterministic stays the default) ---
    fill_strategy: Literal["deterministic", "agent"] = "deterministic"
    agent_model_deployment: str = ""   # blank → reuse azure_openai_deployment
    max_steps: int = 20
    no_progress_limit: int = 5
    traces_dir: str = "./traces"
```

Add `from typing import Literal` at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (all, including pre-existing config tests).

- [ ] **Step 5: Update config.yaml**

Append to `config.yaml`:

```yaml
# --- agent loop (opt-in; default deterministic keeps today's behaviour) ---
fill_strategy: "deterministic"   # "deterministic" | "agent"
agent_model_deployment: ""       # blank reuses azure_openai_deployment
max_steps: 20
no_progress_limit: 5
traces_dir: "./traces"
```

- [ ] **Step 6: Commit**

```bash
git add src/formfiller/config.py config.yaml tests/test_config.py
git commit -m "feat(agent): add fill_strategy and agent loop config knobs"
```

---

## Task 2: Agent data models

**Files:**
- Create: `src/formfiller/agent/__init__.py` (empty)
- Create: `src/formfiller/agent/models.py`
- Create: `tests/agent/__init__.py` (empty)
- Test: `tests/agent/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_models.py`:

```python
from formfiller.agent.models import (
    SnapshotElement, PageSnapshot, ToolCall, ToolResult, LoopOutcome,
)


def test_snapshot_and_signature():
    el = SnapshotElement(ref="e1", role="textbox", name="SIREN", value="", state={"required": True})
    snap = PageSnapshot(url="http://x", title="Form", elements=(el,), blocker=None)
    assert snap.signature() == snap.signature()           # stable
    el2 = SnapshotElement(ref="e1", role="textbox", name="SIREN", value="123", state={})
    other = PageSnapshot(url="http://x", title="Form", elements=(el2,), blocker=None)
    assert snap.signature() != other.signature()          # value change shows progress


def test_toolresult_terminal_defaults_none():
    tc = ToolCall(call_id="c1", name="read_snapshot", arguments={})
    res = ToolResult(call_id="c1", name="read_snapshot", output={"ok": True})
    assert res.terminal is None
    assert tc.arguments == {}


def test_loopoutcome_fields():
    out = LoopOutcome(status="dry_run", reason="ok", fields_filled=3, steps=7)
    assert out.status == "dry_run"
    assert out.screenshot is None and out.schema is None and out.mapping is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_models.py -v`
Expected: FAIL (`ModuleNotFoundError: formfiller.agent.models`).

- [ ] **Step 3: Create the package and models**

Create empty `src/formfiller/agent/__init__.py` and `tests/agent/__init__.py`.

Create `src/formfiller/agent/models.py`:

```python
from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

from formfiller.models import FormSchema, MappingResult

_FROZEN = ConfigDict(frozen=True)


class SnapshotElement(BaseModel):
    model_config = _FROZEN
    ref: str                       # data-ff-ref id, e.g. "e3"
    role: str                      # accessible role / tag, e.g. "textbox", "button", "radio"
    name: str                      # accessible name / visible text
    value: str = ""
    state: dict[str, Any] = {}     # checked / required / disabled / etc.


class PageSnapshot(BaseModel):
    model_config = _FROZEN
    url: str
    title: str
    elements: tuple[SnapshotElement, ...]
    blocker: Optional[str] = None  # "login" | "consent" | "captcha" | None

    def signature(self) -> str:
        """Stable string capturing structure+values, for no-progress detection."""
        parts = [self.url, self.title, self.blocker or ""]
        for e in self.elements:
            parts.append(f"{e.ref}|{e.role}|{e.name}|{e.value}|{json.dumps(e.state, sort_keys=True)}")
        return "\n".join(parts)


class ToolCall(BaseModel):
    model_config = _FROZEN
    call_id: str
    name: str
    arguments: dict[str, Any] = {}


class ToolResult(BaseModel):
    model_config = _FROZEN
    call_id: str
    name: str
    output: dict[str, Any]
    # When set, the loop ends with this status.
    terminal: Optional[Literal["submitted", "dry_run", "review", "fail"]] = None
    reason: str = ""
    screenshot: Optional[bytes] = None
    schema: Optional[FormSchema] = None
    mapping: Optional[MappingResult] = None


class LoopOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    status: Literal["submitted", "dry_run", "review", "abort", "fail"]
    reason: str = ""
    fields_filled: int = 0
    steps: int = 0
    screenshot: Optional[bytes] = None
    schema: Optional[FormSchema] = None
    mapping: Optional[MappingResult] = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/agent/__init__.py src/formfiller/agent/models.py tests/agent/__init__.py tests/agent/test_models.py
git commit -m "feat(agent): add snapshot/tool/outcome data models"
```

---

## Task 3: JSONL step trace

**Files:**
- Create: `src/formfiller/agent/trace.py`
- Test: `tests/agent/test_trace.py`

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_trace.py`:

```python
import json
from formfiller.agent.trace import TraceWriter


def test_trace_writes_jsonl_lines(tmp_path):
    w = TraceWriter(tmp_path / "traces", run_id="run-1")
    w.write({"step": 1, "tool": "read_snapshot"})
    w.write({"step": 2, "tool": "submit"})
    path = w.path
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["tool"] == "read_snapshot"
    assert json.loads(lines[1])["step"] == 2


def test_trace_run_id_in_filename(tmp_path):
    w = TraceWriter(tmp_path / "traces", run_id="abc")
    assert "abc" in w.path.name and w.path.suffix == ".jsonl"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_trace.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/formfiller/agent/trace.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TraceWriter:
    """Append-only JSONL writer, one file per run. Never raises on write
    (tracing must not crash a run)."""

    def __init__(self, traces_dir: str | Path, run_id: str) -> None:
        self.dir = Path(traces_dir)
        self.run_id = run_id
        self.path = self.dir / f"{run_id}.jsonl"
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 — tracing is best-effort
            print(f"[warn] trace write failed: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_trace.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/agent/trace.py tests/agent/test_trace.py
git commit -m "feat(agent): add JSONL step-trace writer"
```

---

## Task 4: Blocker detection

**Files:**
- Create: `src/formfiller/agent/blockers.py`
- Test: `tests/agent/test_blockers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_blockers.py`:

```python
from formfiller.agent.blockers import detect_blocker


def _raw(url="https://forms.office.com/r/x", elements=None, has_captcha_frame=False):
    return {"url": url, "elements": elements or [], "has_captcha_frame": has_captcha_frame}


def test_detects_password_login():
    raw = _raw(elements=[{"role": "textbox", "name": "Password", "type": "password"}])
    assert detect_blocker(raw) == "login"


def test_detects_login_by_url():
    raw = _raw(url="https://login.microsoftonline.com/abc", elements=[])
    assert detect_blocker(raw) == "login"


def test_detects_consent_banner():
    raw = _raw(elements=[{"role": "button", "name": "Accept all cookies", "type": ""}])
    assert detect_blocker(raw) == "consent"


def test_detects_captcha_frame():
    assert detect_blocker(_raw(has_captcha_frame=True)) == "captcha"


def test_no_blocker_on_normal_form():
    raw = _raw(elements=[{"role": "textbox", "name": "SIREN", "type": "text"}])
    assert detect_blocker(raw) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_blockers.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/formfiller/agent/blockers.py`:

```python
from __future__ import annotations

from typing import Any, Optional

_LOGIN_HOSTS = ("login.microsoftonline.com", "login.live.com", "accounts.google.com", "/login")
_CONSENT_TEXTS = ("accept all", "accept cookies", "accept all cookies", "agree", "tout accepter",
                  "accepter", "j'accepte")


def detect_blocker(raw: dict[str, Any]) -> Optional[str]:
    """Heuristically classify a page as a login / consent / captcha blocker.

    `raw` is the dict produced by perception's page evaluate:
    {"url": str, "elements": [{"role","name","type"}], "has_captcha_frame": bool}.
    Order matters: captcha first (never auto-solve), then login, then consent.
    """
    if raw.get("has_captcha_frame"):
        return "captcha"

    url = (raw.get("url") or "").lower()
    if any(h in url for h in _LOGIN_HOSTS):
        return "login"

    elements = raw.get("elements") or []
    for e in elements:
        if (e.get("type") or "").lower() == "password":
            return "login"

    for e in elements:
        name = (e.get("name") or "").strip().lower()
        if (e.get("role") or "") == "button" and any(t == name or t in name for t in _CONSENT_TEXTS):
            return "consent"

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_blockers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/agent/blockers.py tests/agent/test_blockers.py
git commit -m "feat(agent): add login/consent/captcha blocker detection"
```

---

## Task 5: Perception — page snapshot with stable refs

**Files:**
- Create: `src/formfiller/agent/perception.py`
- Test: `tests/agent/test_perception.py`

The pure function `build_snapshot(raw)` turns the JS-evaluate dict into a `PageSnapshot` (unit-tested, no browser). `read_snapshot(page)` runs the JS (which also tags each interactive element with `data-ff-ref`) and calls `build_snapshot`. The tagging lets later primitives locate elements via `[data-ff-ref="..."]`.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_perception.py`:

```python
from formfiller.agent.perception import build_snapshot, read_snapshot


def test_build_snapshot_maps_elements_and_blocker():
    raw = {
        "url": "https://forms.office.com/r/x",
        "title": "E-invoicing",
        "has_captcha_frame": False,
        "elements": [
            {"ref": "e0", "role": "button", "name": "Start now", "value": "", "type": "",
             "state": {}},
            {"ref": "e1", "role": "textbox", "name": "SIREN", "value": "", "type": "text",
             "state": {"required": True}},
        ],
    }
    snap = build_snapshot(raw)
    assert snap.url.endswith("/r/x")
    assert snap.title == "E-invoicing"
    assert snap.blocker is None
    assert [e.ref for e in snap.elements] == ["e0", "e1"]
    assert snap.elements[1].state["required"] is True


def test_build_snapshot_sets_blocker():
    raw = {"url": "https://login.microsoftonline.com/x", "title": "Sign in",
           "has_captcha_frame": False, "elements": []}
    assert build_snapshot(raw).blocker == "login"


class _FakePage:
    """Stands in for a Playwright Page: returns canned evaluate output."""
    def __init__(self, raw):
        self._raw = raw
        self.evaluated = []

    def evaluate(self, js):
        self.evaluated.append(js)
        return self._raw


def test_read_snapshot_uses_page_evaluate():
    raw = {"url": "u", "title": "t", "has_captcha_frame": False,
           "elements": [{"ref": "e0", "role": "textbox", "name": "A", "value": "",
                         "type": "text", "state": {}}]}
    page = _FakePage(raw)
    snap = read_snapshot(page)
    assert snap.elements[0].ref == "e0"
    assert page.evaluated, "read_snapshot must call page.evaluate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_perception.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/formfiller/agent/perception.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_perception.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/agent/perception.py tests/agent/test_perception.py
git commit -m "feat(agent): add a11y/DOM snapshot perception with stable refs"
```

---

## Task 6: Tool executor — perception, power tools, primitives, control (no submit yet)

**Files:**
- Create: `src/formfiller/agent/tools.py`
- Test: `tests/agent/test_tools.py`

`ToolExecutor` holds the live `page`, the form `url`, a `mapper: Callable[[FormSchema], MappingResult]` (same shape as `PipelineHooks.map_fields`), `threshold`, `dry_run`, and a `confirm: Callable[[str], bool]`. `dispatch(ToolCall) -> ToolResult`. This task implements every tool **except** `submit` (Task 7).

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_tools.py`:

```python
import pytest

from formfiller.agent.models import ToolCall
from formfiller.agent.tools import ToolExecutor, TOOL_SCHEMAS
from formfiller.models import FormQuestion, FormSchema, MappingResult, MappedAnswer, QuestionType


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        return 1

    def click(self, **kw):
        self.page.calls.append(("click", self.selector))

    def fill(self, text, **kw):
        self.page.calls.append(("fill", self.selector, text))

    def select_option(self, label=None, **kw):
        self.page.calls.append(("select", self.selector, label))


class FakePage:
    def __init__(self, raw=None, title="Form", url="https://forms.office.com/r/x"):
        self.calls = []
        self._raw = raw or {"url": url, "title": title, "has_captcha_frame": False,
                            "elements": [{"ref": "e0", "role": "button", "name": "Next",
                                          "value": "", "type": "", "state": {}}]}
        self._title = title
        self._url = url

    def evaluate(self, js):
        if "scrollBy" in js:
            self.calls.append(("scroll",))
            return None
        return self._raw

    def locator(self, selector):
        return FakeLocator(self, selector)

    def title(self):
        return self._title

    def goto(self, url, **kw):
        self.calls.append(("goto", url))

    def get_by_role(self, role, name=None):
        return FakeLocator(self, f"role={role}")


_SCHEMA = FormSchema(
    url="https://forms.office.com/r/x", title="Form",
    questions=(FormQuestion(id="ms:0", label="SIREN", type=QuestionType.TEXT, required=True),),
)


def _executor(page, mapper=None):
    return ToolExecutor(
        page=page, url="https://forms.office.com/r/x",
        schema_reader=lambda: _SCHEMA,
        mapper=mapper or (lambda schema: MappingResult(answers=(
            MappedAnswer(question_id="ms:0", profile_field="siren", value="123456789",
                         confidence=0.95, status="matched"),))),
        threshold=0.8, dry_run=True, confirm=lambda s: False,
    )


def test_tool_schemas_have_required_keys():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {"read_snapshot", "extract_questions", "lookup_profile", "answer_question",
            "click", "fill", "select_choice", "scroll", "navigate_next", "goto",
            "detect_blocker", "request_human", "submit", "finish"} <= names
    for t in TOOL_SCHEMAS:
        assert t["type"] == "function"
        assert "parameters" in t and t["parameters"]["type"] == "object"


def test_read_snapshot_tool():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="read_snapshot", arguments={}))
    assert res.terminal is None
    assert res.output["elements"][0]["ref"] == "e0"


def test_extract_questions_tool():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="extract_questions", arguments={}))
    assert res.output["questions"][0]["id"] == "ms:0"


def test_answer_question_calls_fill_form(monkeypatch):
    captured = {}
    import formfiller.agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "fill_form",
                        lambda page, instr: captured.setdefault("instr", list(instr)))
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="answer_question",
                               arguments={"question_id": "ms:0", "value": "123456789"}))
    assert res.output["status"] == "filled"
    assert captured["instr"][0].question_id == "ms:0"
    assert captured["instr"][0].value == "123456789"


def test_click_primitive_uses_ref_selector():
    page = FakePage()
    ex = _executor(page)
    ex.dispatch(ToolCall(call_id="c", name="click", arguments={"ref": "e0"}))
    assert ("click", '[data-ff-ref="e0"]') in page.calls


def test_request_human_is_terminal_review():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="request_human",
                               arguments={"reason": "captcha"}))
    assert res.terminal == "review"
    assert "captcha" in res.reason


def test_finish_not_ready_is_terminal_review():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="finish",
                               arguments={"ready_to_submit": False, "summary": "nothing to do"}))
    assert res.terminal == "review"


def test_unknown_tool_returns_error_not_raises():
    ex = _executor(FakePage())
    res = ex.dispatch(ToolCall(call_id="c", name="nope", arguments={}))
    assert res.terminal is None
    assert "error" in res.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_tools.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement (tools except submit)**

Create `src/formfiller/agent/tools.py`:

```python
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from formfiller.agent.models import PageSnapshot, ToolCall, ToolResult
from formfiller.agent.perception import read_snapshot
from formfiller.confidence import FillInstruction, evaluate_gate
from formfiller.form_filler import fill_form, submit_form, take_screenshot
from formfiller.form_reader import schema_from_page
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
        return ToolResult(call_id=call.call_id, name=call.name,
                          output={"control": "finish", "summary": summary},
                          terminal="review",
                          reason=summary or "agent finished without submitting")

    def _tool_submit(self, call: ToolCall) -> ToolResult:
        raise NotImplementedError("submit guard added in Task 7")

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
```

Note: the test injects `schema_reader`; in production (Task 10) it is `lambda: schema_from_page(self.page, self.url)`. `schema_from_page` is imported so production wiring needs no extra import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/agent/tools.py tests/agent/test_tools.py
git commit -m "feat(agent): add tool executor (perception, power tools, primitives, control)"
```

---

## Task 7: Submit guard

**Files:**
- Modify: `src/formfiller/agent/tools.py` (replace `_tool_submit`)
- Test: `tests/agent/test_submit_guard.py`

Guard sequence: recompute `schema_from_page` → `mapper` → `evaluate_gate`. Gate `review` → fill proposed values for the screenshot, terminal `review`. Gate `submit` → fill values + screenshot; if `dry_run` → terminal `dry_run`; else confirm → real `submit_form`, terminal `submitted`/`fail`; decline → terminal `review`.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_submit_guard.py`:

```python
from formfiller.agent.models import ToolCall
from formfiller.agent.tools import ToolExecutor
from formfiller.models import FormQuestion, FormSchema, MappingResult, MappedAnswer, QuestionType
import formfiller.agent.tools as tools_mod


_SCHEMA = FormSchema(
    url="https://forms.office.com/r/x", title="Form",
    questions=(FormQuestion(id="ms:0", label="SIREN", type=QuestionType.TEXT, required=True),),
)
_GOOD = MappingResult(answers=(MappedAnswer(question_id="ms:0", profile_field="siren",
        value="123456789", confidence=0.95, status="matched"),))
_BAD = MappingResult(answers=(MappedAnswer(question_id="ms:0", profile_field=None,
        value=None, confidence=0.0, status="no_data"),))


class _Page:
    def title(self):
        return "Form"


def _executor(mapping, dry_run, confirm, monkeypatch):
    monkeypatch.setattr(tools_mod, "fill_form", lambda page, instr: None)
    monkeypatch.setattr(tools_mod, "take_screenshot", lambda page: b"\x89PNG")
    monkeypatch.setattr(tools_mod, "submit_form", lambda page, dry_run: True)
    return ToolExecutor(page=_Page(), url="https://forms.office.com/r/x",
                        schema_reader=lambda: _SCHEMA, mapper=lambda s: mapping,
                        threshold=0.8, dry_run=dry_run, confirm=confirm)


def test_gate_review_refuses_submit(monkeypatch):
    ex = _executor(_BAD, dry_run=False, confirm=lambda s: True, monkeypatch=monkeypatch)
    res = ex.dispatch(ToolCall(call_id="c", name="submit", arguments={"summary": "go"}))
    assert res.terminal == "review"
    assert res.screenshot == b"\x89PNG"
    assert res.schema is not None and res.mapping is not None


def test_dry_run_does_not_submit(monkeypatch):
    submitted = {"called": False}
    monkeypatch.setattr(tools_mod, "submit_form",
                        lambda page, dry_run: submitted.__setitem__("called", True))
    ex = _executor(_GOOD, dry_run=True, confirm=lambda s: True, monkeypatch=monkeypatch)
    res = ex.dispatch(ToolCall(call_id="c", name="submit", arguments={"summary": "go"}))
    assert res.terminal == "dry_run"
    assert submitted["called"] is False
    assert res.screenshot == b"\x89PNG"


def test_real_submit_requires_confirmation(monkeypatch):
    ex = _executor(_GOOD, dry_run=False, confirm=lambda s: False, monkeypatch=monkeypatch)
    res = ex.dispatch(ToolCall(call_id="c", name="submit", arguments={"summary": "go"}))
    assert res.terminal == "review"
    assert "declin" in res.reason.lower() or "confirm" in res.reason.lower()


def test_confirmed_submit_is_terminal_submitted(monkeypatch):
    ex = _executor(_GOOD, dry_run=False, confirm=lambda s: True, monkeypatch=monkeypatch)
    res = ex.dispatch(ToolCall(call_id="c", name="submit", arguments={"summary": "go"}))
    assert res.terminal == "submitted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_submit_guard.py -v`
Expected: FAIL (`NotImplementedError`).

- [ ] **Step 3: Replace `_tool_submit`**

In `src/formfiller/agent/tools.py`, replace the `_tool_submit` stub with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_submit_guard.py tests/agent/test_tools.py -v`
Expected: PASS (both files).

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/agent/tools.py tests/agent/test_submit_guard.py
git commit -m "feat(agent): add guarded submit (gate + dry_run + human confirm)"
```

---

## Task 8: LLM adapter + protocol

**Files:**
- Create: `src/formfiller/agent/llm.py`
- Test: `tests/agent/test_llm.py`

`AgentLLM` is a Protocol with one method `respond`. `OpenAIResponsesAgentLLM` wraps `client.responses.create(...)` and extracts `function_call` items from `response.output`. A `FakeLLM` (test helper) replays scripted turns; it lives in the test file and is reused by Task 9 via import.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_llm.py`:

```python
from formfiller.agent.llm import OpenAIResponsesAgentLLM, LLMTurn


class _FnCall:
    type = "function_call"
    def __init__(self, name, args, call_id):
        self.name = name
        self.arguments = args
        self.call_id = call_id


class _Resp:
    def __init__(self, output, rid="resp-1"):
        self.output = output
        self.id = rid
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


class _Client:
    def __init__(self, resp):
        self.responses = self
        self._resp = resp
        self.last_kwargs = None
    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._resp


def test_respond_parses_function_calls():
    resp = _Resp([_FnCall("read_snapshot", "{}", "call_1"),
                  _FnCall("answer_question", '{"question_id":"ms:0","value":"x"}', "call_2")])
    llm = OpenAIResponsesAgentLLM(_Client(resp), deployment="gpt-5.4-nano",
                                  instructions="sys")
    turn = llm.respond(previous_response_id=None, input="go", tools=[{"type": "function",
            "name": "read_snapshot", "parameters": {"type": "object", "properties": {}}}])
    assert isinstance(turn, LLMTurn)
    assert turn.response_id == "resp-1"
    assert [c.name for c in turn.tool_calls] == ["read_snapshot", "answer_question"]
    assert turn.tool_calls[1].arguments == {"question_id": "ms:0", "value": "x"}


def test_respond_passes_previous_id_and_tools():
    client = _Client(_Resp([]))
    llm = OpenAIResponsesAgentLLM(client, deployment="d", instructions="sys")
    llm.respond(previous_response_id="resp-prev",
                input=[{"type": "function_call_output", "call_id": "c", "output": "{}"}],
                tools=[])
    assert client.last_kwargs["previous_response_id"] == "resp-prev"
    assert client.last_kwargs["model"] == "d"
    # first turn sends instructions; continuation turns should not resend them
    assert client.last_kwargs.get("instructions") in (None, "sys")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_llm.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/formfiller/agent/llm.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Union, runtime_checkable

from formfiller.agent.models import ToolCall


@dataclass
class LLMTurn:
    response_id: str
    tool_calls: tuple[ToolCall, ...]
    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentLLM(Protocol):
    def respond(self, *, previous_response_id: Optional[str],
                input: Union[str, list[dict]], tools: list[dict]) -> LLMTurn: ...


class OpenAIResponsesAgentLLM:
    """Drives the Azure v1 Responses API function-calling loop.

    `client` is an openai.OpenAI pointed at <endpoint>/openai/v1/ (see cli.py).
    Instructions are sent only on the first turn; continuation turns rely on
    previous_response_id to carry context.
    """

    def __init__(self, client, *, deployment: str, instructions: str,
                 max_output_tokens: int = 16000) -> None:
        self.client = client
        self.deployment = deployment
        self.instructions = instructions
        self.max_output_tokens = max_output_tokens

    def respond(self, *, previous_response_id, input, tools) -> LLMTurn:
        kwargs: dict[str, Any] = {
            "model": self.deployment,
            "input": input,
            "tools": tools,
            "tool_choice": "auto",
            "max_output_tokens": self.max_output_tokens,
        }
        if previous_response_id is None:
            kwargs["instructions"] = self.instructions
        else:
            kwargs["previous_response_id"] = previous_response_id

        resp = self.client.responses.create(**kwargs)

        calls = []
        for item in (getattr(resp, "output", None) or []):
            if getattr(item, "type", None) == "function_call":
                try:
                    args = json.loads(item.arguments) if item.arguments else {}
                except (TypeError, ValueError):
                    args = {}
                calls.append(ToolCall(call_id=item.call_id, name=item.name, arguments=args))

        usage = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = {"input_tokens": getattr(u, "input_tokens", None),
                     "output_tokens": getattr(u, "output_tokens", None)}

        text = getattr(resp, "output_text", "") or ""
        return LLMTurn(response_id=resp.id, tool_calls=tuple(calls), text=text, usage=usage)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_llm.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/agent/llm.py tests/agent/test_llm.py
git commit -m "feat(agent): add Responses API function-calling LLM adapter"
```

---

## Task 9: Loop driver

**Files:**
- Create: `src/formfiller/agent/loop.py`
- Create: `tests/agent/conftest.py` (FakeLLM + FakeExecutor helpers)
- Test: `tests/agent/test_loop.py`

`run_loop` repeatedly calls `llm.respond`, dispatches each tool call through the executor, writes a trace record per step, feeds `function_call_output` items back, and ends on: a terminal ToolResult, no tool calls (review), the step budget (abort), or the no-progress breaker (abort).

- [ ] **Step 1: Write the failing test + fakes**

Create `tests/agent/conftest.py`:

```python
from formfiller.agent.llm import LLMTurn
from formfiller.agent.models import ToolCall, ToolResult


class FakeLLM:
    """Replays scripted turns. Each scripted turn is a list of (name, args)."""
    def __init__(self, scripted_turns):
        self._turns = list(scripted_turns)
        self.calls = 0

    def respond(self, *, previous_response_id, input, tools):
        if not self._turns:
            return LLMTurn(response_id=f"r{self.calls}", tool_calls=())  # no action → review
        turn = self._turns.pop(0)
        self.calls += 1
        tcs = tuple(ToolCall(call_id=f"c{self.calls}_{i}", name=n, arguments=a)
                    for i, (n, a) in enumerate(turn))
        return LLMTurn(response_id=f"r{self.calls}", tool_calls=tcs)


class FakeExecutor:
    """Records dispatched calls; returns scripted ToolResults by tool name."""
    def __init__(self, results_by_name, signatures=None):
        self._results = results_by_name
        self._sigs = list(signatures or [])
        self.dispatched = []

    def dispatch(self, call: ToolCall) -> ToolResult:
        self.dispatched.append(call)
        spec = self._results.get(call.name, {"output": {"ok": True}})
        return ToolResult(call_id=call.call_id, name=call.name, **spec)

    def page_signature(self) -> str:
        return self._sigs.pop(0) if self._sigs else "static"
```

Create `tests/agent/test_loop.py`:

```python
from formfiller.agent.loop import run_loop
from tests.agent.conftest import FakeLLM, FakeExecutor


def _trace(records):
    class T:
        def write(self, r):
            records.append(r)
    return T()


def test_loop_ends_on_terminal_submit(tmp_path):
    records = []
    llm = FakeLLM([[("read_snapshot", {})], [("submit", {"summary": "go"})]])
    ex = FakeExecutor({"submit": {"output": {"control": "dry_run"}, "terminal": "dry_run",
                                  "reason": "dry-run"}},
                      signatures=["s1", "s2"])
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=20, no_progress_limit=5, trace=_trace(records))
    assert out.status == "dry_run"
    assert out.steps == 2
    assert len(records) == 2


def test_loop_aborts_on_max_steps():
    llm = FakeLLM([[("read_snapshot", {})]] * 50)
    ex = FakeExecutor({}, signatures=[f"s{i}" for i in range(50)])
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=3, no_progress_limit=99, trace=_trace([]))
    assert out.status == "abort"
    assert "max steps" in out.reason.lower()


def test_loop_aborts_on_no_progress():
    llm = FakeLLM([[("read_snapshot", {})]] * 50)
    ex = FakeExecutor({}, signatures=["same"] * 50)   # signature never changes
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=50, no_progress_limit=3, trace=_trace([]))
    assert out.status == "abort"
    assert "progress" in out.reason.lower()


def test_loop_review_when_model_emits_no_tool_calls():
    llm = FakeLLM([])   # immediately returns no tool calls
    ex = FakeExecutor({})
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=5, no_progress_limit=5, trace=_trace([]))
    assert out.status == "review"


def test_loop_carries_screenshot_and_schema_from_terminal(tmp_path):
    from formfiller.models import FormSchema, MappingResult
    schema = FormSchema(url="u", title="t", questions=())
    mapping = MappingResult(answers=())
    llm = FakeLLM([[("submit", {"summary": "go"})]])
    ex = FakeExecutor({"submit": {"output": {}, "terminal": "submitted", "reason": "ok",
                                  "screenshot": b"\x89PNG", "schema": schema, "mapping": mapping}})
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=5, no_progress_limit=5, trace=_trace([]))
    assert out.status == "submitted"
    assert out.screenshot == b"\x89PNG"
    assert out.schema is schema and out.mapping is mapping
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_loop.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/formfiller/agent/loop.py`:

```python
from __future__ import annotations

import json
from typing import Any, Optional

from formfiller.agent.models import LoopOutcome


def run_loop(llm, executor, *, instructions: str, user_input: str,
             tools: list[dict], max_steps: int, no_progress_limit: int,
             trace) -> LoopOutcome:
    """Drive the function-calling loop until a terminal tool result, no action,
    the step budget, or the no-progress breaker. Returns a LoopOutcome."""
    prev_id: Optional[str] = None
    next_input: Any = user_input
    last_sig: Optional[str] = None
    no_progress = 0
    fields_filled = 0

    for step in range(1, max_steps + 1):
        turn = llm.respond(previous_response_id=prev_id, input=next_input, tools=tools)
        prev_id = turn.response_id

        if not turn.tool_calls:
            trace.write({"step": step, "event": "no_tool_calls", "text": turn.text,
                         "usage": turn.usage})
            return LoopOutcome(status="review", reason="agent stopped without an action",
                               fields_filled=fields_filled, steps=step)

        outputs = []
        for call in turn.tool_calls:
            result = executor.dispatch(call)
            trace.write({"step": step, "tool": call.name, "arguments": call.arguments,
                         "observation": result.output, "terminal": result.terminal,
                         "usage": turn.usage})
            if call.name == "answer_question" and result.output.get("status") == "filled":
                fields_filled += 1
            outputs.append({"type": "function_call_output", "call_id": call.call_id,
                            "output": json.dumps(result.output, default=str)})
            if result.terminal is not None:
                return LoopOutcome(status=result.terminal, reason=result.reason,
                                   fields_filled=fields_filled, steps=step,
                                   screenshot=result.screenshot, schema=result.schema,
                                   mapping=result.mapping)

        sig = executor.page_signature()
        no_progress = no_progress + 1 if sig == last_sig else 0
        last_sig = sig
        if no_progress >= no_progress_limit:
            trace.write({"step": step, "event": "no_progress_abort"})
            return LoopOutcome(status="abort", reason="no progress after repeated steps",
                               fields_filled=fields_filled, steps=step)

        next_input = outputs

    trace.write({"event": "max_steps_abort", "steps": max_steps})
    return LoopOutcome(status="abort", reason="max steps reached",
                       fields_filled=fields_filled, steps=max_steps)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_loop.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/agent/loop.py tests/agent/conftest.py tests/agent/test_loop.py
git commit -m "feat(agent): add loop driver with step budget, no-progress breaker, trace"
```

---

## Task 10: Agent pipeline + system prompt + outcome→JobResult + fallback

**Files:**
- Create: `src/formfiller/agent/pipeline.py`
- Test: `tests/agent/test_pipeline.py`

`run_agent_pipeline(email, config, profile, agent_deps, det_hooks)` mirrors `process_email`'s logging discipline: find link (NoFormLinkError → fail row), open page via `agent_deps.open_page()`, build executor + llm, run loop, map outcome → `JobResult` (logging once), park reviews, save dry-run preview. On `abort`/`fail` it delegates to `process_email(email, config, profile, det_hooks)` (which logs once) and returns that.

`AgentDeps` injects the browser-session factory and the LLM/mapper factories so the pipeline is testable without Playwright/Azure.

- [ ] **Step 1: Write the failing test**

Create `tests/agent/test_pipeline.py`:

```python
from contextlib import contextmanager

from formfiller.agent.models import LoopOutcome
from formfiller.agent.pipeline import run_agent_pipeline, AgentDeps
from formfiller.config import AppConfig, ProfileField
from formfiller.models import EmailMessage, FormSchema, MappingResult
from formfiller.orchestrator import PipelineHooks


def _email(body="link https://forms.office.com/r/x"):
    return EmailMessage(entry_id="E1", sender="c@acme.com", subject="s",
                        received="2026-06-10T09:00:00", body_text=body, body_html="")


def _config(tmp_path, dry_run=True):
    return AppConfig(excel_log_path=str(tmp_path / "log.xlsx"),
                     review_queue_dir=str(tmp_path / "queue"),
                     traces_dir=str(tmp_path / "traces"),
                     dry_run=dry_run, fill_strategy="agent",
                     azure_openai_deployment="gpt-5.4-nano")


_PROFILE = (ProfileField(name="siren", value="123456789", aliases=()),)


def _deps(outcome):
    @contextmanager
    def open_page():
        yield object()
    return AgentDeps(
        open_page=open_page,
        run=lambda page, url, config, profile, trace: outcome,
    )


def test_dry_run_outcome_logs_success_and_saves_preview(tmp_path):
    out = LoopOutcome(status="dry_run", reason="dry-run", fields_filled=2, steps=4,
                      screenshot=b"\x89PNG")
    result = run_agent_pipeline(_email(), _config(tmp_path), _PROFILE, _deps(out),
                                det_hooks=None)
    assert result.status == "success"
    assert result.fields_filled == 2
    preview = tmp_path / "dry_run_preview" / "E1.png"
    assert preview.exists() and preview.read_bytes() == b"\x89PNG"


def test_review_outcome_parks_and_logs_manual(tmp_path):
    schema = FormSchema(url="https://forms.office.com/r/x", title="t", questions=())
    out = LoopOutcome(status="review", reason="captcha", steps=1,
                      screenshot=b"\x89PNG", schema=schema, mapping=MappingResult(answers=()))
    result = run_agent_pipeline(_email(), _config(tmp_path), _PROFILE, _deps(out),
                                det_hooks=None)
    assert result.status == "manual"
    assert "captcha" in result.review_reason
    assert (tmp_path / "queue" / "E1").exists()


def test_no_link_logs_fail(tmp_path):
    out = LoopOutcome(status="dry_run", reason="x")
    result = run_agent_pipeline(_email("no link"), _config(tmp_path), _PROFILE, _deps(out),
                                det_hooks=None)
    assert result.status == "fail"
    assert "link" in result.review_reason.lower()


def test_abort_falls_back_to_deterministic(tmp_path):
    out = LoopOutcome(status="abort", reason="max steps")
    # deterministic hooks produce a clean submit
    from formfiller.models import FormQuestion, QuestionType, MappedAnswer
    schema = FormSchema(url="https://forms.office.com/r/x", title="t",
                        questions=(FormQuestion(id="q1", label="SIREN",
                                   type=QuestionType.TEXT, required=True),))
    mapping = MappingResult(answers=(MappedAnswer(question_id="q1", profile_field="siren",
              value="123456789", confidence=0.95, status="matched"),))
    det_hooks = PipelineHooks(read_form=lambda url: schema,
                              map_fields=lambda s: mapping,
                              fill_and_submit=lambda url, instr, dry: (b"\x89PNG", False))
    result = run_agent_pipeline(_email(), _config(tmp_path, dry_run=True), _PROFILE,
                                _deps(out), det_hooks=det_hooks)
    # deterministic dry-run success
    assert result.status == "success"
    # exactly one Excel row (the fallback's), not two
    from openpyxl import load_workbook
    rows = load_workbook(tmp_path / "log.xlsx").active.max_row
    assert rows == 2  # header + one data row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/agent/test_pipeline.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/formfiller/agent/pipeline.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, ContextManager, Optional

from formfiller.agent.models import LoopOutcome
from formfiller.config import AppConfig, ProfileField
from formfiller.link_extractor import NoFormLinkError, extract_form_url
from formfiller.models import EmailMessage, FormSchema, MappingResult
from formfiller.orchestrator import PipelineHooks, process_email
from formfiller.result_logger import JobResult, append_result
from formfiller.review_queue import park_for_review


@dataclass
class AgentDeps:
    """Injected browser/LLM seams so the pipeline is testable without Playwright/Azure.

    open_page() -> context manager yielding a Playwright Page.
    run(page, url, config, profile, trace) -> LoopOutcome  (builds executor+llm, runs loop).
    """
    open_page: Callable[[], ContextManager]
    run: Callable[..., LoopOutcome]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _form_type(url: str) -> str:
    if "google" in url or "forms.gle" in url:
        return "google_forms"
    if "office.com" in url or "microsoft.com" in url:
        return "ms_forms"
    return "other"


def run_agent_pipeline(email: EmailMessage, config: AppConfig,
                       profile: tuple[ProfileField, ...], deps: AgentDeps,
                       det_hooks: Optional[PipelineHooks]) -> JobResult:
    base = dict(timestamp=_now_iso(), sender=email.sender,
                client_name=email.sender.split("@")[-1].split(".")[0]
                if "@" in email.sender else email.sender,
                form_url="", form_type="", overall_confidence=0.0, fields_filled=0,
                fields_blank_flagged="", review_reason="", screenshot_path="")

    def _finish(**overrides) -> JobResult:
        result = JobResult(**{**base, **overrides})
        try:
            written = append_result(config.excel_log_path, result)
            if Path(written) != Path(config.excel_log_path):
                print(f"[warn] log file was locked; wrote to sidecar: {written}")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] could not write log ({exc}); result not logged.")
        return result

    # 1. Find link.
    try:
        url = extract_form_url(email.body_html, email.body_text)
    except NoFormLinkError as exc:
        return _finish(status="fail", review_reason=f"No form link: {exc}")
    base["form_url"] = url
    base["form_type"] = _form_type(url)

    # 2. Run the agent loop.
    from formfiller.agent.trace import TraceWriter
    trace = TraceWriter(config.traces_dir, run_id=email.entry_id)
    try:
        with deps.open_page() as page:
            outcome = deps.run(page=page, url=url, config=config, profile=profile, trace=trace)
    except Exception as exc:  # noqa: BLE001 — isolate one bad form
        outcome = LoopOutcome(status="fail", reason=f"agent error: {exc}")

    # 3. Fallback ladder: abort/fail → deterministic pipeline (which logs its own row).
    if outcome.status in ("abort", "fail"):
        if det_hooks is not None:
            return process_email(email, config, profile, det_hooks)
        return _finish(status="fail",
                       review_reason=f"agent {outcome.status}: {outcome.reason}")

    # 4. Map agent outcome → JobResult (log exactly once).
    if outcome.status == "review":
        schema = outcome.schema or FormSchema(url=url, title="", questions=())
        mapping = outcome.mapping or MappingResult(answers=())
        park_for_review(queue_dir=config.review_queue_dir, job_id=email.entry_id,
                        schema=schema, result=mapping, reason=outcome.reason,
                        screenshot_bytes=outcome.screenshot)
        return _finish(status="manual", review_reason=outcome.reason,
                       fields_filled=outcome.fields_filled,
                       screenshot_path=str(Path(config.review_queue_dir) / email.entry_id
                                           / "screenshot.png"))

    # submitted | dry_run
    preview_path = ""
    if outcome.status == "dry_run" and outcome.screenshot:
        preview = Path(config.excel_log_path).parent / "dry_run_preview" / f"{email.entry_id}.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(outcome.screenshot)
        preview_path = str(preview)

    reason = ("dry-run: filled but not submitted (preview saved — verify before enabling "
              "submission)" if outcome.status == "dry_run" else "")
    return _finish(status="success", fields_filled=outcome.fields_filled,
                   review_reason=reason, screenshot_path=preview_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/agent/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/agent/pipeline.py tests/agent/test_pipeline.py
git commit -m "feat(agent): add agent pipeline with outcome mapping and deterministic fallback"
```

---

## Task 11: Wire production deps + CLI strategy branch

**Files:**
- Create: `src/formfiller/agent/system_prompt.py`
- Modify: `src/formfiller/cli.py`
- Test: `tests/test_cli_agent.py`

This task builds the real `AgentDeps` (Playwright page + Azure LLM + mapper) and branches `cli.main()` on `config.fill_strategy`. The page/Azure wiring is covered today by manual dry-run runs (Task 12), so the unit test here asserts only the *selection* logic and the `deps.run` assembly via a fake client.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_agent.py`:

```python
from formfiller.cli import choose_pipeline


def test_choose_pipeline_deterministic():
    assert choose_pipeline("deterministic") == "deterministic"


def test_choose_pipeline_agent():
    assert choose_pipeline("agent") == "agent"


def test_build_agent_run_assembles_executor_and_loop(monkeypatch, tmp_path):
    # A fake client whose first response asks to finish → loop ends as review.
    from formfiller.agent.llm import LLMTurn
    import formfiller.cli as cli

    class _FakeLLM:
        def __init__(self, *a, **k): pass
        def respond(self, *, previous_response_id, input, tools):
            from formfiller.agent.models import ToolCall
            return LLMTurn(response_id="r1",
                           tool_calls=(ToolCall(call_id="c1", name="finish",
                                       arguments={"ready_to_submit": False, "summary": "stop"}),))

    monkeypatch.setattr(cli, "OpenAIResponsesAgentLLM", _FakeLLM)

    from formfiller.config import AppConfig, ProfileField
    cfg = AppConfig(excel_log_path=str(tmp_path / "l.xlsx"), traces_dir=str(tmp_path / "t"),
                    fill_strategy="agent", azure_openai_deployment="dep")
    profile = (ProfileField(name="siren", value="123", aliases=()),)

    run = cli.build_agent_run(client=object(), config=cfg, profile=profile)

    class _Trace:
        def write(self, r): pass
    class _Page:
        def goto(self, *a, **k): pass
        def evaluate(self, js): return {"url": "u", "title": "t", "elements": [],
                                        "has_captcha_frame": False}

    outcome = run(page=_Page(), url="https://forms.office.com/r/x", config=cfg,
                  profile=profile, trace=_Trace())
    assert outcome.status == "review"   # finish(ready=False) → review
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_agent.py -v`
Expected: FAIL (`ImportError: cannot import name 'choose_pipeline'`).

- [ ] **Step 3: Create the system prompt**

Create `src/formfiller/agent/system_prompt.py`:

```python
from __future__ import annotations

SYSTEM_PROMPT = (
    "You are an agent that completes a web form that has already been opened in a browser. "
    "You perceive the page only through tools. Work in a loop: call read_snapshot to see the "
    "page, reason, act with one tool, then observe.\n\n"
    "Strategy:\n"
    "- Prefer the deterministic power tools: call extract_questions to get the form's questions "
    "and their ids, call lookup_profile to get proposed values, then answer_question(id, value) "
    "for each field. These reuse proven matching logic.\n"
    "- Use the primitives (click, fill, select_choice, scroll, navigate_next, goto) only for "
    "things the power tools don't cover: an intro 'Start' button, a cookie/consent banner, a "
    "'Next' button on a multi-page form, or an unusual layout. Address them by the ref from "
    "read_snapshot.\n"
    "- If you see a login wall or a captcha (the snapshot's blocker hint, or detect_blocker), "
    "do NOT try to solve it: call request_human with the reason.\n"
    "- When every required question is answered, call submit with a short summary. A guard will "
    "re-check the confidence gate, honour dry-run, and ask a human before any real submission — "
    "it may refuse, which is fine.\n"
    "- If there is nothing to do or you cannot proceed safely, call finish.\n"
    "Never invent data: only values returned by lookup_profile may be entered."
)
```

- [ ] **Step 4: Add CLI wiring**

In `src/formfiller/cli.py`, add imports near the top:

```python
from formfiller.agent.llm import OpenAIResponsesAgentLLM
```

Add these functions (place above `main()`):

```python
def choose_pipeline(fill_strategy: str) -> str:
    """Return the pipeline name for a fill_strategy value."""
    return "agent" if fill_strategy == "agent" else "deterministic"


def build_agent_run(*, client, config, profile):
    """Build the AgentDeps.run callable: assemble the executor + LLM and run the loop.

    Returns a function run(page, url, config, profile, trace) -> LoopOutcome.
    """
    from formfiller.agent.loop import run_loop
    from formfiller.agent.system_prompt import SYSTEM_PROMPT
    from formfiller.agent.tools import TOOL_SCHEMAS, ToolExecutor
    from formfiller.field_mapper import map_fields
    from formfiller.form_reader import schema_from_page

    deployment = config.agent_model_deployment or config.azure_openai_deployment

    def run(*, page, url, config, profile, trace):
        page.goto(url, wait_until="load")   # start the agent on the form page
        executor = ToolExecutor(
            page=page, url=url,
            schema_reader=lambda: schema_from_page(page, url),
            mapper=lambda schema: map_fields(client, deployment, schema, profile),
            threshold=config.confidence_threshold, dry_run=config.dry_run,
            confirm=_terminal_confirm,
        )
        llm = OpenAIResponsesAgentLLM(client, deployment=deployment, instructions=SYSTEM_PROMPT)
        return run_loop(llm, executor, instructions=SYSTEM_PROMPT,
                        user_input=f"Complete the form at {url}.",
                        tools=TOOL_SCHEMAS, max_steps=config.max_steps,
                        no_progress_limit=config.no_progress_limit, trace=trace)

    return run


def _terminal_confirm(summary: str) -> bool:
    answer = input(f"\nAgent is ready to SUBMIT (irreversible): {summary}\nProceed? [y/N]: ")
    return answer.strip().lower() in ("y", "yes")


def _build_agent_deps(config, profile):
    """Production AgentDeps: real Playwright page + Azure client."""
    import os
    from openai import OpenAI
    from formfiller.agent.pipeline import AgentDeps
    from formfiller.config import azure_v1_base_url
    from formfiller.form_reader import open_page, prepare_form

    client = OpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
                    base_url=azure_v1_base_url(os.environ["AZURE_OPENAI_ENDPOINT"]),
                    default_query={"api-version": config.azure_api_version})
    run = build_agent_run(client=client, config=config, profile=profile)

    from contextlib import contextmanager

    @contextmanager
    def open_session():
        # The page is navigated to the form url inside `run` (build_agent_run);
        # here we just provide a fresh page and tear it down afterward.
        with open_page(headless=True) as page:
            yield page

    return AgentDeps(open_page=open_session, run=run)
```

Then modify `main()` so that after building `profile`/`config` and choosing an email, it branches:

```python
    if choose_pipeline(config.fill_strategy) == "agent":
        from formfiller.agent.pipeline import run_agent_pipeline
        det_hooks = _build_hooks(config, profile)   # fallback path
        agent_deps = _build_agent_deps(config, profile)
        # navigate to the form before handing to the agent
        result = run_agent_pipeline(chosen, config, profile, agent_deps, det_hooks)
    else:
        hooks = _build_hooks(config, profile)
        result = process_email(chosen, config, profile, hooks)
```

Add `from formfiller.orchestrator import process_email` to the imports used by `main()` (it currently imports `process_email` indirectly; add the explicit import at top).

> Test note: the `run` closure calls `page.goto(url, ...)` first (it starts the agent on the form page). The `_Page` fake in `test_build_agent_run_assembles_executor_and_loop` therefore needs a no-op `def goto(self, *a, **k): pass`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_agent.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite regression**

Run: `python -m pytest -q`
Expected: PASS — original 59 + all new agent tests, nothing broken.

- [ ] **Step 7: Commit**

```bash
git add src/formfiller/agent/system_prompt.py src/formfiller/cli.py tests/test_cli_agent.py
git commit -m "feat(agent): wire production AgentDeps and CLI fill_strategy branch"
```

---

## Task 12: Eval corpus + manual dry-run checklist

**Files:**
- Create: `docs/superpowers/eval/agent-fill-eval.md`

No code. A reproducible manual-eval procedure for the LLM loop (the part unit tests can't pin down) plus a place to record results when comparing nano vs a stronger model.

- [ ] **Step 1: Write the eval doc**

Create `docs/superpowers/eval/agent-fill-eval.md`:

```markdown
# Agent Fill-Stage — Manual Eval

The deterministic plumbing is covered by pytest. This is the empirical eval for the
LLM loop itself. Always run with `dry_run: true` and `fill_strategy: agent`.

## Corpus
1. MS Forms test form "Adisséo – E-invoicing (Copie 1)" (6 questions; the verified happy path).
2. A public MS Form with a cookie/consent banner (consent-handling).
3. A public multi-page form with a Next button (navigation + re-extract per page).
4. A form behind a login (must route to human, never attempt login).

## Procedure (per form)
1. Put the form link in a test email (or point the runner at it).
2. Set `config.yaml`: `fill_strategy: agent`, `dry_run: true`.
3. Run the CLI and pick the email.
4. Open `traces/<entry_id>.jsonl` and read the step sequence.
5. Open the dry-run preview screenshot.

## Pass criteria
- [ ] Loop reached `submit` and the guard returned `dry_run` (happy path), OR routed to
      `review`/`request_human` for the right reason (blocker/login/captcha).
- [ ] No `answer_question` used a value not present in `lookup_profile` output.
- [ ] Step count well under `max_steps` (record it).
- [ ] No-progress breaker did not fire on a healthy run.
- [ ] Preview screenshot shows the expected fields filled.

## Record (nano vs stronger model)
| form | model | steps | outcome | notes |
|------|-------|-------|---------|-------|
|      |       |       |         |       |

If nano loops, drifts, or repeatedly mis-orders tools, set `agent_model_deployment` to a
stronger GPT-5 reasoning deployment and re-run the same corpus.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/eval/agent-fill-eval.md
git commit -m "docs(agent): add manual dry-run eval corpus and checklist"
```

---

## Final verification

- [ ] Run the whole suite: `python -m pytest -q` → all green (59 original + new).
- [ ] Confirm `git grep -n "fill_strategy" config.yaml` shows the default is `deterministic`.
- [ ] Confirm `process_email` and its tests are unchanged: `git log --oneline -- src/formfiller/orchestrator.py` shows no new commit touching its logic.
- [ ] Manual: with `fill_strategy: deterministic`, the app behaves exactly as before (smoke test on the MS Forms test form in dry-run).
- [ ] Manual: run the Task 12 eval corpus with `fill_strategy: agent`, `dry_run: true`.
```
