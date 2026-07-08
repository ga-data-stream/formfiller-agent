# Form-filler POC

Tool that reads a form-request email from your Outlook inbox, finds the form
link, fills a Microsoft/Google form from `profile.yaml`, and logs the outcome
to an Excel file. Two ways to run:

- **Interactive** (`formfiller`) — pick one email from a list, process it. Good
  for dev/testing.
- **Batch** (`formfiller-batch`) — process every mail in the `ligne adressage`
  Inbox subfolder in one pass, then sort each into an Outlook subfolder
  `Traité` (submitted) or `Revue humaine` (held for a human / failed). This is
  the mode used in production on a colleague's workstation.

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

## Batch mode
`formfiller-batch` — no prompt. It processes every mail in the
`inbox_subfolder` (default `ligne adressage`), and for each one moves the mail
to an Outlook subfolder:

- `Traité` (`processed_subfolder`) — submitted successfully;
- `Revue humaine` (`review_subfolder`) — held for review or failed.

Idempotent: processed `entry_id`s are recorded in `processed_ids.json` (written
before the move) so a mail is never processed twice, and a lock file
(`.batch.lock`) prevents two runs overlapping. Each run also writes a log to
`logs/batch-<timestamp>.log` with a per-mail line and a summary.

> Auto-submission: in production `dry_run: false` submits forms whose required
> fields all clear `confidence_threshold`. Keep `dry_run: true` until you have
> verified the chain end to end.

## Deploying to another workstation

Production runs **natively on Windows** (the batch drives Outlook via COM and
Playwright via a local browser). The `.devcontainer/` is for developing this
project only — a Linux container has no access to the host's Outlook, so it
**cannot** run the batch.

**Prerequisites on the target machine**
- Windows with **Outlook desktop** installed and a logged-in profile.
- **Python ≥ 3.11** (`winget install Python.Python.3.11` if absent).
- Form-request emails must land in `<Inbox>/ligne adressage` of the account
  Outlook opens by default. If the requests arrive in a shared/generic mailbox,
  set up an Outlook **redirect** rule (not "forward", which rewrites the sender)
  so they are copied into that dedicated subfolder of the user's own mailbox.

**Install (run once, with the dev or IT present)**
1. Clone or copy this repo onto the machine.
2. In PowerShell, from the repo folder: `./install.ps1`
   It creates a virtualenv, installs the package + Chromium, prompts for
   `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` (writes `.env`, never echoed),
   writes `config.yaml` from `config.prod.example.yaml` (backing up any existing
   one), and creates a desktop shortcut **"Formfiller - Traiter les demandes"**.
3. Smoke test: set `dry_run: true` in `config.yaml`, drop a test email with a
   real form link into `ligne adressage`, double-click the shortcut, and check
   the mail moved and the Excel log + `logs/` line appeared. Then set
   `dry_run: false`.

**Daily use** — double-click the desktop shortcut (runs one batch pass and
shows a summary). Update after a `git pull` with `./update.ps1`.

> The scheduled/automatic run (a Windows task repeating the batch) is **not set
> up yet** — launch is manual for now. The full runbook, including the deferred
> scheduled-task setup, is in
> [`docs/deploiement-collegue.md`](docs/deploiement-collegue.md).

## Test
`python -m pytest`
