# Semantic Two-Pass Field Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make form-field mapping resilient to varied wording by reasoning from per-field descriptions, calibrating confidence with a propose→verify two-pass LLM flow, and emitting a human-readable per-form reasoning log.

**Architecture:** A new `map_and_verify` runs two `gpt-5.4` calls per form — a propose pass that maps each question to a profile field with a rationale, then a verify pass that re-checks each proposal and sets the final status. The gate routes on the verifier's discrete status instead of a confidence float. A markdown decisions log records both passes' reasoning per form.

**Tech Stack:** Python ≥3.11, Pydantic v2, OpenAI Responses API (`client.responses.parse`), pytest, openpyxl (existing).

## Global Constraints

- Never invent data: only values present in the profile (or an exact form option) may be filled.
- Verbatim values (SIREN, SIRET, IBAN) must stay exact — no generated/reformatted values.
- Mapping runs once per form; two LLM calls acceptable, gated by `mapping_verify`.
- All logging/tracing is best-effort: a logging failure must never crash a run.
- Follow existing patterns: frozen Pydantic models (`ConfigDict(frozen=True)` / `_FROZEN`), `client.responses.parse(model=, instructions=, input=, text_format=, max_output_tokens=)`.
- Run `pytest` after each task; commit per task. Work on branch `feat/semantic-two-pass-mapping` (already created). Never commit to `main`.

---

### Task 1: Config — field descriptions + new settings

