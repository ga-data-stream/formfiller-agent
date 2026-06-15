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
