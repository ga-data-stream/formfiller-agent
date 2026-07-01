# Verifier Model + Effort Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the mapping verifier (pass 2) run on a different model and/or reasoning effort than the proposer (pass 1), configurable in `config.yaml`.

**Architecture:** Add two optional `AppConfig` fields following the existing "blank → reuse" convention. `map_and_verify` gains two optional params and resolves the fallback itself (blank/None → reuse the proposer's model/effort), then routes them to the existing `_verify` call. The CLI passes the config values through on both the deterministic and agent paths. Pass 1 (`map_fields`) is untouched.

**Tech Stack:** Python ≥3.11, Pydantic v2, pytest, Azure OpenAI Responses API.

## Global Constraints

- Python ≥3.11 (uses `X | None` type syntax).
- Run `pytest` after each task; all tests must pass.
- Work on branch `feat/verifier-model-config` (already created). Never commit to `main`.
- Additive and 100% backward compatible: a config without the new keys keeps today's behavior (verifier = same model/effort as proposer).
- Do NOT touch the agent-loop LLM (`OpenAIResponsesAgentLLM`) — it is not a verifier.
- Field/param names, verbatim: `verifier_model_deployment` (config), `verifier_reasoning_effort` (config), `verifier_deployment` (function param), `verifier_reasoning_effort` (function param).

---

## File Structure

- **Modify** `src/formfiller/config.py` — add `verifier_model_deployment` and `verifier_reasoning_effort` to `AppConfig`.
- **Modify** `src/formfiller/field_mapper.py` — add the two optional params to `map_and_verify` and resolve the fallback before calling `_verify`.
- **Modify** `src/formfiller/cli.py` — pass the config values through in `do_map` (deterministic) and `mapper` (agent).
- **Modify** `config.yaml` — document the two new knobs, left empty.
- **Modify** `tests/test_config.py` — defaults + override + rejection.
- **Modify** `tests/test_field_mapper.py` — routing to pass 2 + fallback.
- **Modify** `tests/test_cli_agent.py` — new deterministic-path wiring test; update the `_fake_map_and_verify` signature so the agent test survives the new kwargs.

---

## Task 1: Config fields

**Files:**
- Modify: `src/formfiller/config.py:31` (after the `reasoning_effort` field)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (leaf).
- Produces: `AppConfig.verifier_model_deployment: str = ""`; `AppConfig.verifier_reasoning_effort: Literal["none","minimal","low","medium","high","xhigh"] | None = None`.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/test_config.py`:

```python
def test_appconfig_verifier_model_defaults():
    from formfiller.config import AppConfig
    cfg = AppConfig(excel_log_path="x.xlsx")
    assert cfg.verifier_model_deployment == ""     # blank → reuse mapping model
    assert cfg.verifier_reasoning_effort is None    # None → reuse reasoning_effort


def test_appconfig_verifier_model_overrides():
    from formfiller.config import AppConfig
    cfg = AppConfig(excel_log_path="x.xlsx",
                    verifier_model_deployment="gpt-5.4",
                    verifier_reasoning_effort="high")
    assert cfg.verifier_model_deployment == "gpt-5.4"
    assert cfg.verifier_reasoning_effort == "high"


def test_appconfig_rejects_unknown_verifier_reasoning_effort():
    import pytest
    from pydantic import ValidationError
    from formfiller.config import AppConfig
    with pytest.raises(ValidationError):
        AppConfig(excel_log_path="x.xlsx", verifier_reasoning_effort="turbo")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -k verifier -v`
Expected: FAIL — `test_appconfig_verifier_model_defaults` / `_overrides` error with an unexpected-keyword / missing-attribute error (`verifier_model_deployment` does not exist yet); the rejection test does NOT raise `ValidationError` (Pydantic would ignore the unknown field or the model has no such field).

- [ ] **Step 3: Add the fields**

In `src/formfiller/config.py`, immediately after the `reasoning_effort` field (currently line 31), add:

```python
    # Verifier (pass 2) may run on a different model / reasoning depth than the
    # proposer (pass 1). Blank → reuse azure_openai_deployment; None → reuse
    # reasoning_effort.
    verifier_model_deployment: str = ""
    verifier_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
```

(`Literal` is already imported at the top of the file; `X | None` is valid on Python ≥3.11.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -k verifier -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/config.py tests/test_config.py
git commit -m "feat(config): add verifier_model_deployment and verifier_reasoning_effort"
```

---

## Task 2: Route verifier model + effort in `map_and_verify`

**Files:**
- Modify: `src/formfiller/field_mapper.py:182-199` (`map_and_verify` signature + the `_verify` call)
- Test: `tests/test_field_mapper.py`

**Interfaces:**
- Consumes: existing `_verify(client, deployment, schema, profile, proposed, max_output_tokens, reasoning_effort=...)` (unchanged); existing test helpers `_SeqClient`, `LLMMapping`, `LLMMappedAnswer`, `LLMVerification`, `LLMVerifiedAnswer`, `_schema`, `_profile`.
- Produces: `map_and_verify(client, deployment, schema, profile, verify=True, max_output_tokens=16000, reasoning_effort="medium", verifier_deployment="", verifier_reasoning_effort=None)` — blank `verifier_deployment` → uses `deployment`; `None` `verifier_reasoning_effort` → uses `reasoning_effort`. Pass 1 (`map_fields`) still uses `deployment`/`reasoning_effort`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_field_mapper.py` (the `_SeqClient` helper already exists later in the file — these tests run fine regardless of definition order at import time since they reference it at call time):

```python
def test_map_and_verify_routes_verifier_model_and_effort_to_pass2():
    from formfiller.field_mapper import LLMVerification, LLMVerifiedAnswer
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.9,
                        status="matched", rationale="ok"),
    ])
    verify = LLMVerification(answers=[
        LLMVerifiedAnswer(question_id="q1", profile_field="company_legal_name",
                          value="Ginesis Finance SAS", confidence=0.95,
                          status="matched", rationale="ok"),
    ])
    client = _SeqClient([propose, verify])
    map_and_verify(client, "propose-dep", _schema(), _profile(),
                   reasoning_effort="low",
                   verifier_deployment="verify-dep",
                   verifier_reasoning_effort="high")
    calls = client.responses.calls
    assert len(calls) == 2
    # pass 1 keeps the proposer model + effort
    assert calls[0]["model"] == "propose-dep"
    assert calls[0]["reasoning"] == {"effort": "low"}
    # pass 2 uses the verifier model + effort
    assert calls[1]["model"] == "verify-dep"
    assert calls[1]["reasoning"] == {"effort": "high"}