**Files:**
- Modify: `src/formfiller/config.py` (`ProfileField`, `AppConfig`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `ProfileField.description: str = ""`; `AppConfig.decisions_dir: str = "./decisions"`; `AppConfig.mapping_verify: bool = True`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_profile_field_has_optional_description():
    from formfiller.config import ProfileField
    f = ProfileField(name="x", value="v", description="what x is")
    assert f.description == "what x is"
    assert ProfileField(name="y", value="v").description == ""


def test_appconfig_has_decisions_dir_and_verify_defaults():
    from formfiller.config import AppConfig
    cfg = AppConfig(excel_log_path="x.xlsx")
    assert cfg.decisions_dir == "./decisions"
    assert cfg.mapping_verify is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_profile_field_has_optional_description tests/test_config.py::test_appconfig_has_decisions_dir_and_verify_defaults -v`
Expected: FAIL (`description` / `decisions_dir` not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/formfiller/config.py`, add a field to `ProfileField`:

```python
class ProfileField(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    value: str
    description: str = ""
    aliases: tuple[str, ...] = ()
```

And to `AppConfig` (after `traces_dir`):

```python
    traces_dir: str = "./traces"
    decisions_dir: str = "./decisions"
    mapping_verify: bool = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/config.py tests/test_config.py
git commit -m "feat(config): add ProfileField.description, decisions_dir, mapping_verify"
```

---

### Task 2: Models — rationale, DecisionRecord, MappingOutcome

**Files:**
- Modify: `src/formfiller/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `MappingResult`, `MappedAnswer` (existing).
- Produces: `MappedAnswer.rationale: str = ""`; `DecisionRecord`; `MappingOutcome(result: MappingResult, decisions: tuple[DecisionRecord, ...])`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_mapped_answer_has_rationale_default():
    from formfiller.models import MappedAnswer
    a = MappedAnswer(question_id="q", profile_field="f", value="v",
                     confidence=0.9, status="matched")
    assert a.rationale == ""
    assert a.model_copy(update={"rationale": "because"}).rationale == "because"


def test_mapping_outcome_carries_result_and_decisions():
    from formfiller.models import (
        MappedAnswer, MappingResult, DecisionRecord, MappingOutcome,
    )
    rec = DecisionRecord(
        question_id="q", label="L", type="text", required=True,
        profile_field="f", value="v",
        propose_status="matched", propose_confidence=0.9, propose_rationale="p",
        final_status="matched", final_confidence=0.95, verify_rationale="v",
        final_action="fill",
    )
    res = MappingResult(answers=(MappedAnswer(question_id="q", profile_field="f",
                        value="v", confidence=0.95, status="matched"),))
    out = MappingOutcome(result=res, decisions=(rec,))
    assert out.result.by_id("q").value == "v"
    assert out.decisions[0].final_action == "fill"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py::test_mapped_answer_has_rationale_default tests/test_models.py::test_mapping_outcome_carries_result_and_decisions -v`
Expected: FAIL (`rationale` / `DecisionRecord` / `MappingOutcome` not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/formfiller/models.py`, add `rationale` to `MappedAnswer`:

```python
class MappedAnswer(BaseModel):
    model_config = _FROZEN
    question_id: str
    profile_field: Optional[str]
    value: Optional[str]
    confidence: float
    status: MappingStatus
    rationale: str = ""
```

After `MappingResult`, add:

```python
class DecisionRecord(BaseModel):
    model_config = _FROZEN
    question_id: str
    label: str
    type: str
    required: bool
    profile_field: Optional[str]
    value: Optional[str]
    propose_status: str
    propose_confidence: float
    propose_rationale: str
    final_status: str
    final_confidence: float
    verify_rationale: str
    final_action: Literal["fill", "review", "blank"]


class MappingOutcome(BaseModel):
    model_config = _FROZEN
    result: MappingResult
    decisions: tuple[DecisionRecord, ...] = ()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/models.py tests/test_models.py
git commit -m "feat(models): add rationale, DecisionRecord, MappingOutcome"
```

---

### Task 3: Mapper pass 1 — rationale + description-aware prompt

**Files:**
- Modify: `src/formfiller/field_mapper.py` (`LLMMappedAnswer`, `_SYSTEM`, `_build_user_prompt`, `map_fields`)
- Test: `tests/test_field_mapper.py`

**Interfaces:**
- Consumes: `MappedAnswer.rationale` (Task 2), `ProfileField.description` (Task 1).
- Produces: `LLMMappedAnswer.rationale: str = ""`; `map_fields` propagates rationale into `MappedAnswer`; profile prompt includes `description`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_field_mapper.py`:

```python
def test_map_fields_propagates_rationale():
    parsed = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.95,
                        status="matched", rationale="company name -> legal name"),
    ])
    result = map_fields(_StubClient(parsed), "gpt-5.4", _schema(), _profile())
    assert result.by_id("q1").rationale == "company name -> legal name"


def test_user_prompt_includes_field_description():
    from formfiller.field_mapper import _build_user_prompt
    prof = (ProfileField(name="addressing_line", value="X",
                         description="e-invoicing routing line, NOT postal"),)
    prompt = _build_user_prompt(_schema(), prof)
    assert "e-invoicing routing line" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_field_mapper.py::test_map_fields_propagates_rationale tests/test_field_mapper.py::test_user_prompt_includes_field_description -v`
Expected: FAIL (`rationale` not accepted / description not in prompt).

- [ ] **Step 3: Write minimal implementation**

In `src/formfiller/field_mapper.py`:

Add `rationale` to the LLM output model:

```python
class LLMMappedAnswer(BaseModel):
    question_id: str
    profile_field: Optional[str]
    value: Optional[str]
    confidence: float
    status: Literal["matched", "no_data", "ambiguous"]
    rationale: str = ""
```

Replace `_SYSTEM` with a description-aware, commit-oriented prompt (keep the words `option`, `verbatim`, `exactly` so existing prompt tests pass):

```python
_SYSTEM = (
    "You map web-form questions to a fixed company data profile. Reason from each "
    "profile field's 'description' (what it is and when it applies), not just its "
    "name. For each question, choose the single best-matching profile field and "
    "return its value, a confidence in [0,1], a status, and a one-sentence "
    "'rationale' explaining your choice. Use status 'matched' when a profile field "
    "answers the question — commit to it even if the wording differs from the field "
    "name. Use 'no_data' when the profile genuinely has nothing relevant. Use "
    "'ambiguous' ONLY when two or more fields could each plausibly answer, or the "
    "question itself is unclear. When a question lists 'options' (a choice "
    "question), the value MUST be exactly one of those options, copied verbatim "
    "(same spelling, case, separators) — never a paraphrase. Never invent data: "
    "values must come from the profile. Respond with the structured data only."
)
```

Include `description` in the profile payload:

```python
def _build_user_prompt(schema: FormSchema, profile: Sequence[ProfileField]) -> str:
    profile_lines = [
        {"field": f.name, "value": f.value,
         "description": f.description, "aliases": list(f.aliases)}
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
```

Propagate rationale in `map_fields` (the `MappedAnswer(...)` construction inside the `answers = tuple(...)` comprehension):

```python
    answers = tuple(
        MappedAnswer(
            question_id=a.question_id,
            profile_field=a.profile_field,
            value=a.value,
            confidence=a.confidence,
            status=a.status,
            rationale=a.rationale,
        )
        for a in parsed.answers
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_field_mapper.py -v`
Expected: PASS (all existing tests still green — `rationale` defaults to `""`).

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/field_mapper.py tests/test_field_mapper.py
git commit -m "feat(mapper): pass-1 rationale and description-aware prompt"
```

---

### Task 4: Mapper verify pass — `map_and_verify` → `MappingOutcome`

**Files:**
- Modify: `src/formfiller/field_mapper.py`
- Test: `tests/test_field_mapper.py`

**Interfaces:**
- Consumes: `map_fields` (Task 3), `_resolve_choice_values`, `DecisionRecord`/`MappingOutcome` (Task 2).
- Produces: `map_and_verify(client, deployment, schema, profile, verify=True, max_output_tokens=16000) -> MappingOutcome`; helpers `_verify`, `_action_for`, `_outcome_from_single`; models `LLMVerifiedAnswer`, `LLMVerification`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_field_mapper.py` (a sequential stub that returns queued parsed objects, one per `.parse` call):

```python
from formfiller.field_mapper import map_and_verify
from formfiller.models import MappingOutcome


class _SeqResponses:
    def __init__(self, parsed_seq):
        self._seq = list(parsed_seq)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return _StubResponse(self._seq.pop(0))


class _SeqClient:
    def __init__(self, parsed_seq):
        self.responses = _SeqResponses(parsed_seq)


def test_verify_can_rescue_timid_match():
    from formfiller.field_mapper import LLMVerification, LLMVerifiedAnswer
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.4,
                        status="ambiguous", rationale="unsure"),
    ])
    verify = LLMVerification(answers=[
        LLMVerifiedAnswer(question_id="q1", profile_field="company_legal_name",
                          value="Ginesis Finance SAS", confidence=0.97,
                          status="matched", rationale="clearly the legal name"),
    ])
    out = map_and_verify(_SeqClient([propose, verify]), "gpt-5.4", _schema(), _profile())
    assert isinstance(out, MappingOutcome)
    ans = out.result.by_id("q1")
    assert ans.status == "matched"
    assert ans.value == "Ginesis Finance SAS"
    rec = next(d for d in out.decisions if d.question_id == "q1")
    assert rec.propose_status == "ambiguous"
    assert rec.final_status == "matched"
    assert rec.final_action == "fill"
    assert "legal name" in rec.verify_rationale


def test_verify_rejects_value_not_in_profile_keeps_pass1():
    from formfiller.field_mapper import LLMVerification, LLMVerifiedAnswer
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.9,
                        status="matched", rationale="ok"),
    ])
    verify = LLMVerification(answers=[
        LLMVerifiedAnswer(question_id="q1", profile_field="company_legal_name",
                          value="Some Hallucinated Name", confidence=0.9,
                          status="matched", rationale="changed"),
    ])
    out = map_and_verify(_SeqClient([propose, verify]), "gpt-5.4", _schema(), _profile())
    assert out.result.by_id("q1").value == "Ginesis Finance SAS"  # pass-1 value kept


def test_verify_failure_falls_back_to_pass1():
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.9,
                        status="matched", rationale="ok"),
    ])

    class _BoomResponses(_SeqResponses):
        def parse(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _StubResponse(propose)
            raise RuntimeError("verify boom")

    client = _SeqClient([propose])
    client.responses = _BoomResponses([propose])
    out = map_and_verify(client, "gpt-5.4", _schema(), _profile())
    assert out.result.by_id("q1").value == "Ginesis Finance SAS"
    rec = next(d for d in out.decisions if d.question_id == "q1")
    assert "unavailable" in rec.verify_rationale.lower()


def test_verify_false_skips_second_call():
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.9,
                        status="matched", rationale="ok"),
    ])
    client = _SeqClient([propose])
    out = map_and_verify(client, "gpt-5.4", _schema(), _profile(), verify=False)
    assert len(client.responses.calls) == 1
    assert out.result.by_id("q1").status == "matched"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_field_mapper.py -k verify -v`
Expected: FAIL (`map_and_verify` / `LLMVerification` not defined).

- [ ] **Step 3: Write minimal implementation**

In `src/formfiller/field_mapper.py`, add `logging` import at top if missing (`import logging` and `logger = logging.getLogger(__name__)`), then add the verify models, helpers, and orchestrator:

```python
class LLMVerifiedAnswer(BaseModel):
    question_id: str
    profile_field: Optional[str]
    value: Optional[str]
    confidence: float
    status: Literal["matched", "no_data", "ambiguous"]
    rationale: str = ""


class LLMVerification(BaseModel):
    answers: list[LLMVerifiedAnswer]


_VERIFY_SYSTEM = (
    "You are a strict reviewer of a first-pass mapping from form questions to a "
    "company data profile. For each question you are given the proposed field, "
    "value, status and rationale. Decide the FINAL answer: confirm it, correct the "
    "field/value (using ONLY profile values), or flag it. Return status 'matched' "
    "when a profile field clearly answers it (commit even if wording differs), "
    "'no_data' when the profile has nothing relevant, and 'ambiguous' ONLY when two "
    "or more fields genuinely compete or the question is unclear. For choice "
    "questions the value MUST be exactly one of the listed options, verbatim. Give "
    "a one-sentence 'rationale' for your verdict. Never invent data."
)


def _verify(client, deployment: str, schema: FormSchema,
            profile: Sequence[ProfileField], proposed: MappingResult,
            max_output_tokens: int) -> LLMVerification:
    proposed_payload = [
        {"question_id": a.question_id, "profile_field": a.profile_field,
         "value": a.value, "status": a.status, "rationale": a.rationale}
        for a in proposed.answers
    ]
    user = (
        _build_user_prompt(schema, profile)
        + "\n\nFIRST-PASS PROPOSALS (review each):\n"
        + json.dumps(proposed_payload, ensure_ascii=False, indent=2)
        + "\n\nReturn one verified answer object per question_id."
    )
    completion = client.responses.parse(
        model=deployment, instructions=_VERIFY_SYSTEM, input=user,
        text_format=LLMVerification, max_output_tokens=max_output_tokens,
    )
    parsed = getattr(completion, "output_parsed", None)
    if parsed is None:
        status = getattr(completion, "status", "unknown")
        raise RuntimeError(f"verify pass returned no structured output (status={status}).")
    return parsed


def _action_for(q, ans) -> str:
    from formfiller.models import QuestionType
    if q.type == QuestionType.UNSUPPORTED:
        return "review"
    if ans is None:
        return "review" if q.required else "blank"
    if ans.status == "ambiguous":
        return "review"
    if ans.status == "no_data" or ans.value is None:
        return "review" if q.required else "blank"
    return "fill"


def _outcome_from_single(schema: FormSchema, proposed: MappingResult,
                         verify_note: str = "") -> "MappingOutcome":
    from formfiller.models import DecisionRecord, MappingOutcome
    by_id = {a.question_id: a for a in proposed.answers}
    decisions = []
    for q in schema.questions:
        a = by_id.get(q.id)
        decisions.append(DecisionRecord(
            question_id=q.id, label=q.label, type=q.type.value, required=q.required,
            profile_field=a.profile_field if a else None,
            value=a.value if a else None,
            propose_status=a.status if a else "no_data",
            propose_confidence=a.confidence if a else 0.0,
            propose_rationale=a.rationale if a else "",
            final_status=a.status if a else "no_data",
            final_confidence=a.confidence if a else 0.0,
            verify_rationale=verify_note,
            final_action=_action_for(q, a),
        ))
    return MappingOutcome(result=proposed, decisions=tuple(decisions))


def map_and_verify(client, deployment: str, schema: FormSchema,
                   profile: Sequence[ProfileField], verify: bool = True,
                   max_output_tokens: int = 16000) -> "MappingOutcome":
    """Two-pass mapping. Pass 1 proposes (with rationale); pass 2 verifies and
    sets the final status. Returns a MappingOutcome (result for the gate +
    decisions for the reasoning log). Falls back to pass-1 if verify fails."""
    from formfiller.models import DecisionRecord, MappedAnswer, MappingOutcome
    proposed = map_fields(client, deployment, schema, profile, max_output_tokens)
    if not verify:
        return _outcome_from_single(schema, proposed)
    try:
        verification = _verify(client, deployment, schema, profile, proposed, max_output_tokens)
    except Exception as exc:  # noqa: BLE001 — verify is best-effort
        logger.warning("verify pass failed (%s); using pass-1 mapping.", exc)
        return _outcome_from_single(schema, proposed, verify_note="(verification unavailable)")

    allowed = {f.value for f in profile if f.value}
    opt_sets = {q.id: set(q.options) for q in schema.questions if q.options}
    proposed_by_id = {a.question_id: a for a in proposed.answers}
    verified_by_id = {v.question_id: v for v in verification.answers}

    merged = []
    for q in schema.questions:
        p = proposed_by_id.get(q.id)
        v = verified_by_id.get(q.id)
        if v is None:
            merged.append(p or MappedAnswer(question_id=q.id, profile_field=None,
                          value=None, confidence=0.0, status="no_data",
                          rationale="(no verifier response)"))
            continue
        ok = (v.value in allowed) or (q.id in opt_sets and v.value in opt_sets[q.id])
        value = v.value if ok else (p.value if p else None)
        field = v.profile_field if ok else (p.profile_field if p else None)
        merged.append(MappedAnswer(question_id=q.id, profile_field=field, value=value,
                      confidence=v.confidence, status=v.status, rationale=v.rationale))

    final_result = _resolve_choice_values(schema, MappingResult(answers=tuple(merged)))
    final_by_id = {a.question_id: a for a in final_result.answers}

    decisions = []
    for q in schema.questions:
        p = proposed_by_id.get(q.id)
        fa = final_by_id.get(q.id)
        decisions.append(DecisionRecord(
            question_id=q.id, label=q.label, type=q.type.value, required=q.required,
            profile_field=fa.profile_field if fa else None,
            value=fa.value if fa else None,
            propose_status=p.status if p else "no_data",
            propose_confidence=p.confidence if p else 0.0,
            propose_rationale=p.rationale if p else "",
            final_status=fa.status if fa else "no_data",
            final_confidence=fa.confidence if fa else 0.0,
            verify_rationale=fa.rationale if fa else "",
            final_action=_action_for(q, fa),
        ))
    return MappingOutcome(result=final_result, decisions=tuple(decisions))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_field_mapper.py -v`
Expected: PASS (all, including the new `-k verify` tests).

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/field_mapper.py tests/test_field_mapper.py
git commit -m "feat(mapper): add propose-then-verify map_and_verify returning MappingOutcome"
```

---

### Task 5: Gate — route on status, retire the confidence float

**Files:**
- Modify: `src/formfiller/confidence.py` (`evaluate_gate`)
- Test: `tests/test_confidence.py`, `tests/test_orchestrator.py` (one test)

**Interfaces:**
- Produces: `evaluate_gate` no longer routes on `confidence < threshold`; routes on `status` only. `threshold` param kept (unused) for signature stability.

- [ ] **Step 1: Update the tests first (they encode the new behavior)**

In `tests/test_confidence.py`, **replace** `test_low_confidence_field_routes_to_review` with:

```python
def test_low_confidence_matched_now_fills_not_routes():
    # Confidence is no longer a gate: a 'matched' answer fills regardless of score.
    schema = _schema(_q("q1"))
    result = MappingResult(answers=(_ans("q1", confidence=0.3),))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "submit"
    assert {f.question_id for f in decision.fields_to_fill} == {"q1"}
```

And in `tests/test_confidence.py`, **replace** `test_review_decision_still_carries_fillable_fields_for_screenshot` so it forces review via an *ambiguous* answer rather than low confidence:

```python
def test_review_decision_still_carries_fillable_fields_for_screenshot():
    schema = _schema(_q("q1"), _q("q2", required=False))
    result = MappingResult(answers=(
        # q1 fills; q2 ambiguous -> routes whole form to review
        _ans("q1"),
        MappedAnswer(question_id="q2", profile_field="f", value="v2",
                     confidence=0.9, status="ambiguous"),
    ))
    decision = evaluate_gate(schema, result, threshold=0.8)
    assert decision.action == "review"
    assert {f.question_id for f in decision.fields_to_fill} == {"q1"}
```

In `tests/test_orchestrator.py`, **replace** `test_low_confidence_parks_for_review_and_logs_manual` so review is triggered by `status="ambiguous"` (confidence no longer routes):

```python
def test_ambiguous_parks_for_review_and_logs_manual(tmp_path):
    mapping = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="company_legal_name",
                     value="Ginesis Finance SAS", confidence=0.9, status="ambiguous"),
    ))
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: mapping,
        fill_and_submit=lambda url, instr, dry_run: (b"\x89PNG", False, len(instr)),
    )
    cfg = _config(tmp_path)
    result = process_email(_email("link https://forms.office.com/r/x"), cfg, _PROFILE, hooks)
    assert result.status == "manual"
    assert "ambiguous" in result.review_reason.lower()
    assert (tmp_path / "queue" / "E1").exists()
