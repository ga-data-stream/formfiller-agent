# Semantic two-pass field mapping with reasoning log

**Date:** 2026-06-25
**Status:** Design — awaiting review
**Author:** Pierre K (with Claude)

## Problem

Form questions are worded differently on every form, but the company data needed
to answer them is fixed. Today the mapper (`field_mapper.map_fields`) makes a
single LLM call and the gate (`confidence.evaluate_gate`) routes a form to manual
review whenever any answer's self-reported `confidence < 0.8`.

Observed failure mode (confirmed by the user): the model usually picks the
**right** value but reports it as **low-confidence / ambiguous**, so the 0.8 gate
sends correct answers to manual review. The team has been compensating by adding
per-phrasing `aliases` to each profile field — a maintenance treadmill that does
not scale and still leaves ~50% of fields filled manually. Target: <5% manual.

The model has since been upgraded to `gpt-5.4` (config), so reasoning capacity is
available; the design should exploit it instead of hand-fed aliases.

## Goals

- Map varied form wording to the fixed profile **without** per-phrasing aliases.
- Stop routing correct-but-timid answers to manual review (calibrate confidence).
- Keep verbatim values exact (SIREN, IBAN, SIRET) — no free-form generation.
- Emit a **human-readable per-form reasoning log** so the user can see *why* each
  field was filled or flagged.
- One shared mapping path for both the deterministic and the agent pipelines.

## Non-goals

- Replacing the structured profile with a free-form prose dossier (rejected:
  loses verbatim-value guarantees and provenance for legal/financial forms).
- Changing form reading/extraction (`form_reader`) or the agent control loop.
- Multi-language support beyond what `gpt-5.4` already handles (FR/EN).

## Architecture

