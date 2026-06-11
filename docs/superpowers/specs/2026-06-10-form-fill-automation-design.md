# Email-to-Form Automation — Design Spec

**Date:** 2026-06-10
**Status:** Approved design, ready for implementation plan
**Author:** Pierre Kanzanza

## 1. Problem

Clients and suppliers email requests to fill out web forms (mostly Microsoft Forms
and Google Forms, occasionally a custom form). Every form asks for the same
underlying information about our company, just worded and ordered differently.
Today this is done by hand. We want a service that:

1. Detects an incoming form-fill request.
2. Finds the form link in the email.
3. Opens the form, reads it, and maps its questions to our known data.
4. Fills the form and submits it when confident; routes to a human when not.
5. Logs every outcome (success / manual / fail) to an Excel file on SharePoint.

## 2. Scope & constraints

- **Volume:** Medium, ~1–10 requests/day. Triggered/unattended; no heavy queuing
  or parallelism needed.
- **Form types:** Mostly MS Forms and Google Forms. Custom forms rare. We assume
  clean, DOM-accessible forms.
- **Autonomy:** Auto-submit when field-mapping confidence is high; route to human
  review when uncertain.
- **Runtime:** Custom Python CLI run on Pierre's own logged-in Windows machine.
- **Launch model:** Manual. The user runs the tool, it lists recent emails from the
  live Outlook inbox, and the user **picks the email to process**. No scheduling, no
  unattended polling.
- **Data:** A single fixed data profile (same data across all forms).

### Non-goals (YAGNI)

- No vision/computer-use form filling in the POC (deferred fallback — see §8).
- No multi-tenant or multi-profile support; one company profile.
- No parallel processing; one email at a time, chosen by the user.
- No scheduler, no unattended polling, no webhooks — manual launch only.
- No automatic "is this a form request?" detection — the user picks the email.

## 3. Key architectural decisions

| Decision | Choice | Rationale |
|---|---|---|
| Form-driving engine | **DOM extraction + LLM field-mapping** (Approach A) | MS/Google Forms have clean, predictable DOM. Fast, cheap, deterministic, debuggable. Vision deferred as a later fallback. |
| Email ingestion (POC) | **Live Outlook inbox via COM** (`pywin32`) | Pierre is logged in on the machine; reading the inbox directly gives the natural "pick the email" UX with no export step or auth setup. |
| Excel logging (POC) | **`openpyxl` on the synced OneDrive/SharePoint copy** | Zero auth; the sync client pushes changes up. Graph Excel API is a later swap-in. |
| Submit gate | Per-form confidence gate (see §5) | Submitting wrong data to external parties is consequential. |
| Launch | **Manual CLI**, user selects the email | No scheduler needed for the POC. |

**Ingestion rationale in full:** Because the POC runs interactively on Pierre's
logged-in Windows machine, the tool reads the live Outlook inbox through the
desktop COM interface (`win32com.client`) — no Graph app registration, no IMAP, no
file export. `email_source` is kept behind a small interface so a `GraphMailboxWatcher`
adapter (for an unattended, server-hosted version) can replace it later without
touching the rest of the pipeline.

## 4. Components & data flow

Each module has one responsibility, communicates via plain pydantic objects, and is
independently testable with fixtures.

| Module | Responsibility | Depends on |
|---|---|---|
| `email_source` | List recent Outlook inbox messages; return the one the user selects | `pywin32` (Outlook COM) |
| `cli` | Show the message list, take the user's pick, drive the run, print outcome | `email_source`, `orchestrator` |
| `link_extractor` | Pull candidate URLs from email body/HTML; pick the form link | (LLM optional) |
| `form_reader` | Open URL in Playwright, scrape questions/inputs → `FormSchema` | Playwright |
| `field_mapper` | Map each `FormSchema` question → data profile; return value + confidence | Azure OpenAI, profile |
| `form_filler` | Type/select answers in Playwright; submit or hold per confidence gate | Playwright |
| `result_logger` | Append outcome row to the synced Excel file | openpyxl (later: Graph) |
| `review_queue` | Persist low-confidence/failed jobs (screenshot + state) for a human | local folder |
| `orchestrator` | Wire the pipeline for the chosen email, handle retries, structured logging | all above |

**Data flow for one run:**

