# Email-to-Form Automation POC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A manually-launched Python CLI that lets the user pick an email from their live Outlook inbox, finds the form link, reads the form with Playwright, maps its questions to a fixed data profile using an LLM (Azure OpenAI), auto-submits when confident (else parks for review), and logs the outcome to a synced SharePoint/OneDrive Excel file.

**Architecture:** A pipeline of small, single-responsibility modules communicating via Pydantic objects (`EmailMessage → form URL → FormSchema → MappingResult → GateDecision → JobResult`). Each module sits behind a narrow interface so the Outlook (COM), Playwright (browser), and LLM (Azure OpenAI) dependencies can be faked in tests. The orchestrator wires the pipeline for one chosen email; the CLI handles selection and output. No scheduler.

**Tech Stack:** Python 3.11+, pydantic v2, pywin32 (Outlook COM), Playwright (Chromium), openai SDK (`AzureOpenAI`, structured outputs via `.beta.chat.completions.parse`), openpyxl, PyYAML, pytest.

---

## Conventions

- Source lives under `src/formfiller/`. Tests mirror it under `tests/`.
- Run tests with `python -m pytest` from the project root.
- Every test file starts with `import pytest` and imports from `formfiller.*`.
- The package is installed editable (`pip install -e .`) so imports resolve — set up in Task 1.
- Commit after each task's tests pass. Use the message shown in the task's final step.
- The data profile and config are loaded once and passed down — no module reads global state.
- Pydantic models use `model_config = ConfigDict(frozen=True)` for the data-transfer objects so they are hashable and immutable.

---

## File Structure

```
Poc forms e-fact/
├── pyproject.toml                      # package metadata + deps (Task 1)
├── .env.example                        # Azure OpenAI key + endpoint placeholders (Task 1)
├── .gitignore                          # .env, __pycache__, etc. (Task 1)
├── config.yaml                         # runtime settings (Task 3)
├── profile.yaml                        # the company data profile (Task 3)
├── src/formfiller/
│   ├── __init__.py
│   ├── models.py                       # all Pydantic DTOs (Task 2)
│   ├── config.py                       # load config.yaml + profile.yaml (Task 3)
│   ├── link_extractor.py               # email body → form URL (Task 4)
│   ├── confidence.py                   # gate logic — the core decision (Task 5)
│   ├── result_logger.py                # append row to Excel (Task 6)
│   ├── review_queue.py                 # park held jobs to a folder (Task 7)
│   ├── email_source.py                 # EmailSource protocol + Outlook COM impl (Task 8)
│   ├── form_reader.py                  # Playwright → FormSchema (Task 9)
│   ├── form_filler.py                  # Playwright fills + submits (Task 10)
│   ├── field_mapper.py                 # Azure OpenAI maps schema → answers (Task 11)
│   ├── orchestrator.py                 # wires the pipeline (Task 12)
│   └── cli.py                          # selection UI + entry point (Task 13)
└── tests/
    ├── conftest.py                     # shared fixtures (Task 2)
    ├── fixtures/
    │   ├── ms_form.html                # captured MS Form markup (Task 9)
    │   └── google_form.html            # captured Google Form markup (Task 9)
    ├── test_models.py
    ├── test_config.py
    ├── test_link_extractor.py
    ├── test_confidence.py
    ├── test_result_logger.py
    ├── test_review_queue.py
    ├── test_email_source.py
    ├── test_form_reader.py
    ├── test_form_filler.py
    ├── test_field_mapper.py
    └── test_orchestrator.py
```

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/formfiller/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "formfiller"
version = "0.1.0"
description = "Email-to-form automation POC"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.6",
    "PyYAML>=6.0",
    "openai>=1.51",
    "openpyxl>=3.1",
    "playwright>=1.42",
    "pywin32>=306; sys_platform == 'win32'",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
formfiller = "formfiller.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.env
.venv/
venv/
*.egg-info/
.pytest_cache/
review_queue/
*.xlsx
!tests/**/*.xlsx
```

- [ ] **Step 3: Write `.env.example`**

```
AZURE_OPENAI_API_KEY=replace-me
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
```

- [ ] **Step 4: Create empty package markers**

Create `src/formfiller/__init__.py` containing exactly:

```python
"""Email-to-form automation POC."""
```

Create `tests/__init__.py` as an empty file.

- [ ] **Step 5: Install and verify**

Run: `pip install -e ".[dev]" && python -m playwright install chromium`
Expected: installs without error; `python -c "import formfiller"` prints nothing and exits 0.

- [ ] **Step 6: Commit**

```bash
git init
git add pyproject.toml .gitignore .env.example src/formfiller/__init__.py tests/__init__.py
git commit -m "chore: scaffold formfiller package"
```

---

## Task 2: Data models

**Files:**
- Create: `src/formfiller/models.py`
- Create: `tests/test_models.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
import pytest
from formfiller.models import (
    QuestionType,
    FormQuestion,
    FormSchema,
    MappedAnswer,
    MappingResult,
    EmailMessage,
)


def test_form_question_defaults():
    q = FormQuestion(id="q1", label="Company name", type=QuestionType.TEXT, required=True)
    assert q.options == ()
    assert q.required is True


def test_form_schema_holds_questions():
    q = FormQuestion(id="q1", label="VAT", type=QuestionType.TEXT, required=False)
    schema = FormSchema(url="https://forms.office.com/x", title="Vendor", questions=(q,))
    assert schema.questions[0].label == "VAT"


def test_mapped_answer_carries_confidence_and_status():
    a = MappedAnswer(
        question_id="q1",
        profile_field="vat_number",
        value="FR123",
        confidence=0.92,
        status="matched",
    )
    assert a.confidence == 0.92
    assert a.status == "matched"


def test_mapping_result_lookup_by_question_id():
    a = MappedAnswer(question_id="q1", profile_field=None, value=None, confidence=0.0, status="no_data")
    result = MappingResult(answers=(a,))
    assert result.by_id("q1") is a
    assert result.by_id("missing") is None


def test_email_message_is_frozen():
    msg = EmailMessage(
        entry_id="abc",
        sender="client@x.com",
        subject="Please fill",
        received="2026-06-10T09:00:00",
        body_text="link: https://forms.gle/x",
        body_html="<a href='https://forms.gle/x'>form</a>",
    )
    with pytest.raises(Exception):
        msg.entry_id = "changed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.models'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/models.py
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class QuestionType(str, Enum):
    TEXT = "text"
    EMAIL = "email"
    DATE = "date"
    NUMBER = "number"
    CHOICE_SINGLE = "choice_single"
    CHOICE_MULTI = "choice_multi"
    UNSUPPORTED = "unsupported"


_FROZEN = ConfigDict(frozen=True)


class FormQuestion(BaseModel):
    model_config = _FROZEN
    id: str
    label: str
    type: QuestionType
    required: bool
    options: tuple[str, ...] = ()


class FormSchema(BaseModel):
    model_config = _FROZEN
    url: str
    title: str
    questions: tuple[FormQuestion, ...]


