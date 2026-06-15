# Design — Agentic Fill Stage (MVP)

**Date:** 2026-06-15
**Status:** Approved (brainstorming) → ready for writing-plans
**Scope:** Restructure *only* the fill stage of the `formfiller` POC into an adaptive
agent loop. Additive — the existing deterministic pipeline stays intact and remains the default.

## Background

The POC is a manually-launched CLI: pick an Outlook email → extract the form link → open it
with Playwright → read the questions → map them to `profile.yaml` via **one** Azure OpenAI call
→ a deterministic confidence gate decides submit vs human-review → fill + submit → log to Excel.
It submitted a real Microsoft Form on 2026-06-15. The LLM is **Azure AI Foundry (`gpt-5.4-nano`)**
via the `openai.OpenAI` client pointed at `<endpoint>/openai/v1/` + the **Responses API**
(`responses.parse` / `responses.create`), **not** Anthropic.

Today the LLM is called exactly once, on already-extracted question labels. It never perceives the
live page and never acts. A fixed pipeline can't handle the surprises that occur after opening a
link: login/redirect walls, cookie/consent banners, captchas, multi-page forms, conditional/dynamic
questions, validation errors, unusual layouts, zero-question pages. **Removing that limitation is the
MVP goal.**

## Goal

Replace the fill stage (not the whole app) with an agent loop:

> observe page state → reason → act via tools → observe result → repeat until the form is
> complete → gate → submit.

The agent adapts on the fly to whatever the page does after the link is opened.

## Non-negotiables

- Keep the existing deterministic pipeline working as both a **fallback** and a set of **tools**.
- Don't break the **59 passing tests**.
- Stay on **Azure AI Foundry / the v1 Responses API**.
- Submit is **irreversible and outward-facing** — gate it hard.
- Log every step (thought / action / observation) for debugging and eval.

## Decisions (from brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Perception | **A11y/DOM text snapshot** with stable `ref` ids (text-first). On-demand vision is a later additive tool, **not** in this build. |
| 2 | Loop control | **Native function-calling loop** on the Azure v1 Responses API. |
| 3 | Tool granularity | **Layered** — primitives + power tools that wrap the existing deterministic modules. |
| 4 | Submit safety | **Guarded `submit` tool** — agent initiates, but a harness guard runs the gate + `dry_run` + human-confirm and can veto. The irreversible click is impossible without all three. |
| 5 | Coexistence | **Config-selectable**, deterministic stays the default. Agent path reuses deterministic logic as tools + falls back to it. |
| 6 | Model | **Start on `gpt-5.4-nano`**, model-agnostic; loop deployment name is a config knob; upgrade empirically if traces show drift/looping. |
| 7 | Eval / observability | **Layered** — always-on JSONL step traces; fast deterministic pytest (fake browser/LLM + DOM fixtures) for plumbing; live dry-run + human trace review for the LLM loop. |

## Architecture

```
email_source → link_extractor → open page (Playwright)        [unchanged pre-steps]
                                        │
                       fill_strategy ∈ {deterministic | agent}
                                        │
   deterministic ─────────────────────┐│┌──────────────────── agent
   (today's pipeline, unchanged)       ││
                                        ▼▼
            ┌──────────── AGENT LOOP (gpt-5.4-nano · function-calling) ───────────┐
            │  read_snapshot → reason → act (tool) → observe → repeat              │
            │                                                                       │
            │  POWER TOOLS            PRIMITIVES            CONTROL / SAFETY         │
            │  extract_questions      click                 detect_blocker          │
            │   → form_reader         fill                  request_human           │
            │  lookup_profile         select_choice         submit (guarded)        │
            │   → field_mapper        scroll                finish                  │
            │  answer_question        navigate_next                                 │
            │   → choices+filler      goto                                          │
            │                                                                       │
            │  guards: max_steps (20) · no-progress breaker (5) · allowlist · trace │
            └───────────────────────────────────────────────────────────────────┘
                                        │  submit(...) / finish(...)
                                        ▼
   submit guard → confidence gate → dry_run? → human confirm → ✅ submit_form
        │
        └─ blocker / gate=review / low-confidence / budget blown → review_queue
                                        │
                            all paths → result_logger (Excel)
```