```
cli → email_source.list_recent() → user picks a message
  → link_extractor → form URL
    → form_reader → FormSchema { questions[], types, options, required }
      → field_mapper → answers[] + per-field confidence + overall score
        → if gate passes → form_filler.submit()
           else          → review_queue.park(screenshot, prefilled state)
        → result_logger.append(status row)
        → cli prints the outcome
```

## 5. Field mapping & confidence (the core)

**Data profile** — a single structured config (`profile.yaml`): canonical fields
(`company_legal_name`, `vat_number`, `iban`, `contact_email`, `address_*`, …), each
with its value plus a few aliases/synonyms ("VAT", "Tax ID", "N° TVA", "numéro de
TVA") to aid matching and reduce LLM cost.

**Mapping step** — `form_reader` produces a `FormSchema` (questions with type:
text/choice/date/email, options, required flag). The LLM (Azure OpenAI) receives the
schema + profile and returns, per question: chosen field, value to enter, a **0–1
confidence**, and a flag for "no matching data" or "ambiguous".

**Confidence gate (per form):**

- **Submit** only if every **required** field is filled AND the minimum per-field
  confidence ≥ threshold (default **0.8**, tunable).
- **Optional field with no matching data** → leave blank, submit anyway, and flag it
  in the log (`fields_blank_flagged`).
- **Required field missing/unmatched, any low-confidence field, or an unexpected
  question type** → hold the whole form for review rather than guessing.

**Review queue** — a local folder holding, per parked form: a screenshot of the
filled-but-unsubmitted form, the `FormSchema`, the proposed answers + confidences,
and the hold reason. A human opens the live link, finishes it, and the logger records
it as `manual`.

## 6. Reliability

- **Idempotency / no double-submit:** the Outlook message EntryID of each processed
  email is recorded; if the user picks one already submitted, the tool warns before
  proceeding. A crash mid-run never silently resubmits.
- **Isolation:** the run is wrapped in try/except — a broken form logs `fail` +
  screenshot and exits cleanly rather than crashing.
- **Retries:** transient failures (page load, network) retried with backoff;
  persistent failure → `fail` + parked for review.
- **Dry-run mode:** global flag that fills but never submits — for safe end-to-end
  testing against real forms.
- **Observability:** structured per-job logs (email → URL → decision → outcome) plus
  the Excel row as the human-facing record.

## 7. Tech stack

- **Python** CLI app run interactively on the Windows machine.
- **pywin32** (`win32com.client`) — read the live Outlook inbox, list/select messages.
- **Playwright** (Chromium) — form reading/filling, screenshots, headed/headless.
- **Azure OpenAI** (`openai.AzureOpenAI`) — field mapping via structured output
  (`.beta.chat.completions.parse` with a Pydantic `response_format`). The LLM client
  is injected behind the mapper so the model/provider can be swapped without touching
  the pipeline.
- **pydantic** — data objects (`FormSchema`, `MappedAnswer`, `JobResult`).
- **openpyxl** — synced-folder Excel log; **PyYAML** — `profile.yaml`, `config.yaml`.
- **Launch:** run manually from the terminal (e.g. `python -m formfiller`); no scheduler.
- **Secrets:** `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT` in a gitignored
  `.env`; deployment name and API version in `config.yaml`. No other secrets in the POC.

**Excel log columns:** `timestamp, sender, client_name, form_url, form_type,
status (success|manual|fail), overall_confidence, fields_filled,
fields_blank_flagged, review_reason, screenshot_path`.

## 8. Production upgrade path (post-POC)

These are designed-for but deliberately out of POC scope:

- **Unattended trigger** — replace manual selection + COM with a `GraphMailboxWatcher`
  (Entra app registration, `Mail.Read`) polling a dedicated mailbox/folder on a
  schedule, behind the same `email_source` interface.
- **Graph Excel logging** — `result_logger` swapped to the Excel REST API for
  concurrency-safe appends.
- **Vision fallback (Approach C)** — when the DOM scraper finds no usable fields or
  confidence is low, escalate that single form to a vision-capable model (e.g. a
  GPT-4o vision deployment) that reads a screenshot.
- **SharePoint-hosted review queue** — so others can action held forms without the VM.

## 9. Testing

- Unit tests per module with fixtures: mocked Outlook messages (the COM layer behind
  a small interface so it can be faked), captured `FormSchema`s from real MS/Google
  forms, mocked LLM responses.
- Dry-run end-to-end against a couple of test forms we control.
- Confidence-gate logic tested against synthetic schemas (required-missing,
  optional-missing, low-confidence, unexpected type).