A new two-pass mapping flow replaces the single `map_fields` call. It runs **once
per form** and is consumed by both pipelines (the deterministic orchestrator and
the agent's `lookup_profile` tool), so they no longer diverge.

```
schema (questions) ─┐
profile (+descriptions) ─┤
                         ▼
  PASS 1 · PROPOSE   gpt-5.4 → per question: profile_field, value, status,
                     confidence, RATIONALE
                         ▼
  choice-snapping    deterministic; dropdown values → exact option (unchanged)
                         ▼
  PASS 2 · VERIFY    gpt-5.4 re-checks each proposal against the profile +
                     question: best field? genuinely ambiguous? would a human
                     disagree? → FINAL status, confidence, VERIFIER RATIONALE,
                     optional corrected field/value
                         ▼
  gate (status-based) → fields_to_fill / route-to-human / blank
                         ▼
  decision log (markdown, ./decisions/<entry_id>.md)
```

The **verifier is the calibration step**: it rescues timid-but-correct matches
(the dominant failure mode) and vetoes confident-but-wrong ones. This is what
moves the manual rate down.

## Components

### 1. Semantic profile (`config.ProfileField`, `profile.yaml`)
- Add optional `description: str = ""` — what the field is, when it applies, and
  disambiguation notes (e.g. "the e-invoicing routing line, NOT the postal
  address").
- `aliases` stays but becomes an *optional* hint, no longer the matching
  mechanism.
- Backward compatible: `load_profile` already does `ProfileField(**f)`; a missing
  `description` defaults to empty.
- Backfill `description` for all current fields in `profile.yaml`.

### 2. Data model (`models.py`)
- `MappedAnswer` gains `rationale: str = ""` (the decisive/final reasoning).
- New `DecisionRecord` (frozen): `question_id, label, type, required,
  profile_field, value, propose_status, propose_confidence, propose_rationale,
  final_status, final_confidence, verify_rationale, final_action`
  (`final_action ∈ {"fill","review","blank"}`).
- New `MappingOutcome` (frozen): `result: MappingResult` (fed to the gate) +
  `decisions: tuple[DecisionRecord, ...]` (fed to the log writer).
- `MappingStatus` vocabulary unchanged (`matched | no_data | ambiguous`):
  verifier `matched` → fill, `ambiguous` → human, `no_data` → blank/flag. Reusing
  it keeps gate churn minimal.

### 3. Mapper (`field_mapper.py`)
- **Pass 1** `_propose(...)`: existing single call, prompt rewritten to reason
  from `description`; `LLMMappedAnswer` gains `rationale`. Instruct the model to
  *commit* when one field clearly fits and only flag `ambiguous` when two+ fields
  genuinely compete or data is missing.
- **choice-snapping**: `_resolve_choice_values` runs on the pass-1 proposals
  (unchanged logic), so the verifier sees snapped dropdown values.
- **Pass 2** `_verify(...)`: new call. Input = schema + profile(+descriptions) +
  pass-1 proposals. Output per question = final status, confidence, verifier
  rationale, optional corrected `profile_field`/`value`. May only use profile
  values (never invents data).
- **Orchestration** `map_and_verify(client, deployment, schema, profile,
  verify=True) -> MappingOutcome`: runs pass 1 → snap → pass 2 → assemble
  `MappingResult` + `DecisionRecord`s.
- Public `map_fields` is kept as a thin wrapper (pass-1 only) for callers/tests
  that want the cheap path; production wiring calls `map_and_verify`.

### 4. Gate (`confidence.py`)
- Remove the `answer.confidence < threshold` → review branch (the over-flagging
  cause). Route purely on discrete status: `matched`+value → fill; `ambiguous` →
  review; `no_data`/missing on a required question → review; optional → blank.
- `evaluate_gate` keeps its `threshold` parameter for signature stability but no
  longer uses it; `confidence_threshold` in config is marked advisory/deprecated.

### 5. Reasoning log (`decision_log.py`, new)
- `write_decisions_md(decisions_dir, entry_id, form_title, form_url, decisions)`:
  one markdown file per form, a section per question showing chosen field, value,
  pass-1 rationale, verifier verdict + rationale, and final action.
- Best-effort: never raises (mirrors `agent/trace.TraceWriter`).

### 6. Config (`config.py`)
- `AppConfig.decisions_dir: str = "./decisions"`.
- `AppConfig.mapping_verify: bool = True` — toggle the second pass (cost escape
  hatch / A-only mode).
- `confidence_threshold` retained but documented as advisory.

### 7. Wiring
- `orchestrator.process_email`: call `map_and_verify`, pass result to the gate,
  write the decisions log (keyed by `email.entry_id`).
- `agent/tools.py` `lookup_profile` + `agent/pipeline.py`: use `map_and_verify`;
  write the decisions log on the agent path too.
- The aggregate Excel row (`result_logger`) is unchanged.

## Data flow (per form)

1. Read schema (existing).
2. `map_and_verify` → `MappingOutcome` (result + decisions).
3. `evaluate_gate(schema, outcome.result, threshold)` → fill / review.
4. `write_decisions_md(...)` (best-effort).
5. Fill + screenshot + Excel row (existing).

## Error handling

- **Pass-2 (verify) LLM failure:** log a warning, fall back to the pass-1
  `MappingResult`, mark each `DecisionRecord.verify_rationale =
  "(verification unavailable)"`. The run continues — never crashes.
- **Pass-1 failure:** unchanged — surfaces as the existing "Read/map error" fail
  row.
- **Decisions-log write failure:** swallowed and warned (best-effort).
- **Verifier proposing a value not in the profile:** ignored; fall back to the
  pass-1 value (guard: only profile values may be filled).

## Testing

- `field_mapper`: mock `client.responses.parse` to return canned pass-1 then
  pass-2 outputs; assert assembly, value/field override, status calibration
  (timid→matched, wrong→ambiguous), and `verify=False` short-circuit.
- Verify-pass-failure → fallback-to-pass-1 test (no crash, decisions marked
  unverified).
- `confidence`: a `matched` low-confidence answer now **fills** (no longer
  routes); `ambiguous` still routes; required `no_data` still routes.
- `decision_log`: renders expected sections; never raises on a bad path.
- `config`: `ProfileField` loads with/without `description`; new `AppConfig`
  fields default correctly.
- Update existing tests that assume confidence-based gating or the old
  `map_fields` shape. Pipeline/orchestrator tests inject a fake mapper hook, so
  they are largely unaffected.

## Backward compatibility / migration

- `description` and the new config fields are optional with safe defaults.
- Existing `profile.yaml` keeps working before descriptions are backfilled
  (the model simply has less to reason from until then).
- `mapping_verify: false` reproduces close to today's single-pass behavior
  (minus the confidence gate), as a fallback.

## Files touched (~8 + tests)

`models.py`, `config.py`, `profile.yaml`, `field_mapper.py`, `confidence.py`,
`decision_log.py` (new), `orchestrator.py`, `agent/tools.py`, `agent/pipeline.py`,
and corresponding tests.

## Risks / open questions

- Two LLM calls per form (≈2× mapping cost). Mitigated: once per form, gpt-5.4,
  and `mapping_verify` toggle. Acceptable for the manual-rate reduction.
- Retiring the float gate removes a safety lever; the verifier's `ambiguous`
  verdict is the new safety net. The dry-run default and human-confirm-before-
  submit remain in place, so a bad fill is still caught before submission.
- Description quality now matters; vague descriptions degrade pass 1. The verify
  pass partially compensates.