MappingStatus = Literal["matched", "no_data", "ambiguous"]


class MappedAnswer(BaseModel):
    model_config = _FROZEN
    question_id: str
    profile_field: Optional[str]
    value: Optional[str]
    confidence: float
    status: MappingStatus


class MappingResult(BaseModel):
    model_config = _FROZEN
    answers: tuple[MappedAnswer, ...]

    def by_id(self, question_id: str) -> Optional[MappedAnswer]:
        for a in self.answers:
            if a.question_id == question_id:
                return a
        return None


class EmailMessage(BaseModel):
    model_config = _FROZEN
    entry_id: str
    sender: str
    subject: str
    received: str
    body_text: str
    body_html: str
```

- [ ] **Step 4: Write `tests/conftest.py` with shared fixtures**

```python
# tests/conftest.py
import pytest
from formfiller.models import QuestionType, FormQuestion, FormSchema, EmailMessage


@pytest.fixture
def sample_schema():
    return FormSchema(
        url="https://forms.office.com/r/sample",
        title="Vendor Onboarding",
        questions=(
            FormQuestion(id="q1", label="Company legal name", type=QuestionType.TEXT, required=True),
            FormQuestion(id="q2", label="VAT number", type=QuestionType.TEXT, required=True),
            FormQuestion(id="q3", label="Nickname (optional)", type=QuestionType.TEXT, required=False),
        ),
    )


@pytest.fixture
def sample_email():
    return EmailMessage(
        entry_id="ENTRY-1",
        sender="client@acme.com",
        subject="Please complete our vendor form",
        received="2026-06-10T09:00:00",
        body_text="Hello, please fill https://forms.office.com/r/sample thanks",
        body_html='<p>Hello, please fill <a href="https://forms.office.com/r/sample">here</a></p>',
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add src/formfiller/models.py tests/test_models.py tests/conftest.py
git commit -m "feat: add pydantic data models"
```

---

## Task 3: Config and profile loading

**Files:**
- Create: `src/formfiller/config.py`
- Create: `config.yaml`
- Create: `profile.yaml`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import textwrap
import pytest
from formfiller.config import load_config, load_profile, ProfileField


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_load_config_reads_threshold_and_paths(tmp_path):
    cfg_file = _write(tmp_path, "config.yaml", """
        confidence_threshold: 0.8
        dry_run: false
        excel_log_path: C:/synced/log.xlsx
        review_queue_dir: ./review_queue
        inbox_list_count: 25
        azure_openai_deployment: gpt-4o
        azure_api_version: "2024-10-21"
    """)
    cfg = load_config(cfg_file)
    assert cfg.confidence_threshold == 0.8
    assert cfg.dry_run is False
    assert cfg.inbox_list_count == 25
    assert cfg.azure_openai_deployment == "gpt-4o"
    assert cfg.azure_api_version == "2024-10-21"


def test_load_profile_parses_fields_with_aliases(tmp_path):
    prof_file = _write(tmp_path, "profile.yaml", """
        fields:
          - name: company_legal_name
            value: Ginesis Finance SAS
            aliases: ["company name", "raison sociale"]
          - name: vat_number
            value: FR12345678901
            aliases: ["VAT", "N° TVA", "tax id"]
    """)
    profile = load_profile(prof_file)
    assert len(profile) == 2
    vat = next(f for f in profile if f.name == "vat_number")
    assert vat.value == "FR12345678901"
    assert "VAT" in vat.aliases


def test_profile_field_is_frozen():
    f = ProfileField(name="x", value="y", aliases=())
    with pytest.raises(Exception):
        f.value = "z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.config'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/config.py
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    confidence_threshold: float = 0.8
    dry_run: bool = False
    excel_log_path: str
    review_queue_dir: str = "./review_queue"
    inbox_list_count: int = 25
    azure_openai_deployment: str = "gpt-4o"
    azure_api_version: str = "2024-10-21"


class ProfileField(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    value: str
    aliases: tuple[str, ...] = ()


def load_config(path: str | Path) -> AppConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppConfig(**data)


def load_profile(path: str | Path) -> tuple[ProfileField, ...]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return tuple(ProfileField(**f) for f in data["fields"])
```

- [ ] **Step 4: Write the real `config.yaml` and `profile.yaml`**

Create `config.yaml`:

```yaml
# Runtime settings for the form-filler POC.
confidence_threshold: 0.8       # submit only if every required field >= this
dry_run: true                   # POC default: fill but never click submit
excel_log_path: "C:/Users/PierreKANZANZA/OneDrive - Ginini antipode/Documents/GININI/IA/Poc forms e-fact/form_log.xlsx"
review_queue_dir: "./review_queue"
inbox_list_count: 25            # how many recent inbox emails to list
azure_openai_deployment: "gpt-4o"   # your Azure OpenAI deployment name (must support structured outputs)
azure_api_version: "2024-10-21"     # >= 2024-08-01-preview for structured outputs
```

Create `profile.yaml` (placeholder values — Pierre edits these before real use):

```yaml
# The single company data profile. Same data for every form.
# `aliases` help match differently-worded questions and reduce LLM cost.
fields:
  - name: company_legal_name
    value: "Ginesis Finance SAS"
    aliases: ["company name", "legal name", "raison sociale", "société"]
  - name: vat_number
    value: "FR0000000000"
    aliases: ["VAT", "VAT number", "N° TVA", "numéro de TVA", "tax id"]
  - name: siret
    value: "00000000000000"
    aliases: ["SIRET", "company registration number"]
  - name: contact_email
    value: "contact@ginesis-finance.com"
    aliases: ["email", "e-mail", "contact email", "adresse email"]
  - name: contact_phone
    value: "+33000000000"
    aliases: ["phone", "telephone", "téléphone", "phone number"]
  - name: address_street
    value: "1 Rue Exemple"
    aliases: ["street", "address", "adresse", "rue"]
  - name: address_city
    value: "Paris"
    aliases: ["city", "ville"]
  - name: address_postal_code
    value: "75001"
    aliases: ["postal code", "zip", "code postal"]
  - name: address_country
    value: "France"
    aliases: ["country", "pays"]
  - name: iban
    value: "FR0000000000000000000000000"
    aliases: ["IBAN", "bank account"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/formfiller/config.py config.yaml profile.yaml tests/test_config.py
git commit -m "feat: add config and profile loading"
```

---

## Task 4: Link extractor

**Files:**
- Create: `src/formfiller/link_extractor.py`
- Create: `tests/test_link_extractor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_link_extractor.py
import pytest
from formfiller.link_extractor import extract_form_url, NoFormLinkError


def test_picks_ms_forms_link_from_html():
    html = '<p>Hi <a href="https://acme.com/news">news</a> please fill ' \
           '<a href="https://forms.office.com/r/abc123">this form</a></p>'
    assert extract_form_url(html, "") == "https://forms.office.com/r/abc123"


def test_picks_google_forms_short_link_from_plain_text():
    text = "Complete it here: https://forms.gle/XyZ123 — thanks"
    assert extract_form_url("", text) == "https://forms.gle/XyZ123"


def test_picks_google_docs_forms_link():
    text = "Form: https://docs.google.com/forms/d/e/1FAIpQL/viewform"
    assert extract_form_url("", text) == "https://docs.google.com/forms/d/e/1FAIpQL/viewform"


def test_prefers_form_link_over_other_links():
    text = "See https://acme.com and fill https://forms.microsoft.com/r/zzz"
    assert extract_form_url("", text) == "https://forms.microsoft.com/r/zzz"


def test_raises_when_no_form_link_present():
    with pytest.raises(NoFormLinkError):
        extract_form_url("<a href='https://acme.com'>x</a>", "no forms here")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_link_extractor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.link_extractor'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/link_extractor.py
from __future__ import annotations

import re

# Domains that indicate an actual form, in priority order of confidence.
_FORM_HOST_PATTERNS = (
    r"forms\.office\.com",
    r"forms\.microsoft\.com",
    r"forms\.gle",
    r"docs\.google\.com/forms",
)

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)


class NoFormLinkError(Exception):
    """Raised when no recognizable form link is found in an email."""


def _all_urls(html: str, text: str) -> list[str]:
    # href="..." first (most reliable), then any bare URLs in either field.
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    bare = _URL_RE.findall(html) + _URL_RE.findall(text)
    seen: list[str] = []
    for url in [*hrefs, *bare]:
        if url not in seen:
            seen.append(url)
    return seen


def extract_form_url(body_html: str, body_text: str) -> str:
    """Return the first URL whose host matches a known form provider.

    Searches href attributes first, then bare URLs. Raises NoFormLinkError
    if none match.
    """
    urls = _all_urls(body_html, body_text)
    for pattern in _FORM_HOST_PATTERNS:
        for url in urls:
            if re.search(pattern, url, re.IGNORECASE):
                return url
    raise NoFormLinkError("No Microsoft/Google form link found in the email.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_link_extractor.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/link_extractor.py tests/test_link_extractor.py
git commit -m "feat: add form-link extractor"
```

---

## Task 5: Confidence gate (the core decision)

**Files:**
- Create: `src/formfiller/confidence.py`
- Create: `tests/test_confidence.py`

This module decides submit vs. review per the spec rules. TDD every rule.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_confidence.py
import pytest
from formfiller.models import QuestionType, FormQuestion, FormSchema, MappedAnswer, MappingResult
from formfiller.confidence import evaluate_gate


def _schema(*questions):
    return FormSchema(url="https://forms.office.com/r/x", title="t", questions=tuple(questions))


def _q(qid, required=True, qtype=QuestionType.TEXT):
    return FormQuestion(id=qid, label=qid, type=qtype, required=required)


def _ans(qid, value="v", confidence=0.95, status="matched", field="f"):
    return MappedAnswer(question_id=qid, profile_field=field, value=value, confidence=confidence, status=status)


def test_submits_when_all_required_matched_above_threshold():
    schema = _schema(_q("q1"), _q("q2"))
    result = MappingResult(answers=(_ans("q1"), _ans("q2")))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "submit"
    assert {f.question_id for f in decision.fields_to_fill} == {"q1", "q2"}
    assert decision.fields_blank_flagged == ()


def test_optional_no_data_is_left_blank_and_flagged_still_submits():
    schema = _schema(_q("q1"), _q("q2", required=False))
    result = MappingResult(answers=(
        _ans("q1"),
        MappedAnswer(question_id="q2", profile_field=None, value=None, confidence=0.0, status="no_data"),
    ))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "submit"
    assert decision.fields_blank_flagged == ("q2",)
    assert {f.question_id for f in decision.fields_to_fill} == {"q1"}


def test_required_no_data_routes_to_review():
    schema = _schema(_q("q1"), _q("q2"))
    result = MappingResult(answers=(
        _ans("q1"),
        MappedAnswer(question_id="q2", profile_field=None, value=None, confidence=0.0, status="no_data"),
    ))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert "required" in decision.reason.lower()


def test_low_confidence_field_routes_to_review():
    schema = _schema(_q("q1"))
    result = MappingResult(answers=(_ans("q1", confidence=0.5),))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert "confidence" in decision.reason.lower()


def test_ambiguous_field_routes_to_review():
    schema = _schema(_q("q1"))
    result = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="f", value="v", confidence=0.9, status="ambiguous"),
    ))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert "ambiguous" in decision.reason.lower()