```

> Note: `test_orchestrator.py` still passes a bare `MappingResult` from its fake `map_fields` — that is updated to `MappingOutcome` in Task 7. This task keeps it as-is so the suite stays green between tasks.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_confidence.py tests/test_orchestrator.py -v`
Expected: FAIL on the rewritten tests (old gate still routes on confidence).

- [ ] **Step 3: Write minimal implementation**

In `src/formfiller/confidence.py`, **remove** the low-confidence branch inside the matched case. Change:

```python
        # matched with a value — collect the fill (shown even if low-confidence)
        if answer.confidence < threshold:
            review_reasons.append(f"Low confidence ({answer.confidence:.2f}) mapping '{q.label}'.")
        fields_to_fill.append(FillInstruction(question_id=q.id, value=answer.value))
```

to:

```python
        # matched with a value — collect the fill. Confidence is no longer a gate;
        # the verifier's discrete status (ambiguous / no_data) is the safety net.
        fields_to_fill.append(FillInstruction(question_id=q.id, value=answer.value))
```

Also update the `evaluate_gate` docstring line that mentions "or a matched value below `threshold`" to note confidence is advisory.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_confidence.py tests/test_orchestrator.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/confidence.py tests/test_confidence.py tests/test_orchestrator.py
git commit -m "feat(gate): route on discrete status, retire confidence-float gating"
```

---

### Task 6: Decision log writer

**Files:**
- Create: `src/formfiller/decision_log.py`
- Test: `tests/test_decision_log.py`

**Interfaces:**
- Consumes: `DecisionRecord` (Task 2).
- Produces: `write_decisions_md(decisions_dir, entry_id, form_title, form_url, decisions) -> Optional[Path]` (best-effort; returns the written path or `None`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_decision_log.py`:

