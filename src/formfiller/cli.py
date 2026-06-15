from __future__ import annotations

from pathlib import Path
from typing import Optional

from formfiller.agent.llm import OpenAIResponsesAgentLLM
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


def choose_pipeline(fill_strategy: str) -> str:
    """Return the pipeline name for a fill_strategy value."""
    return "agent" if fill_strategy == "agent" else "deterministic"


def build_agent_run(*, client, config, profile):
    """Build the AgentDeps.run callable: assemble the executor + LLM and run the loop.

    Returns a function run(page, url, config, profile, trace) -> LoopOutcome.
    """
    from formfiller.agent.loop import run_loop
    from formfiller.agent.system_prompt import SYSTEM_PROMPT
    from formfiller.agent.tools import TOOL_SCHEMAS, ToolExecutor
    from formfiller.field_mapper import map_fields
    from formfiller.form_reader import schema_from_page

    deployment = config.agent_model_deployment or config.azure_openai_deployment

    def run(*, page, url, config, profile, trace):
        page.goto(url, wait_until="load")   # start the agent on the form page
        executor = ToolExecutor(
            page=page, url=url,
            schema_reader=lambda: schema_from_page(page, url),
            mapper=lambda schema: map_fields(client, deployment, schema, profile),
            threshold=config.confidence_threshold, dry_run=config.dry_run,
            confirm=_terminal_confirm,
        )
        llm = OpenAIResponsesAgentLLM(client, deployment=deployment, instructions=SYSTEM_PROMPT)
        return run_loop(llm, executor, instructions=SYSTEM_PROMPT,
                        user_input=f"Complete the form at {url}.",
                        tools=TOOL_SCHEMAS, max_steps=config.max_steps,
                        no_progress_limit=config.no_progress_limit, trace=trace)

    return run


def _terminal_confirm(summary: str) -> bool:
    answer = input(f"\nAgent is ready to SUBMIT (irreversible): {summary}\nProceed? [y/N]: ")
    return answer.strip().lower() in ("y", "yes")


def _build_agent_deps(config, profile):
    """Production AgentDeps: real Playwright page + Azure client."""
    import os
    from openai import OpenAI
    from formfiller.agent.pipeline import AgentDeps
    from formfiller.config import azure_v1_base_url
    from formfiller.form_reader import open_page

    client = OpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
                    base_url=azure_v1_base_url(os.environ["AZURE_OPENAI_ENDPOINT"]),
                    default_query={"api-version": config.azure_api_version})
    run = build_agent_run(client=client, config=config, profile=profile)

    from contextlib import contextmanager

    @contextmanager
    def open_session():
        # The page is navigated to the form url inside `run`; here we just provide
        # a fresh page and tear it down afterward.
        with open_page(headless=True) as page:
            yield page

    return AgentDeps(open_page=open_session, run=run)


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

    if choose_pipeline(config.fill_strategy) == "agent":
        from formfiller.agent.pipeline import run_agent_pipeline
        det_hooks = _build_hooks(config, profile)   # fallback path
        agent_deps = _build_agent_deps(config, profile)
        result = run_agent_pipeline(chosen, config, profile, agent_deps, det_hooks)
    else:
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