Green/new vs grey/existing: the loop subsystem and config branch are new; everything before the
branch and after the guard is existing code, reused unchanged.

### Boundary

`orchestrator.py` reads `fill_strategy` from `config.yaml`:
- `deterministic` (default) → today's pipeline, byte-for-byte unchanged → 59 tests stay green.
- `agent` → the loop runs on the already-opened page.

The loop **begins at "page is open"** (so it owns login/consent/captcha that appear before any
form) and **ends** when the agent calls `submit`/`finish`, hits the step budget, or a guard aborts.
Control then returns to the existing gate → submit path.

## Loop control & perception

- **Driver:** native function-calling on the Azure v1 Responses API. Each turn sends the
  conversation (`previous_response_id` to carry reasoning context) + the tool schema; the model
  emits one or more tool calls; the harness executes each, appends the structured result as the
  observation; repeat. The loop ends on `submit`/`finish`, step budget, or a guard abort.
- **Perception:** the agent never sees raw HTML. `read_snapshot()` returns a compact
  accessibility/DOM snapshot — a flattened list of interactive elements (role, accessible name,
  value/state, stable `ref` id), plus page URL/title and a short blocker hint. The model acts by
  `ref`, never by coordinates.
- **Model:** `gpt-5.4-nano` to start; deployment name is `config.yaml: agent_model_deployment`, so
  swapping to a stronger GPT-5 reasoning model is one line. The single-shot `lookup_profile` mapping
  tool keeps using nano through the existing `field_mapper` path.
- **Why function-calling over hand-rolled ReAct:** the reasoning model is trained for it, handles
  multi-step and parallel calls natively, and observability is preserved because the harness wraps
  and logs every tool execution.

## Tool set (the action allowlist)

The agent may call only these tools; the schema list **is** the allowlist. Unknown/disallowed names
are rejected with an observation, never crash the run. The agent is instructed to prefer power tools
(cheap, deterministic) and drop to primitives only when a page resists them.

**Perception**
- `read_snapshot()` → flattened interactive-element list with `ref`s, URL/title, blocker hint.

**Power tools** (deterministic fast path — wrap existing modules, behavior preserved)
- `extract_questions()` → `form_reader` MS Forms extraction; structured questions with their `ref`s.
- `lookup_profile(question_text, options?)` → `field_mapper` (Azure) → proposed value + confidence;
  if `options` provided, runs `choices.match_choice` to snap to an exact option.
- `answer_question(ref, value)` → fills text/textarea or selects the matching radio via
  `choices` + `form_filler` logic, in one call.

**Primitives** (fallback for surprises)
- `click(ref)`, `fill(ref, text)`, `select_choice(ref, option)`, `scroll(direction)`,
  `navigate_next()`, `goto(url)`.