```python
from pathlib import Path
from formfiller.models import DecisionRecord
from formfiller.decision_log import write_decisions_md


def _rec(**kw):
    base = dict(question_id="q1", label="Quel est votre SIREN ?", type="text",
                required=True, profile_field="siren", value="987654321",
                propose_status="matched", propose_confidence=0.9,
                propose_rationale="SIREN question -> siren",
                final_status="matched", final_confidence=0.97,
                verify_rationale="correct; not SIRET", final_action="fill")
    base.update(kw)
    return DecisionRecord(**base)


def test_writes_markdown_with_reasoning(tmp_path):
    path = write_decisions_md(str(tmp_path), "E1", "Adisseo", "https://x", (_rec(),))
    assert path is not None and Path(path).exists()
    text = Path(path).read_text(encoding="utf-8")
    assert "Quel est votre SIREN ?" in text
    assert "siren" in text
    assert "correct; not SIRET" in text
    assert "fill" in text


def test_write_failure_returns_none_does_not_raise(tmp_path):
    # point at a path that cannot be a directory (a file occupies it)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    result = write_decisions_md(str(blocker), "E1", "t", "u", (_rec(),))
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_decision_log.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write minimal implementation**

Create `src/formfiller/decision_log.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from formfiller.models import DecisionRecord