def test_unsupported_question_type_routes_to_review():
    schema = _schema(_q("q1", qtype=QuestionType.UNSUPPORTED))
    result = MappingResult(answers=(_ans("q1"),))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert "type" in decision.reason.lower()


def test_required_question_with_no_answer_at_all_routes_to_review():
    schema = _schema(_q("q1"), _q("q2"))
    result = MappingResult(answers=(_ans("q1"),))  # nothing for q2
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert "missing" in decision.reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_confidence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.confidence'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/confidence.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from formfiller.models import FormSchema, MappingResult, QuestionType


class FillInstruction(BaseModel):
    model_config = ConfigDict(frozen=True)
    question_id: str
    value: str


class GateDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: Literal["submit", "review"]
    reason: str
    fields_to_fill: tuple[FillInstruction, ...]
    fields_blank_flagged: tuple[str, ...]


def evaluate_gate(
    schema: FormSchema, result: MappingResult, threshold: float
) -> GateDecision:
    """Decide whether to auto-submit the filled form or hold it for review.

    Rules (from the spec):
      * Unsupported question type anywhere -> review.
      * Required question with no answer / no matching data -> review.
      * Any answer flagged 'ambiguous' -> review.
      * Any matched answer below `threshold` -> review.
      * Optional question with no matching data -> leave blank, flag, keep going.
      * Otherwise -> submit, filling every matched answer.
    """
    fields_to_fill: list[FillInstruction] = []
    blank_flagged: list[str] = []

    for q in schema.questions:
        if q.type == QuestionType.UNSUPPORTED:
            return _review(f"Unsupported question type for '{q.label}'.")

        answer = result.by_id(q.id)

        if answer is None:
            if q.required:
                return _review(f"Required question '{q.label}' is missing an answer.")
            blank_flagged.append(q.id)
            continue

        if answer.status == "ambiguous":
            return _review(f"Mapping for '{q.label}' is ambiguous.")

        if answer.status == "no_data" or answer.value is None:
            if q.required:
                return _review(f"Required question '{q.label}' has no matching profile data.")
            blank_flagged.append(q.id)
            continue

        # status == "matched" with a value
        if answer.confidence < threshold:
            return _review(
                f"Low confidence ({answer.confidence:.2f}) mapping '{q.label}'."
            )
        fields_to_fill.append(FillInstruction(question_id=q.id, value=answer.value))

    return GateDecision(
        action="submit",
        reason="All required fields filled with sufficient confidence.",
        fields_to_fill=tuple(fields_to_fill),
        fields_blank_flagged=tuple(blank_flagged),
    )


