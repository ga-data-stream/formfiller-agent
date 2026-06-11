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