def _safe(name: str) -> str:
    stamp = "".join(c if c.isalnum() else "_" for c in name)
    return stamp or "form"


def write_decisions_md(decisions_dir, entry_id: str, form_title: str,
                       form_url: str,
                       decisions: Sequence[DecisionRecord]) -> Optional[Path]:
    """Write one human-readable markdown file per form capturing both passes'
    reasoning. Best-effort: never raises; returns the path written or None."""
    try:
        d = Path(decisions_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_safe(entry_id)}.md"
        lines = [f"# {form_title}", "", f"<{form_url}>", ""]
        for r in decisions:
            lines += [
                f"## {r.label}",
                f"- **field:** `{r.profile_field}`  **value:** {r.value!r}",
                f"- **action:** {r.final_action}  "
                f"(status: {r.final_status}, confidence {r.final_confidence:.2f})",
                f"- **propose:** {r.propose_rationale} "
                f"(status: {r.propose_status}, confidence {r.propose_confidence:.2f})",
                f"- **verify:** {r.verify_rationale}",
                "",
            ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except Exception as exc:  # noqa: BLE001 — logging must not crash a run
        print(f"[warn] decisions log write failed: {exc}")
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_decision_log.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/decision_log.py tests/test_decision_log.py
git commit -m "feat(decision-log): markdown per-form reasoning writer"
```

---

### Task 7: Wire pipelines to two-pass mapping + decisions log

**Files:**
- Modify: `src/formfiller/orchestrator.py` (`PipelineHooks`, `process_email`)
- Modify: `src/formfiller/cli.py` (`_build_hooks` `do_map`; `build_agent_run` mapper)
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `map_and_verify` (Task 4), `write_decisions_md` (Task 6), `MappingOutcome` (Task 2), `config.decisions_dir`/`mapping_verify` (Task 1).
- Produces: `PipelineHooks.map_fields: Callable[[FormSchema], MappingOutcome]`; `process_email` writes a decisions log per form.

- [ ] **Step 1: Update the orchestrator tests to the new hook contract**

In `tests/test_orchestrator.py`, add an import and a helper, then wrap every fake `map_fields` return in a `MappingOutcome`:

```python
from formfiller.models import MappingOutcome  # add to imports


def _outcome(mapping):
    return MappingOutcome(result=mapping, decisions=())
```

Change each hook definition from `map_fields=lambda schema: mapping` to
`map_fields=lambda schema: _outcome(mapping)`, and
`map_fields=lambda schema: MappingResult(answers=())` to
`map_fields=lambda schema: _outcome(MappingResult(answers=()))`.

Add one new test asserting the decisions log is written:

```python
def test_writes_decisions_log(tmp_path):
    from formfiller.models import DecisionRecord
    mapping = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="company_legal_name",
                     value="Ginesis Finance SAS", confidence=0.95, status="matched"),
    ))
    decisions = (DecisionRecord(
        question_id="q1", label="Company name", type="text", required=True,
        profile_field="company_legal_name", value="Ginesis Finance SAS",
        propose_status="matched", propose_confidence=0.95, propose_rationale="p",
        final_status="matched", final_confidence=0.95, verify_rationale="v",
        final_action="fill"),)
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: MappingOutcome(result=mapping, decisions=decisions),
        fill_and_submit=lambda url, instr, dry_run: (b"\x89PNG", False, len(instr)),
    )
    cfg = _config(tmp_path, dry_run=True)
    process_email(_email("link https://forms.office.com/r/x"), cfg, _PROFILE, hooks)
    log = tmp_path / "decisions" / "E1.md"
    assert log.exists()
    assert "Company name" in log.read_text(encoding="utf-8")