def _review(reason: str) -> GateDecision:
    return GateDecision(
        action="review", reason=reason, fields_to_fill=(), fields_blank_flagged=()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_confidence.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/confidence.py tests/test_confidence.py
git commit -m "feat: add confidence gate decision logic"
```

---

## Task 6: Result logger

**Files:**
- Create: `src/formfiller/result_logger.py`
- Create: `tests/test_result_logger.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_result_logger.py
import pytest
from openpyxl import load_workbook
from formfiller.result_logger import JobResult, append_result, COLUMNS


def _result(status="success"):
    return JobResult(
        timestamp="2026-06-10T09:30:00",
        sender="client@acme.com",
        client_name="Acme",
        form_url="https://forms.office.com/r/x",
        form_type="ms_forms",
        status=status,
        overall_confidence=0.91,
        fields_filled=5,
        fields_blank_flagged="nickname",
        review_reason="",
        screenshot_path="",
    )


def test_creates_workbook_with_header_when_missing(tmp_path):
    path = tmp_path / "log.xlsx"
    append_result(path, _result())
    wb = load_workbook(path)
    ws = wb.active
    assert [c.value for c in ws[1]] == list(COLUMNS)
    assert ws[2][0].value == "2026-06-10T09:30:00"
    assert ws[2][5].value == "success"


def test_appends_second_row_without_duplicating_header(tmp_path):
    path = tmp_path / "log.xlsx"
    append_result(path, _result(status="success"))
    append_result(path, _result(status="manual"))
    wb = load_workbook(path)
    ws = wb.active
    assert ws.max_row == 3  # header + 2 rows
    assert ws[3][5].value == "manual"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_result_logger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.result_logger'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/result_logger.py
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from pydantic import BaseModel, ConfigDict

COLUMNS = (
    "timestamp",
    "sender",
    "client_name",
    "form_url",
    "form_type",
    "status",
    "overall_confidence",
    "fields_filled",
    "fields_blank_flagged",
    "review_reason",
    "screenshot_path",
)


class JobResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    timestamp: str
    sender: str
    client_name: str
    form_url: str
    form_type: str
    status: str  # "success" | "manual" | "fail"
    overall_confidence: float
    fields_filled: int
    fields_blank_flagged: str
    review_reason: str
    screenshot_path: str


def append_result(path: str | Path, result: JobResult) -> None:
    """Append one outcome row to the Excel log, creating it with a header row
    if it does not yet exist. Writes to the local (synced) copy of the file."""
    path = Path(path)
    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = "log"
        ws.append(list(COLUMNS))

    ws.append([getattr(result, col) for col in COLUMNS])
    wb.save(path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_result_logger.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/result_logger.py tests/test_result_logger.py
git commit -m "feat: add excel result logger"
```

---

## Task 7: Review queue

**Files:**
- Create: `src/formfiller/review_queue.py`
- Create: `tests/test_review_queue.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_queue.py
import json
import pytest
from formfiller.models import QuestionType, FormQuestion, FormSchema, MappedAnswer, MappingResult
from formfiller.review_queue import park_for_review


def _schema():
    return FormSchema(
        url="https://forms.office.com/r/x",
        title="Vendor",
        questions=(FormQuestion(id="q1", label="VAT", type=QuestionType.TEXT, required=True),),
    )


def _result():
    return MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="vat_number", value="FR1", confidence=0.5, status="matched"),
    ))


def test_park_writes_payload_json_and_returns_dir(tmp_path):
    out_dir = park_for_review(
        queue_dir=tmp_path,
        job_id="job-123",
        schema=_schema(),
        result=_result(),
        reason="Low confidence",
        screenshot_bytes=b"\x89PNG fake",
    )
    payload = json.loads((out_dir / "payload.json").read_text(encoding="utf-8"))
    assert payload["reason"] == "Low confidence"
    assert payload["form_url"] == "https://forms.office.com/r/x"
    assert payload["answers"][0]["question_id"] == "q1"
    assert (out_dir / "screenshot.png").read_bytes() == b"\x89PNG fake"


def test_park_without_screenshot_skips_image(tmp_path):
    out_dir = park_for_review(
        queue_dir=tmp_path,
        job_id="job-456",
        schema=_schema(),
        result=_result(),
        reason="Required field missing",
        screenshot_bytes=None,
    )
    assert (out_dir / "payload.json").exists()
    assert not (out_dir / "screenshot.png").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_review_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.review_queue'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/review_queue.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from formfiller.models import FormSchema, MappingResult


