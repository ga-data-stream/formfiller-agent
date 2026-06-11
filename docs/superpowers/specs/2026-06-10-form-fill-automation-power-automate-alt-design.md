# Email-to-Form Automation — Power Automate Hybrid (Alternative Design)

**Date:** 2026-06-10
**Status:** Alternative design for comparison — not yet approved
**Author:** Pierre Kanzanza
**Companion to:** `2026-06-10-form-fill-automation-design.md` (the pure-Python design)

> This explores doing **most of the workflow in Power Automate** (low-code), keeping
> only the agentic form-reading/filling as custom code. Same problem, scope, data
> profile, and confidence policy as the main spec — only the orchestration changes.

## 1. The constraint that shapes this design

Power Automate **cloud flows cannot drive a browser** to read and fill arbitrary
forms. Power Automate Desktop (RPA) can do UI automation but is brittle, needs an
attended/unattended target machine + premium RPA licensing, and still cannot do the
*reasoning* ("understand this form and map its questions to our data").

**Therefore the agentic core stays custom code** (Playwright + Azure OpenAI). Power Automate
replaces the *glue* around it — trigger, link extraction, logging, human approval,
orchestration — not the agent.

## 2. Architecture

### Power Automate cloud flow owns

| Concern | Connector / mechanism | Premium? |
|---|---|---|
| Trigger | "When a new email arrives in a shared mailbox / folder" (Outlook) | No |
| Link extraction | Regex via flow expressions; optionally AI Builder/AI prompt for messy emails | Regex: No · AI Builder: **Yes (credits)** |
| Call the agent | HTTP action (premium) **or** SharePoint-list job queue (standard) | HTTP: **Yes** · Queue: No |
| Human review | Approvals connector (Teams/email approve-reject) | No |
| Logging | Excel Online / SharePoint "Add a row" | No |
| Orchestration / retry / monitoring | Flow run history; one flow run per email | No |

### Custom agent service owns (the only code)

A small service exposing the form-filling agent. Same internal modules as the main
spec (`form_reader`, `field_mapper`, `form_filler`) plus a thin transport layer.
Two integration shapes depending on licensing (see §3):

- **HTTP endpoint** — `POST /fill` takes `{ form_url, profile_ref }`, returns
  `{ status, overall_confidence, proposed_answers, fields_blank_flagged,
  screenshot_url, needs_review, review_reason }`.
- **Queue poller** — polls a SharePoint "Jobs" list for new rows, processes them,
  writes results back to the row.

## 3. Variant chosen: H1 (fat flow, thin agent)

The agent reads + maps + fills + **submits when confident**, then returns the outcome.
Power Automate logs it. Low-confidence → agent returns `needs_review` → PA fires an
Approval → on approve, a human finishes the form via the live link → PA logs `manual`.

Confidence policy is identical to the main spec (§5 there): threshold 0.8; optional
missing field → leave blank, submit, flag in log; required missing / low-confidence /
unexpected type → review.

### Flow (premium path — direct HTTP)

```
[Trigger] new email in folder
  → [Compose] regex-extract form URL from body
  → [HTTP] POST agent /fill { form_url, profile_ref }
  → [Switch] on response.status
       success → [Excel] add row (success, confidence, blanks…)
       needs_review → [Approvals] start & wait
                         approved → human finishes form → [Excel] add row (manual)
                         rejected → [Excel] add row (skipped)
       fail → [Excel] add row (fail, screenshot link)
```

### Flow (no-premium fallback — SharePoint list as queue)

Avoids the premium HTTP action entirely. Integration boundary becomes a SharePoint
list; only standard connectors used.

```
[Trigger] new email
  → [Compose] regex-extract URL
  → [SharePoint] create item in "FormJobs" { url, profile_ref, status=queued }
  → (agent service polls FormJobs, processes, sets status + result fields)
  → [SharePoint] "When an item is modified" (or scheduled check)
       → [Switch] same branching as above (Approvals + Excel logging)
```

The agent service runs a poll loop against the FormJobs list (standard SharePoint
access via its own app credentials), so PA never needs to reach into the VM.

## 4. Hosting & networking

The agent service still needs an always-on home reachable by the integration shape:

- **HTTP path:** service needs an HTTPS endpoint PA cloud can reach. On the POC
  machine that means an **on-prem data gateway** or a tunnel; in production, host in
  **Azure (Container App / Function with Playwright)**.
- **Queue path:** service only needs **outbound** access to SharePoint — no inbound
  endpoint, no gateway. Easier for a machine-hosted POC. (Another reason the
  no-premium fallback is attractive for the initial POC.)

## 5. Premium dependency checklist (resolve before committing)

- [ ] **HTTP / custom connector action** — premium. If unavailable → use the
      SharePoint-list queue fallback.
- [ ] **AI Builder / AI prompt** for link extraction — consumes credits. If
      unavailable → regex expressions only (sufficient for forms.office.com /
      forms.gle / docs.google.com/forms patterns).
- [ ] **Approvals connector** — included in standard/seeded plans; confirm enabled.
- [ ] **Power Automate per-user/per-flow plan** for the mailbox account.
- [ ] **Shared mailbox / folder** access for the trigger account.

## 6. Tradeoffs vs. the pure-Python design

**Wins**
- No custom mail auth/polling — native Outlook trigger (real webhook).
- Native, concurrency-safe Excel/SharePoint logging — removes the synced-folder hack.
- Native Approvals — removes the custom review-queue folder; reviewers act from Teams/email.
- Cloud orchestration, per-email isolation, run history/monitoring for free.
- Visual flow a non-developer can maintain.

**Costs**
- Premium licensing exposure (HTTP, AI Builder) — the main open question.
- Still must host the agent service somewhere; HTTP path may need an on-prem gateway.
- Two systems to maintain (flow + service) instead of one codebase.
- Flow logic is harder to unit-test than Python modules.
- Power Platform lock-in.

**Net:** for the *initial* POC this is arguably more setup than pure Python (licensing
+ hosting + possibly a gateway). As a *production* architecture in an M365-governed
org, the native trigger/logging/approvals are a strong fit. The no-premium queue
fallback narrows the setup gap considerably.

## 7. Recommendation

If licensing turns out to be available: H1 over the HTTP path is clean and minimal.
If licensing is constrained: H1 over the **SharePoint-list queue** path keeps
everything on standard connectors and needs no inbound networking — a good POC default.
Either way, the agentic service (`form_reader` + `field_mapper` + `form_filler`) is
identical to the main spec and can be built once and reused under either orchestrator.

## 8. What stays identical to the main spec

- The data profile (`profile.yaml`) and alias-based matching.
- `FormSchema` extraction via Playwright; field mapping via Azure OpenAI structured output.
- The confidence gate and optional-blank / required-review policy.
- Dry-run mode and the per-module testing approach for the agent core.