**Control / safety**
- `detect_blocker()` → explicit DOM heuristic check for login wall / consent banner / captcha
  (also surfaced passively in every snapshot's blocker hint).
- `request_human(reason)` → route to `review_queue` and end the loop.
- `submit(summary)` → **request** submission; goes through the guard; can be vetoed.
- `finish(ready_to_submit, summary)` → end the loop without submitting.

## Submit guard & safety rails

When the agent calls `submit(summary)` it does **not** click. The guard runs this sequence and can
veto at any step:

1. **Gate check** — run the existing `confidence.py` gate on the current filled-form state. Gate
   says *review* → submit **refused**; observation: "routed to human review: <reason>". Agent then
   `finish`/stops.
2. **`dry_run` check** — if `dry_run: true` (default), no click. Saves the filled-form preview
   screenshot (as today); observation: "dry-run: would have submitted". Normal MVP-testing state.
3. **Human confirmation** — only reached when gate=submit **and** `dry_run: false`. Requires an
   explicit human "yes" (terminal confirm) before the one real click via `form_filler.submit_form`.
   No human yes → refused → review.

**Hard rails enforced by the harness, not the model:**
- **Step budget** — `max_steps` (default **20**, configurable). Exceeded → abort → `review_queue`.
- **No-progress breaker** — N consecutive steps with no DOM change or a repeated identical action
  (default **5**) → abort → `review_queue`.
- **Action allowlist** — only the tools above; anything else rejected.
- **Blocker → human** — login/captcha detected → `request_human`; never attempt to solve a captcha.
- **Trace** — every step (reasoning, tool, args, observation, tokens, latency, guard decisions) →
  JSONL.

The irreversible click is impossible without gate approval **and** non-dry-run **and** a human yes,
even though `submit` is a tool the agent initiates.

## Coexistence & fallback

- `fill_strategy: deterministic | agent` in `config.yaml`, default `deterministic`;
  `orchestrator.py` branches on it.
- **Fallback ladder** (when `agent`): loop aborts (budget / no-progress / error) → try the
  deterministic fill once on the same page → if that can't satisfy the gate → `review_queue`. A
  failed experiment degrades to today's proven behavior, never to a bad submit.
- The deterministic pipeline keeps its own entry point and tests; the agent path reuses its
  internals as tools. Nothing in the old path is modified.

## Observability & eval

- **Trace:** one JSONL file per run under `traces/` (run id, timestamp, each step's
  reasoning/tool/args/observation/tokens/latency, guard decisions, final outcome). Always on.
- **Deterministic tests (pytest, no network/browser):** snapshot parser, blocker detection, each
  power-tool wrapper, the submit guard's gate/dry_run/veto logic, the fallback ladder — driven by
  recorded DOM fixtures + a fake browser + a fake LLM. Grows the existing 59-test suite; stays fast
  and deterministic.
- **LLM-loop eval:** run in `dry_run` against a small live corpus (the MS Forms test form + a couple
  of public forms with consent/login/multi-page); review trace + preview screenshot by hand. This is
  where "is nano good enough?" is answered empirically.

## Module / file plan (additive)

**New — `src/formfiller/agent/` subpackage** (cohesive isolation; rest of the app stays flat):
- `perception.py` — `read_snapshot()`: a11y/DOM element list with `ref`s, URL/title, blocker hint.
- `blockers.py` — login / consent / captcha detection heuristics.
- `tools.py` — tool JSON schemas + implementations: power tools (wrap
  `form_reader`/`field_mapper`/`choices`/`form_filler`), primitives (over Playwright),
  control/safety, and the submit guard.
- `loop.py` — function-calling driver: Responses API turns, step budget, no-progress breaker,
  fallback ladder, outcome.
- `trace.py` — JSONL step-trace writer.
- `models.py` — Pydantic models for snapshot elements, tool results, trace records.

**Changed (minimal, behavior-preserving):**
- `config.py` / `config.yaml` — new keys: `fill_strategy` (default `deterministic`),
  `agent_model_deployment`, `max_steps: 20`, `no_progress_limit: 5`, `traces_dir`.
- `orchestrator.py` — branch on `fill_strategy`; wire the fallback ladder.
- Where a reusable bit of `form_reader`/`field_mapper`/`choices`/`form_filler` isn't cleanly
  callable, extract a thin function **without** changing behavior (existing tests must stay green).

**Reused untouched:** `email_source`, `link_extractor`, `confidence`, `form_filler.submit_form`,
`choices.match_choice`, `review_queue`, `result_logger`.

## Build sequencing

TDD throughout, via writing-plans → subagent-driven-development. Each unit is independently testable
with a fake browser/LLM + DOM fixtures:

1. `models` + `trace`
2. `perception` + `blockers`
3. `tools` (power + primitives)
4. submit **guard**
5. `loop` driver
6. `orchestrator` / config wiring + fallback ladder
7. eval corpus + dry-run review

## Out of scope (YAGNI for this build)

- On-demand `screenshot()` / vision perception (later additive tool).
- Deploying a stronger model (only if traces prove nano insufficient).
- Solving captchas (always hand off to human).
- Changing anything before the page-open step or after the submit guard.
- A full offline replay harness (deterministic tests use fixtures + fakes, not a replay engine).