def park_for_review(
    queue_dir: str | Path,
    job_id: str,
    schema: FormSchema,
    result: MappingResult,
    reason: str,
    screenshot_bytes: Optional[bytes],
) -> Path:
    """Persist a held job to `<queue_dir>/<job_id>/` for a human to finish.

    Writes payload.json (form URL, schema, proposed answers, hold reason) and,
    when provided, screenshot.png of the filled-but-unsubmitted form.
    Returns the per-job directory.
    """
    out_dir = Path(queue_dir) / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "job_id": job_id,
        "reason": reason,
        "form_url": schema.url,
        "form_title": schema.title,
        "questions": [q.model_dump() for q in schema.questions],
        "answers": [a.model_dump() for a in result.answers],
    }
    (out_dir / "payload.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if screenshot_bytes is not None:
        (out_dir / "screenshot.png").write_bytes(screenshot_bytes)

    return out_dir
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_review_queue.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/review_queue.py tests/test_review_queue.py
git commit -m "feat: add review queue"
```

---

## Task 8: Email source (Outlook COM behind an interface)

**Files:**
- Create: `src/formfiller/email_source.py`
- Create: `tests/test_email_source.py`

The Outlook COM call cannot run in CI, so the logic lives behind a `EmailSource` Protocol. We test a `FakeEmailSource` and the pure conversion helper; the real `OutlookEmailSource` is thin.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_email_source.py
import pytest
from formfiller.models import EmailMessage
from formfiller.email_source import FakeEmailSource, EmailSource


def _msg(entry_id, subject):
    return EmailMessage(
        entry_id=entry_id, sender="a@b.com", subject=subject,
        received="2026-06-10T09:00:00", body_text="t", body_html="<p>t</p>",
    )


def test_fake_source_lists_and_fetches_by_entry_id():
    source: EmailSource = FakeEmailSource([_msg("E1", "First"), _msg("E2", "Second")])
    listed = source.list_recent(10)
    assert [m.subject for m in listed] == ["First", "Second"]
    assert source.get("E2").subject == "Second"


def test_fake_source_list_respects_count():
    source = FakeEmailSource([_msg(f"E{i}", str(i)) for i in range(5)])
    assert len(source.list_recent(3)) == 3


def test_fake_source_get_missing_returns_none():
    source = FakeEmailSource([_msg("E1", "x")])
    assert source.get("nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_email_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.email_source'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/email_source.py
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from formfiller.models import EmailMessage


@runtime_checkable
class EmailSource(Protocol):
    """A source of inbox emails the user can pick from."""

    def list_recent(self, count: int) -> list[EmailMessage]:
        ...

    def get(self, entry_id: str) -> Optional[EmailMessage]:
        ...


class FakeEmailSource:
    """In-memory source for tests."""

    def __init__(self, messages: list[EmailMessage]):
        self._messages = list(messages)

    def list_recent(self, count: int) -> list[EmailMessage]:
        return self._messages[:count]

    def get(self, entry_id: str) -> Optional[EmailMessage]:
        for m in self._messages:
            if m.entry_id == entry_id:
                return m
        return None


class OutlookEmailSource:
    """Reads the live Outlook inbox via the desktop COM interface.

    Requires Outlook installed and a logged-in profile (the POC runs on
    Pierre's own machine). Imports pywin32 lazily so the rest of the package
    imports cleanly on non-Windows CI.
    """

    def __init__(self) -> None:
        import win32com.client  # lazy import; Windows + Outlook only

        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        # 6 == olFolderInbox
        self._inbox = outlook.GetDefaultFolder(6)

    def list_recent(self, count: int) -> list[EmailMessage]:
        items = self._inbox.Items
        items.Sort("[ReceivedTime]", True)  # newest first
        out: list[EmailMessage] = []
        for item in items:
            if getattr(item, "Class", None) != 43:  # 43 == olMail
                continue
            out.append(self._to_message(item))
            if len(out) >= count:
                break
        return out

    def get(self, entry_id: str) -> Optional[EmailMessage]:
        try:
            item = self._inbox.Session.GetItemFromID(entry_id)
        except Exception:
            return None
        return self._to_message(item)

    @staticmethod
    def _to_message(item) -> EmailMessage:
        received = getattr(item, "ReceivedTime", None)
        return EmailMessage(
            entry_id=str(item.EntryID),
            sender=str(getattr(item, "SenderEmailAddress", "") or ""),
            subject=str(getattr(item, "Subject", "") or ""),
            received=received.isoformat() if received is not None else "",
            body_text=str(getattr(item, "Body", "") or ""),
            body_html=str(getattr(item, "HTMLBody", "") or ""),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_email_source.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/email_source.py tests/test_email_source.py
git commit -m "feat: add email source interface with Outlook COM impl"
```

---

## Task 9: Form reader (Playwright → FormSchema)

**Files:**
- Create: `src/formfiller/form_reader.py`
- Create: `tests/test_form_reader.py`
- Create: `tests/fixtures/ms_form.html`
- Create: `tests/fixtures/google_form.html`

The reader extracts a `FormSchema` from a loaded Playwright `Page`. We test it against local HTML fixtures opened via `file://`, so no network is needed. The parsing targets generic form markup (`<label>` + input/select/textarea, `aria-required`), which both fixtures use.

- [ ] **Step 1: Create the HTML fixtures**

Create `tests/fixtures/ms_form.html`:

```html
<!doctype html>
<html><head><title>Vendor Onboarding</title></head>
<body>
  <form data-automation="questions">
    <div class="question">
      <label for="i1">Company legal name</label>
      <input id="i1" type="text" aria-required="true" />
    </div>
    <div class="question">
      <label for="i2">Contact email</label>
      <input id="i2" type="email" aria-required="true" />
    </div>
    <div class="question">
      <label for="i3">Preferred contact method</label>
      <select id="i3" aria-required="false">
        <option>Email</option>
        <option>Phone</option>
      </select>
    </div>
    <div class="question">
      <label for="i4">Comments</label>
      <textarea id="i4" aria-required="false"></textarea>
    </div>
  </form>
</body></html>
```

Create `tests/fixtures/google_form.html`:

```html
<!doctype html>
<html><head><title>Supplier Form</title></head>
<body>
  <form>
    <div class="question">
      <label for="g1">VAT number</label>
      <input id="g1" type="text" aria-required="true" />
    </div>
    <div class="question">
      <label for="g2">Country</label>
      <input id="g2" type="text" aria-required="false" />
    </div>
  </form>
</body></html>
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_form_reader.py
from pathlib import Path
import pytest
from formfiller.models import QuestionType
from formfiller.form_reader import read_form

FIXTURES = Path(__file__).parent / "fixtures"


def _file_url(name: str) -> str:
    return (FIXTURES / name).resolve().as_uri()


@pytest.mark.parametrize("name,title,first_label,n", [
    ("ms_form.html", "Vendor Onboarding", "Company legal name", 4),
    ("google_form.html", "Supplier Form", "VAT number", 2),
])
def test_read_form_extracts_questions(name, title, first_label, n):
    schema = read_form(_file_url(name))
    assert schema.title == title
    assert len(schema.questions) == n
    assert schema.questions[0].label == first_label


def test_read_form_detects_types_and_required():
    schema = read_form(_file_url("ms_form.html"))
    by_label = {q.label: q for q in schema.questions}
    assert by_label["Company legal name"].type == QuestionType.TEXT
    assert by_label["Company legal name"].required is True
    assert by_label["Contact email"].type == QuestionType.EMAIL
    assert by_label["Preferred contact method"].type == QuestionType.CHOICE_SINGLE
    assert by_label["Preferred contact method"].options == ("Email", "Phone")
    assert by_label["Comments"].required is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_form_reader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.form_reader'`

- [ ] **Step 4: Write the implementation**

```python
# src/formfiller/form_reader.py
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_form_reader.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/formfiller/form_reader.py tests/test_form_reader.py tests/fixtures/ms_form.html tests/fixtures/google_form.html
git commit -m "feat: add Playwright form reader"
```

---

## Task 10: Form filler (Playwright fill + submit)

**Files:**
- Create: `src/formfiller/form_filler.py`
- Create: `tests/test_form_filler.py`

Tested against the same local HTML fixtures: fill, then read the values back from the DOM. Submission is exercised in dry-run mode (the filler must NOT click submit when `dry_run=True`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_form_filler.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_form_filler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.form_filler'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/form_filler.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_form_filler.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/form_filler.py tests/test_form_filler.py
git commit -m "feat: add Playwright form filler"
```

---

## Task 11: Field mapper (Azure OpenAI structured output)

**Files:**
- Create: `src/formfiller/field_mapper.py`
- Create: `tests/test_field_mapper.py`

`map_fields` builds a prompt from the schema + profile and calls
`client.beta.chat.completions.parse(...)` (the Azure OpenAI structured-output helper)
with a Pydantic `response_format`. The client is injected, so tests pass a stub that
mimics the OpenAI response shape (`completion.choices[0].message.parsed`) — no network,
no API key.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_field_mapper.py
import pytest
from formfiller.config import ProfileField
from formfiller.models import QuestionType, FormQuestion, FormSchema
from formfiller.field_mapper import map_fields, LLMMapping, LLMMappedAnswer


# Stubs mimic the openai SDK shape: client.beta.chat.completions.parse(...)
# returns a completion whose choices[0].message.parsed is the Pydantic object.
class _StubMessage:
    def __init__(self, parsed):
        self.parsed = parsed
        self.refusal = None


class _StubChoice:
    def __init__(self, parsed):
        self.message = _StubMessage(parsed)


class _StubCompletion:
    def __init__(self, parsed):
        self.choices = [_StubChoice(parsed)]


class _StubCompletions:
    def __init__(self, parsed):
        self._parsed = parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _StubCompletion(self._parsed)


class _StubChat:
    def __init__(self, parsed):
        self.completions = _StubCompletions(parsed)


class _StubBeta:
    def __init__(self, parsed):
        self.chat = _StubChat(parsed)


class _StubClient:
    def __init__(self, parsed):
        self.beta = _StubBeta(parsed)


def _schema():
    return FormSchema(
        url="https://forms.office.com/r/x", title="Vendor",
        questions=(
            FormQuestion(id="q1", label="Company name", type=QuestionType.TEXT, required=True),
            FormQuestion(id="q2", label="Random one-off", type=QuestionType.TEXT, required=False),
        ),
    )


def _profile():
    return (
        ProfileField(name="company_legal_name", value="Ginesis Finance SAS", aliases=("company name",)),
    )


def test_map_fields_returns_mapping_result_from_llm_output():
    parsed = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.95, status="matched"),
        LLMMappedAnswer(question_id="q2", profile_field=None, value=None,
                        confidence=0.0, status="no_data"),
    ])
    client = _StubClient(parsed)
    result = map_fields(client, "gpt-4o", _schema(), _profile())
    assert result.by_id("q1").value == "Ginesis Finance SAS"
    assert result.by_id("q1").confidence == 0.95
    assert result.by_id("q2").status == "no_data"


def test_map_fields_passes_deployment_and_response_format():
    parsed = LLMMapping(answers=[])
    client = _StubClient(parsed)
    map_fields(client, "gpt-4o", _schema(), _profile())
    call = client.beta.chat.completions.calls[0]
    assert call["model"] == "gpt-4o"
    assert call["response_format"] is LLMMapping
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_field_mapper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.field_mapper'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/field_mapper.py
from __future__ import annotations

import json
from typing import Literal, Optional, Sequence

from pydantic import BaseModel

from formfiller.config import ProfileField
from formfiller.models import FormSchema, MappedAnswer, MappingResult


class LLMMappedAnswer(BaseModel):
    question_id: str
    profile_field: Optional[str]
    value: Optional[str]
    confidence: float
    status: Literal["matched", "no_data", "ambiguous"]


class LLMMapping(BaseModel):
    answers: list[LLMMappedAnswer]


_SYSTEM = (
    "You map web-form questions to a fixed company data profile. "
    "For each question, choose the single best-matching profile field and return "
    "its value, a confidence in [0,1], and a status. Use status 'matched' when a "
    "profile field clearly answers the question, 'no_data' when the profile has "
    "nothing relevant, and 'ambiguous' when two or more fields could plausibly "
    "apply or the question is unclear. Respond directly with the structured data; "
    "do not add commentary."
)


def _build_user_prompt(schema: FormSchema, profile: Sequence[ProfileField]) -> str:
    profile_lines = [
        {"field": f.name, "value": f.value, "aliases": list(f.aliases)}
        for f in profile
    ]
    question_lines = [
        {
            "question_id": q.id,
            "label": q.label,
            "type": q.type.value,
            "required": q.required,
            "options": list(q.options),
        }
        for q in schema.questions
    ]
    return (
        "PROFILE (the only data you may use as values):\n"
        + json.dumps(profile_lines, ensure_ascii=False, indent=2)
        + "\n\nFORM QUESTIONS:\n"
        + json.dumps(question_lines, ensure_ascii=False, indent=2)
        + "\n\nReturn one answer object per question_id above."
    )


def map_fields(
    client,
    deployment: str,
    schema: FormSchema,
    profile: Sequence[ProfileField],
) -> MappingResult:
    """Ask the LLM to map each form question to a profile field.

    `client` is an `openai.AzureOpenAI`-compatible object exposing
    `beta.chat.completions.parse(...)`. `deployment` is the Azure OpenAI
    deployment name (passed as `model`). Returns a MappingResult of validated
    answers. Raises RuntimeError if the model refuses or returns no parse.
    """
    completion = client.beta.chat.completions.parse(
        model=deployment,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user_prompt(schema, profile)},
        ],
        response_format=LLMMapping,
    )
    message = completion.choices[0].message
    if getattr(message, "refusal", None):
        raise RuntimeError(f"LLM refused to map fields: {message.refusal}")
    parsed: LLMMapping = message.parsed
    if parsed is None:
        raise RuntimeError("LLM returned no structured output.")
    answers = tuple(
        MappedAnswer(
            question_id=a.question_id,
            profile_field=a.profile_field,
            value=a.value,
            confidence=a.confidence,
            status=a.status,
        )
        for a in parsed.answers
    )
    return MappingResult(answers=answers)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_field_mapper.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/field_mapper.py tests/test_field_mapper.py
git commit -m "feat: add Azure OpenAI field mapper with structured output"
```

---

## Task 12: Orchestrator

**Files:**
- Create: `src/formfiller/orchestrator.py`
- Create: `tests/test_orchestrator.py`

The orchestrator wires the pipeline for one email. It takes injected callables for the browser-dependent steps (read, fill, submit, screenshot) and the mapper, so it can be tested end-to-end with fakes — no Playwright, no Outlook, no LLM.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator.py
import pytest
from formfiller.config import AppConfig, ProfileField
from formfiller.models import (
    QuestionType, FormQuestion, FormSchema, EmailMessage, MappedAnswer, MappingResult,
)
from formfiller.orchestrator import process_email, PipelineHooks


def _email(body):
    return EmailMessage(
        entry_id="E1", sender="client@acme.com", subject="Fill this",
        received="2026-06-10T09:00:00", body_text=body, body_html="",
    )


def _config(tmp_path, dry_run=False):
    return AppConfig(
        confidence_threshold=0.8,
        dry_run=dry_run,
        excel_log_path=str(tmp_path / "log.xlsx"),
        review_queue_dir=str(tmp_path / "queue"),
        inbox_list_count=10,
        azure_openai_deployment="gpt-4o",
        azure_api_version="2024-10-21",
    )


_PROFILE = (ProfileField(name="company_legal_name", value="Ginesis Finance SAS", aliases=()),)

_SCHEMA = FormSchema(
    url="https://forms.office.com/r/x", title="Vendor",
    questions=(FormQuestion(id="q1", label="Company name", type=QuestionType.TEXT, required=True),),
)


def test_high_confidence_logs_success(tmp_path):
    mapping = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="company_legal_name",
                     value="Ginesis Finance SAS", confidence=0.95, status="matched"),
    ))
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: mapping,
        fill_and_submit=lambda url, instr, dry_run: (b"\x89PNG", (not dry_run) and len(instr) > 0),
    )
    result = process_email(_email("link https://forms.office.com/r/x"),
                           _config(tmp_path), _PROFILE, hooks)
    assert result.status == "success"
    assert result.fields_filled == 1