```

Add `decisions_dir=str(tmp_path / "decisions")` to the `_config(...)` helper's `AppConfig(...)` so the log lands in the temp dir:

```python
def _config(tmp_path, dry_run=False):
    return AppConfig(
        confidence_threshold=0.8,
        dry_run=dry_run,
        excel_log_path=str(tmp_path / "log.xlsx"),
        review_queue_dir=str(tmp_path / "queue"),
        decisions_dir=str(tmp_path / "decisions"),
        inbox_list_count=10,
        azure_openai_deployment="gpt-4o",
        azure_api_version="2024-10-21",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_orchestrator.py -v`
Expected: FAIL (`process_email` still expects `MappingResult` from the hook / no decisions log written).

- [ ] **Step 3: Update the orchestrator**

In `src/formfiller/orchestrator.py`:

Change the import line to add the outcome type and writer:

```python
from formfiller.models import EmailMessage, FormSchema, MappingResult, MappingOutcome
from formfiller.decision_log import write_decisions_md
```

Update the `PipelineHooks.map_fields` annotation and docstring:

```python
    read_form: Callable[[str], FormSchema]
    map_fields: Callable[[FormSchema], MappingOutcome]
    # returns (screenshot_bytes, submitted?, fields_actually_filled)
    fill_and_submit: Callable[[str, tuple[FillInstruction, ...], bool], tuple[bytes, bool, int]]
```

In `process_email`, change the read+map block to unpack the outcome and write the log. Replace:

```python
    try:
        schema = hooks.read_form(url)
        mapping = hooks.map_fields(schema)
    except Exception as exc:  # noqa: BLE001 — isolate one bad form
        return _finish(status="fail", review_reason=f"Read/map error: {exc}")

    base["overall_confidence"] = _overall_confidence(mapping)
```

with:

```python
    try:
        schema = hooks.read_form(url)
        outcome = hooks.map_fields(schema)
    except Exception as exc:  # noqa: BLE001 — isolate one bad form
        return _finish(status="fail", review_reason=f"Read/map error: {exc}")

    mapping = outcome.result
    write_decisions_md(config.decisions_dir, email.entry_id, schema.title, url,
                       outcome.decisions)
    base["overall_confidence"] = _overall_confidence(mapping)
```

- [ ] **Step 4: Update the production wiring in cli.py**

In `src/formfiller/cli.py` `_build_hooks`, change the import and `do_map`:

```python
    from formfiller.field_mapper import map_and_verify
```
```python
    def do_map(schema):
        return map_and_verify(client, config.azure_openai_deployment, schema,
                              profile, verify=config.mapping_verify)
```

In `build_agent_run`, give the agent the same two-pass matching quality. Change its mapper import and the `mapper=` argument so the executor receives a verified `MappingResult`:

```python
    from formfiller.field_mapper import map_and_verify
```
```python
        executor = ToolExecutor(
            page=page, url=url,
            schema_reader=lambda: schema_from_page(page, url),
            mapper=lambda schema: map_and_verify(
                client, deployment, schema, profile,
                verify=config.mapping_verify).result,
            threshold=config.confidence_threshold, dry_run=config.dry_run,
            confirm=_terminal_confirm,
        )
```

> The agent's per-form markdown decisions log is intentionally out of scope here (see Risks); the agent already emits a JSONL reasoning trace via `TraceWriter`, and the deterministic pipeline is the one in active use.

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS (111+ tests).

- [ ] **Step 6: Commit**

```bash
git add src/formfiller/orchestrator.py src/formfiller/cli.py tests/test_orchestrator.py
git commit -m "feat(pipeline): use two-pass mapping and write per-form decisions log"
```

---

### Task 8: Backfill `profile.yaml` descriptions

**Files:**
- Modify: `profile.yaml`
- Test: `tests/test_profile_descriptions.py` (new)

**Interfaces:**
- Consumes: `load_profile`, `ProfileField.description` (Task 1).

- [ ] **Step 1: Write the failing test**

Create `tests/test_profile_descriptions.py`:

```python
from formfiller.config import load_profile


def test_every_profile_field_has_a_description():
    profile = load_profile("profile.yaml")
    missing = [f.name for f in profile if not f.description.strip()]
    assert missing == [], f"fields missing description: {missing}"


def test_addressing_disambiguation_is_documented():
    by_name = {f.name: f for f in load_profile("profile.yaml")}
    # the e-invoicing routing line must be documented as distinct from postal address
    al = by_name["addressing_line"].description.lower()
    assert "électronique" in al or "electronic" in al or "routing" in al
    ba = by_name["billing_address"].description.lower()
    assert "postal" in ba or "postale" in ba
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_profile_descriptions.py -v`
Expected: FAIL (fields have no `description` yet).

- [ ] **Step 3: Add a `description:` to every field in `profile.yaml`**

For each field add a concise `description:` line. Use these (adjust wording, keep meaning):

```yaml
  - name: company_legal_name
    value: "Ginesis Finance SAS"
    description: "Registered legal/company name (raison sociale) of the company."
    aliases: ["company name", "legal name", "raison sociale", "société"]
  - name: vat_number
    value: "FR0120000000"
    description: "Intra-EU VAT number (numéro de TVA intracommunautaire)."
    aliases: ["VAT", "VAT number", "N° TVA", "numéro de TVA", "tax id"]
  - name: siret
    value: "98765432155555"
    description: "14-digit SIRET establishment identifier (SIREN + 5-digit NIC). NOT the 9-digit SIREN."
    aliases: ["SIRET", "company registration number"]
  - name: contact_email
    value: "contact@ginesis-finance.com"
    description: "General contact email address for the company."
    aliases: ["email", "e-mail", "contact email", "adresse email"]
  - name: contact_phone
    value: "+33000000000"
    description: "Company contact phone number."
    aliases: ["phone", "telephone", "téléphone", "phone number"]
  - name: address_street
    value: "1 place Vendôme"
    description: "Street line only (number + street) of the postal address."
    aliases: ["street", "rue", "numéro et rue"]
  - name: address_city
    value: "Paris"
    description: "City of the postal address."
    aliases: ["city", "ville"]
  - name: address_postal_code
    value: "75001"
    description: "Postal/ZIP code of the postal address."
    aliases: ["postal code", "zip", "code postal"]
  - name: address_country
    value: "France"
    description: "Country of the postal address."
    aliases: ["country", "pays"]
  - name: billing_address
    description: "Full one-line POSTAL billing address (street, postal code, city, country) for questions asking for the whole address in one field. NOT the electronic e-invoicing routing line."
    value: "1 place Vendôme, 75001 Paris, France"
    aliases: ["adresse de facturation", "billing address", "adresse complète", "adresse postale", "full address", "adresse"]
  - name: iban
    value: "FR7600000000000000000000000"
    description: "Company bank account IBAN."
    aliases: ["IBAN", "bank account"]
  - name: siren
    value: "987654321"
    description: "9-digit SIREN company identifier. NOT the 14-digit SIRET."
    aliases: ["SIREN", "numéro SIREN", "SIREN (9 caractères)"]
  - name: accounting_contact
    value: "Jane Doe"
    description: "Name of the person in our accounting team who is the contact for this supplier/relationship."
    aliases: ["contact comptable", "contact dans vos équipes comptables", "accounting contact", "interlocuteur comptable", "en charge de la comptabilité"]
  - name: addressing_format
    description: "The e-invoicing addressing FORMAT/scheme used to route invoices (e.g. SIREN, SIREN+SIRET, with routing code). The model maps this to the form's specific options."
    value: "SIREN + SIRET"
    aliases: ["format d'adressage", "choix de format d'adressage", "addressing format"]
  - name: addressing_line
    description: "The e-invoicing ROUTING/addressing line used to deliver electronic invoices — the 'adresse de facturation électronique'. This is NOT the postal billing address; the word 'électronique' distinguishes it."
    value: "23 456 789 00012-PARIS"
    aliases: ["ligne d'adressage", "adressage", "addressing line", "adresse de facturation électronique", "adresse de facturation electronique", "adresse électronique de facturation"]
  - name: einvoicing_readiness
    value: "en cours"
    description: "Current readiness level for electronic invoicing rollout (e.g. not started / in progress / ready). Currently 'en cours' (in progress)."
    aliases: ["niveau de préparation à la facturation électronique", "préparation facturation électronique", "e-invoicing readiness", "niveau de préparation"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_profile_descriptions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add profile.yaml tests/test_profile_descriptions.py
git commit -m "feat(profile): add semantic descriptions to every field"
```

---

## Final verification

- [ ] Run the whole suite: `pytest -q` — expect all green.
- [ ] Manual smoke (optional, needs Azure + Outlook): run the CLI on the Adisséo form and confirm `./decisions/<entry_id>.md` shows per-question reasoning and the manual rate drops.

```bash
pytest -q
```

---

## Self-review (against the spec)

**Spec coverage:**
- Semantic profile (`description`) → Tasks 1, 8. ✓
- Two-pass propose→verify → Tasks 3, 4. ✓
- choice-snapping preserved → reused in Task 4 (`_resolve_choice_values`). ✓
- Status-based gate, retire float → Task 5. ✓
- Reasoning log (markdown, `./decisions/`) → Tasks 6, 7. ✓
- Shared mapper for both pipelines → Task 7 (deterministic full; agent gets two-pass matching). ✓ (agent markdown log explicitly deferred — see Risks)
- Config `decisions_dir`, `mapping_verify`, advisory threshold → Tasks 1, 5. ✓
- Error handling: verify-failure fallback (Task 4), best-effort log (Task 6). ✓
- Verbatim-value guard (reject hallucinated verifier value) → Task 4 (`allowed`/`opt_sets`). ✓

**Type consistency:** `MappingOutcome(result, decisions)`, `DecisionRecord` fields, and `map_and_verify` signature are used identically in Tasks 2, 4, 6, 7. `PipelineHooks.map_fields -> MappingOutcome` matches the cli `do_map` return (Task 7).

**Deviation from spec (intentional, right-sized):** The agent pipeline's *markdown* decisions log is deferred; the agent gains two-pass matching quality but keeps its existing JSONL trace for reasoning. Rationale: the deterministic pipeline is the one in active use, and wiring the markdown log through the agent loop's terminal outcome would expand scope across `agent/tools.py`, `agent/models.py`, and `agent/loop.py` for little immediate value. Tracked as a follow-up.