def test_map_and_verify_verifier_falls_back_to_proposer():
    from formfiller.field_mapper import LLMVerification, LLMVerifiedAnswer
    propose = LLMMapping(answers=[
        LLMMappedAnswer(question_id="q1", profile_field="company_legal_name",
                        value="Ginesis Finance SAS", confidence=0.9,
                        status="matched", rationale="ok"),
    ])
    verify = LLMVerification(answers=[
        LLMVerifiedAnswer(question_id="q1", profile_field="company_legal_name",
                          value="Ginesis Finance SAS", confidence=0.95,
                          status="matched", rationale="ok"),
    ])
    client = _SeqClient([propose, verify])
    # verifier_deployment="" and verifier_reasoning_effort=None → reuse pass 1
    map_and_verify(client, "propose-dep", _schema(), _profile(),
                   reasoning_effort="low")
    calls = client.responses.calls
    assert calls[1]["model"] == "propose-dep"
    assert calls[1]["reasoning"] == {"effort": "low"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_field_mapper.py -k "routes_verifier or verifier_falls_back" -v`
Expected: FAIL — `test_map_and_verify_routes_verifier_model_and_effort_to_pass2` errors with `TypeError: map_and_verify() got an unexpected keyword argument 'verifier_deployment'`. (The fallback test would pass today, but keep it — it guards the fallback after Step 3.)

- [ ] **Step 3: Add the params and resolve the fallback**

In `src/formfiller/field_mapper.py`, change the `map_and_verify` signature (currently lines 182-185) to:

```python
def map_and_verify(client, deployment: str, schema: FormSchema,
                   profile: Sequence[ProfileField], verify: bool = True,
                   max_output_tokens: int = 16000,
                   reasoning_effort: str = "medium",
                   verifier_deployment: str = "",
                   verifier_reasoning_effort: str | None = None) -> "MappingOutcome":
```

Then replace the existing verify call block (currently lines 194-199):

```python
    try:
        verification = _verify(client, deployment, schema, profile, proposed,
                               max_output_tokens, reasoning_effort=reasoning_effort)
    except Exception as exc:  # noqa: BLE001 — verify is best-effort
        logger.warning("verify pass failed (%s); using pass-1 mapping.", exc)
        return _outcome_from_single(schema, proposed, verify_note="(verification unavailable)")
```

with:

```python
    v_dep = verifier_deployment or deployment
    v_effort = verifier_reasoning_effort or reasoning_effort
    try:
        verification = _verify(client, v_dep, schema, profile, proposed,
                               max_output_tokens, reasoning_effort=v_effort)
    except Exception as exc:  # noqa: BLE001 — verify is best-effort
        logger.warning("verify pass failed (%s); using pass-1 mapping.", exc)
        return _outcome_from_single(schema, proposed, verify_note="(verification unavailable)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_field_mapper.py -k "routes_verifier or verifier_falls_back" -v`
Expected: PASS (2 tests).

Then run the full mapper suite to confirm no regression (the existing `test_map_and_verify_passes_reasoning_effort_to_both_passes` still holds because both passes default to `reasoning_effort` when the verifier args are unset):

Run: `pytest tests/test_field_mapper.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/field_mapper.py tests/test_field_mapper.py
git commit -m "feat(mapper): route verifier model + reasoning_effort to pass 2, fall back to proposer"
```

---

## Task 3: Wire the CLI (both paths) + `config.yaml`

**Files:**
- Modify: `src/formfiller/cli.py:47-50` (`do_map`, deterministic) and `src/formfiller/cli.py:90-92` (`mapper`, agent)
- Modify: `config.yaml` (after the `reasoning_effort` line)
- Test: `tests/test_cli_agent.py`

**Interfaces:**
- Consumes: `map_and_verify(..., verifier_deployment=..., verifier_reasoning_effort=...)` from Task 2; `AppConfig.verifier_model_deployment` / `.verifier_reasoning_effort` from Task 1; `cli._build_hooks(config, profile) -> PipelineHooks` with `.map_fields`.
- Produces: no new public API — both CLI mapping call sites forward the config values.

- [ ] **Step 1: Write the failing test + update the agent fake**

Add this new test to `tests/test_cli_agent.py`:

```python
def test_build_hooks_passes_verifier_config_to_map(monkeypatch, tmp_path):
    # The deterministic hook's mapper must forward the verifier model + effort
    # from config to map_and_verify.
    import formfiller.cli as cli
    from formfiller.config import AppConfig, ProfileField
    from formfiller.models import MappingResult, MappingOutcome, FormSchema

    captured = {}

    def _capture(client, deployment, schema, profile, verify=True,
                 max_output_tokens=16000, reasoning_effort="medium",
                 verifier_deployment="", verifier_reasoning_effort=None):
        captured.update(
            deployment=deployment, reasoning_effort=reasoning_effort,
            verifier_deployment=verifier_deployment,
            verifier_reasoning_effort=verifier_reasoning_effort,
        )
        return MappingOutcome(result=MappingResult(answers=()), decisions=())

    # Patch BEFORE _build_hooks runs (it does `from ... import map_and_verify` at call time).
    monkeypatch.setattr("formfiller.field_mapper.map_and_verify", _capture)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://r.services.ai.azure.com")

    cfg = AppConfig(excel_log_path=str(tmp_path / "l.xlsx"),
                    azure_openai_deployment="main-dep", reasoning_effort="low",
                    verifier_model_deployment="verify-dep",
                    verifier_reasoning_effort="high")
    profile = (ProfileField(name="siren", value="123"),)
    hooks = cli._build_hooks(cfg, profile)

    schema = FormSchema(url="https://forms.office.com/r/x", title="T", questions=())
    hooks.map_fields(schema)

    assert captured["deployment"] == "main-dep"
    assert captured["reasoning_effort"] == "low"
    assert captured["verifier_deployment"] == "verify-dep"
    assert captured["verifier_reasoning_effort"] == "high"
```

In the same file, update the existing `_fake_map_and_verify` inside `test_build_agent_run_writes_decisions_log` (currently around lines 52-53) so it accepts the new kwargs (otherwise the agent `mapper` call breaks once Step 3 lands). Change its signature from:

```python
    def _fake_map_and_verify(client, deployment, schema, profile, verify=True,
                             max_output_tokens=16000, reasoning_effort="medium"):
```

to:

```python
    def _fake_map_and_verify(client, deployment, schema, profile, verify=True,
                             max_output_tokens=16000, reasoning_effort="medium",
                             verifier_deployment="", verifier_reasoning_effort=None):
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `pytest tests/test_cli_agent.py::test_build_hooks_passes_verifier_config_to_map -v`
Expected: FAIL — `assert captured["verifier_deployment"] == "verify-dep"` fails because `do_map` does not forward the config value yet (captured value is the default `""`).

- [ ] **Step 3: Wire both call sites + document in config.yaml**

In `src/formfiller/cli.py`, `do_map` (deterministic path, currently lines 47-50):

```python
    def do_map(schema):
        return map_and_verify(client, config.azure_openai_deployment, schema,
                              profile, verify=config.mapping_verify,
                              reasoning_effort=config.reasoning_effort,
                              verifier_deployment=config.verifier_model_deployment,
                              verifier_reasoning_effort=config.verifier_reasoning_effort)
```

In `src/formfiller/cli.py`, `mapper` (agent path, currently lines 90-92):

```python
        def mapper(schema):
            outcome = map_and_verify(client, deployment, schema, profile,
                                     verify=config.mapping_verify,
                                     reasoning_effort=config.reasoning_effort,
                                     verifier_deployment=config.verifier_model_deployment,
                                     verifier_reasoning_effort=config.verifier_reasoning_effort)
            last["outcome"] = outcome
            last["title"] = schema.title
            return outcome.result
```

In `config.yaml`, add two lines immediately after the `reasoning_effort:` line (line 9):

```yaml
verifier_model_deployment: ""    # blank → reuse azure_openai_deployment for the verify pass (pass 2)
verifier_reasoning_effort:       # blank → reuse reasoning_effort for the verify pass (pass 2)
```

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run: `pytest tests/test_cli_agent.py -v`
Expected: PASS (all, including the new wiring test and the updated decisions-log test).

- [ ] **Step 5: Full suite + commit**

Run: `pytest`
Expected: PASS (whole suite green).

```bash
git add src/formfiller/cli.py config.yaml tests/test_cli_agent.py
git commit -m "feat(cli): forward verifier model + reasoning_effort config on both fill paths"
```

---

## Self-Review

**1. Spec coverage:**
- Config fields (`verifier_model_deployment`, `verifier_reasoning_effort`) → Task 1. ✓
- Fallback resolution centralized in `map_and_verify` (blank/None → reuse) → Task 2. ✓
- CLI wiring on deterministic + agent paths → Task 3. ✓
- `config.yaml` documented knobs → Task 3. ✓
- Tests: config defaults/override/reject → Task 1; routing + fallback → Task 2; wiring → Task 3. ✓
- Non-objective: agent-loop LLM untouched → confirmed, no task modifies `OpenAIResponsesAgentLLM`. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows full code and exact commands. ✓

**3. Type consistency:** `verifier_model_deployment` (config, `str`) and `verifier_reasoning_effort` (config, `Literal|None`) are read in Task 3 and passed to `map_and_verify`'s `verifier_deployment` (`str`) / `verifier_reasoning_effort` (`str|None`) params defined in Task 2. The `blank/None → reuse` semantics match across config, function, and tests. The `_verify` signature is unchanged and receives the resolved `v_dep`/`v_effort`. ✓