def test_low_confidence_parks_for_review_and_logs_manual(tmp_path):
    mapping = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="company_legal_name",
                     value="Ginesis Finance SAS", confidence=0.4, status="matched"),
    ))
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: mapping,
        fill_and_submit=lambda url, instr, dry_run: (b"\x89PNG", False),
    )
    cfg = _config(tmp_path)
    result = process_email(_email("link https://forms.office.com/r/x"), cfg, _PROFILE, hooks)
    assert result.status == "manual"
    assert "confidence" in result.review_reason.lower()
    # a review-queue folder was created
    assert (tmp_path / "queue" / result_job_dir(result)).exists()


def result_job_dir(result):
    # the orchestrator uses the email entry_id as the job id
    return "E1"


def test_no_form_link_logs_fail(tmp_path):
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: MappingResult(answers=()),
        fill_and_submit=lambda url, instr, dry_run: (b"", False),
    )
    result = process_email(_email("no link here"), _config(tmp_path), _PROFILE, hooks)
    assert result.status == "fail"
    assert "link" in result.review_reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.orchestrator'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/orchestrator.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from formfiller.config import AppConfig, ProfileField
from formfiller.confidence import FillInstruction, evaluate_gate
from formfiller.link_extractor import NoFormLinkError, extract_form_url
from formfiller.models import EmailMessage, FormSchema, MappingResult
from formfiller.result_logger import JobResult, append_result
from formfiller.review_queue import park_for_review


