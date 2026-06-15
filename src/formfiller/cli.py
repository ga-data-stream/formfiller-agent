from __future__ import annotations

from pathlib import Path
from typing import Optional

from formfiller.config import AppConfig, ProfileField, azure_v1_base_url, load_config, load_profile
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

    from openai import OpenAI

    from formfiller.field_mapper import map_fields
    from formfiller.form_filler import fill_form, submit_form, take_screenshot
    from formfiller.form_reader import open_page, prepare_form, schema_from_page

    client = OpenAI(
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        base_url=azure_v1_base_url(os.environ["AZURE_OPENAI_ENDPOINT"]),
        default_query={"api-version": config.azure_api_version},
    )

    def read_form(url: str):
        with open_page(headless=True) as page:
            prepare_form(page, url)
            return schema_from_page(page, url)

    def do_map(schema):
        return map_fields(client, config.azure_openai_deployment, schema, profile)

    def fill_and_submit(url, instructions, dry_run):
        with open_page(headless=True) as page:
            prepare_form(page, url)
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
    if result.screenshot_path:
        print(f"Preview (open to verify the filled form): {result.screenshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