@dataclass
class PipelineHooks:
    """Injected steps that touch external systems, so the orchestrator stays
    testable. In production these wrap Playwright and Azure OpenAI (see cli.py)."""
    read_form: Callable[[str], FormSchema]
    map_fields: Callable[[FormSchema], MappingResult]
    # returns (screenshot_bytes, submitted?)
    fill_and_submit: Callable[[str, tuple[FillInstruction, ...], bool], tuple[bytes, bool]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _overall_confidence(result: MappingResult) -> float:
    confs = [a.confidence for a in result.answers if a.status == "matched"]
    return round(min(confs), 3) if confs else 0.0


def process_email(
    email: EmailMessage,
    config: AppConfig,
    profile: tuple[ProfileField, ...],
    hooks: PipelineHooks,
) -> JobResult:
    """Run the full pipeline for one chosen email and return the logged result.

    Always appends exactly one row to the Excel log. Never raises for expected
    failures (no link, browser error) — those become status='fail' rows.
    """
    base = dict(
        timestamp=_now_iso(),
        sender=email.sender,
        client_name=email.sender.split("@")[-1].split(".")[0] if "@" in email.sender else email.sender,
        form_url="",
        form_type="",
        overall_confidence=0.0,
        fields_filled=0,
        fields_blank_flagged="",
        review_reason="",
        screenshot_path="",
    )

    def _finish(**overrides) -> JobResult:
        result = JobResult(**{**base, **overrides})
        append_result(config.excel_log_path, result)
        return result

    # 1. Find the form link.
    try:
        url = extract_form_url(email.body_html, email.body_text)
    except NoFormLinkError as exc:
        return _finish(status="fail", review_reason=f"No form link: {exc}")

    base["form_url"] = url
    base["form_type"] = _form_type(url)

    # 2. Read + 3. Map (wrapped so any browser/LLM error becomes a fail row).
    try:
        schema = hooks.read_form(url)
        mapping = hooks.map_fields(schema)
    except Exception as exc:  # noqa: BLE001 — isolate one bad form
        return _finish(status="fail", review_reason=f"Read/map error: {exc}")

    base["overall_confidence"] = _overall_confidence(mapping)

    # 4. Gate.
    decision = evaluate_gate(schema, mapping, config.confidence_threshold)

    if decision.action == "review":
        try:
            _, _ = hooks.fill_and_submit(url, (), True)  # fill only, never submit
            screenshot, _ = hooks.fill_and_submit(url, decision.fields_to_fill, True)
        except Exception:  # noqa: BLE001
            screenshot = None
        park_for_review(
            queue_dir=config.review_queue_dir,
            job_id=email.entry_id,
            schema=schema,
            result=mapping,
            reason=decision.reason,
            screenshot_bytes=screenshot,
        )
        return _finish(
            status="manual",
            review_reason=decision.reason,
            screenshot_path=str(_job_screenshot_path(config, email.entry_id)),
        )

    # 5. Submit (respecting dry_run).
    try:
        _screenshot, submitted = hooks.fill_and_submit(
            url, decision.fields_to_fill, config.dry_run
        )
    except Exception as exc:  # noqa: BLE001
        return _finish(status="fail", review_reason=f"Fill/submit error: {exc}")

    status = "success" if (submitted or config.dry_run) else "fail"
    reason = "dry-run: filled but not submitted" if config.dry_run else ""
    return _finish(
        status=status,
        fields_filled=len(decision.fields_to_fill),
        fields_blank_flagged=",".join(decision.fields_blank_flagged),
        review_reason=reason,
    )


def _form_type(url: str) -> str:
    if "google" in url or "forms.gle" in url:
        return "google_forms"
    if "office.com" in url or "microsoft.com" in url:
        return "ms_forms"
    return "other"


def _job_screenshot_path(config: AppConfig, job_id: str):
    from pathlib import Path
    return Path(config.review_queue_dir) / job_id / "screenshot.png"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add pipeline orchestrator"
```

---

## Task 13: CLI (selection UI + wiring)

**Files:**
- Create: `src/formfiller/cli.py`
- Create: `tests/test_cli.py`

The CLI lists recent emails, takes a numeric pick, builds the real `PipelineHooks` (Playwright + Azure OpenAI), and prints the outcome. We test the pure pieces — selection parsing and hook construction wiring — and keep `main()` thin.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import pytest
from formfiller.models import EmailMessage
from formfiller.cli import format_inbox_line, parse_selection


def _msg(i):
    return EmailMessage(
        entry_id=f"E{i}", sender=f"s{i}@acme.com", subject=f"Subject {i}",
        received="2026-06-10T09:00:00", body_text="", body_html="",
    )


def test_format_inbox_line_includes_index_sender_subject():
    line = format_inbox_line(0, _msg(0))
    assert line.startswith("[0]")
    assert "s0@acme.com" in line
    assert "Subject 0" in line


def test_parse_selection_valid_index():
    msgs = [_msg(0), _msg(1), _msg(2)]
    assert parse_selection("1", msgs).entry_id == "E1"


def test_parse_selection_out_of_range_returns_none():
    msgs = [_msg(0)]
    assert parse_selection("5", msgs) is None


def test_parse_selection_non_numeric_returns_none():
    msgs = [_msg(0)]
    assert parse_selection("abc", msgs) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'formfiller.cli'`

- [ ] **Step 3: Write the implementation**

```python
# src/formfiller/cli.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from formfiller.config import AppConfig, ProfileField, load_config, load_profile
from formfiller.models import EmailMessage
from formfiller.orchestrator import PipelineHooks, process_email


def format_inbox_line(index: int, msg: EmailMessage) -> str:
    return f"[{index}] {msg.received}  {msg.sender:30.30}  {msg.subject}"


def parse_selection(raw: str, messages: list[EmailMessage]) -> Optional[EmailMessage]:
    raw = raw.strip()
    if not raw.isdigit():
        return None
    idx = int(raw)
    if 0 <= idx < len(messages):
        return messages[idx]
    return None


def _build_hooks(config: AppConfig, profile: tuple[ProfileField, ...]) -> PipelineHooks:
    """Construct the production hooks: Playwright reader/filler + Azure OpenAI mapper."""
    import os

    from openai import AzureOpenAI

    from formfiller.field_mapper import map_fields
    from formfiller.form_filler import fill_form, submit_form, take_screenshot
    from formfiller.form_reader import open_page, schema_from_page

    client = AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=config.azure_api_version,
    )

    def read_form(url: str):
        with open_page(headless=True) as page:
            page.goto(url, wait_until="load")
            return schema_from_page(page, url)

    def do_map(schema):
        return map_fields(client, config.azure_openai_deployment, schema, profile)

    def fill_and_submit(url, instructions, dry_run):
        with open_page(headless=True) as page:
            page.goto(url, wait_until="load")
            fill_form(page, instructions)
            screenshot = take_screenshot(page)
            submitted = submit_form(page, dry_run=dry_run)
            return screenshot, submitted

    return PipelineHooks(read_form=read_form, map_fields=do_map, fill_and_submit=fill_and_submit)


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()
    root = Path.cwd()
    config = load_config(root / "config.yaml")
    profile = load_profile(root / "profile.yaml")

    from formfiller.email_source import OutlookEmailSource

    source = OutlookEmailSource()
    messages = source.list_recent(config.inbox_list_count)
    if not messages:
        print("Inbox is empty.")
        return 0

    print("Recent emails:")
    for i, msg in enumerate(messages):
        print(format_inbox_line(i, msg))

    chosen = parse_selection(input("\nPick an email number: "), messages)
    if chosen is None:
        print("Invalid selection.")
        return 1

    print(f"\nProcessing: {chosen.subject}")
    if config.dry_run:
        print("(dry-run mode: forms will be filled but NOT submitted)")

    hooks = _build_hooks(config, profile)
    result = process_email(chosen, config, profile, hooks)

    print(f"\nResult: {result.status.upper()}")
    if result.review_reason:
        print(f"Reason: {result.review_reason}")
    print(f"Logged to: {config.excel_log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS (all tests green, ~33 passed)

- [ ] **Step 6: Commit**

```bash
git add src/formfiller/cli.py tests/test_cli.py
git commit -m "feat: add CLI entry point and pipeline wiring"
```

---

## Task 14: Manual smoke test & README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# Form-filler POC

Manually-launched tool that picks an email from your Outlook inbox, finds the
form link, fills a Microsoft/Google form from `profile.yaml`, and logs the
outcome to an Excel file.

## Setup
1. `pip install -e ".[dev]"`
2. `python -m playwright install chromium`
3. Copy `.env.example` to `.env` and set `AZURE_OPENAI_API_KEY` and
   `AZURE_OPENAI_ENDPOINT`.
4. Edit `profile.yaml` with your real company data.
5. Check `config.yaml` — set `azure_openai_deployment` to your deployment name
   (must support structured outputs, e.g. a gpt-4o deployment). `dry_run: true`
   fills but never submits; set to `false` only when ready to submit for real.

## Run
`python -m formfiller.cli` (or just `formfiller`)

Pick the email number when prompted. The outcome is appended to the Excel file
named in `config.yaml`; held forms land in `review_queue/<entry_id>/`.

## Test
`python -m pytest`
```

- [ ] **Step 2: Manual smoke test (dry-run, requires Outlook + a test form email)**

With `dry_run: true` in `config.yaml`, send yourself an email containing a link
to a test MS/Google form you control, then run `python -m formfiller.cli`.
Pick the email. Expected:
- The browser opens the form and fills the matched fields.
- No submission happens (dry-run).
- One row appears in the Excel log with status `success` and reason
  "dry-run: filled but not submitted".

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add README and setup instructions"
```

---

## Self-Review notes (resolved)

- **Spec §4 modules → tasks:** `email_source`→T8, `cli`→T13, `link_extractor`→T4,
  `form_reader`→T9, `field_mapper`→T11, `form_filler`→T10, `result_logger`→T6,
  `review_queue`→T7, `orchestrator`→T12. All covered.
- **Spec §5 confidence gate** (optional-blank, required-review, low-confidence,
  ambiguous, unsupported type) → T5, one test per rule.
- **Spec §6 reliability:** dry-run flag threaded through filler/orchestrator (T10/T12);
  per-email try/except isolation produces `fail` rows instead of crashing (T12);
  idempotency note — the POC's manual selection makes double-submit unlikely;
  the spec's "warn if already processed" check is **deferred** (not in scope for
  the manual POC; the orchestrator uses `entry_id` as the job id so a future
  processed-ledger can key on it).
- **Spec §7 Excel columns** → exact `COLUMNS` tuple in T6.
- **Type consistency:** `FillInstruction` defined in `confidence.py` (T5), used by
  `form_filler` (T10) and `orchestrator` (T12). `MappingResult.by_id` used by gate
  (T5) and orchestrator (T12). `JobResult` defined in `result_logger` (T6), returned
  by orchestrator (T12).
- **Retry/backoff** (spec §6) for transient page-load failures is left to the
  openai SDK's built-in retries (mapper) and is **not** added to Playwright calls
  in the POC — a failed read becomes a `fail` row the user can re-run manually.

## Post-implementation fix (final code review)

After all tasks, a final review found that the review-path code as written in Tasks 5
and 12 discarded the proposed fills (the `_review()` helper hardcoded
`fields_to_fill=()`), so the parked screenshot showed a *blank* form, contradicting
spec §5 ("a screenshot of the filled-but-unsubmitted form"). Fixed in commit
`3b767a6`:
- `confidence.evaluate_gate` now iterates all questions and carries the fillable
  instructions + blank-flagged fields on **both** the submit and review paths
  (low-confidence values included, so reviewers see the proposed entries). The
  `_review()` helper was removed.
- `orchestrator` review path now makes a **single** `fill_and_submit(url,
  decision.fields_to_fill, dry_run=True)` call (the earlier dead double-call is gone)
  and records `fields_blank_flagged` on the `manual` row.
- Added `test_review_decision_still_carries_fillable_fields_for_screenshot`.
The committed code is authoritative where it differs from the Task 5 / Task 12 code
blocks above.
```
