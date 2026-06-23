from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, ContextManager, Optional

from formfiller.agent.models import LoopOutcome
from formfiller.config import AppConfig, ProfileField
from formfiller.link_extractor import NoFormLinkError, extract_form_url
from formfiller.models import EmailMessage, FormSchema, MappingResult
from formfiller.orchestrator import PipelineHooks, process_email
from formfiller.result_logger import JobResult, append_result
from formfiller.review_queue import park_for_review


@dataclass
class AgentDeps:
    """Injected browser/LLM seams so the pipeline is testable without Playwright/Azure.

    open_page() -> context manager yielding a Playwright Page.
    run(page, url, config, profile, trace) -> LoopOutcome  (builds executor+llm, runs loop).
    """
    open_page: Callable[[], ContextManager]
    run: Callable[..., LoopOutcome]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _form_type(url: str) -> str:
    if "google" in url or "forms.gle" in url:
        return "google_forms"
    if "office.com" in url or "microsoft.com" in url:
        return "ms_forms"
    return "other"


def run_agent_pipeline(email: EmailMessage, config: AppConfig,
                       profile: tuple[ProfileField, ...], deps: AgentDeps,
                       det_hooks: Optional[PipelineHooks]) -> JobResult:
    base = dict(timestamp=_now_iso(), sender=email.sender,
                client_name=email.sender.split("@")[-1].split(".")[0]
                if "@" in email.sender else email.sender,
                form_url="", form_type="", overall_confidence=0.0, fields_filled=0,
                fields_blank_flagged="", review_reason="", screenshot_path="")

    def _finish(**overrides) -> JobResult:
        result = JobResult(**{**base, **overrides})
        try:
            written = append_result(config.excel_log_path, result)
            if Path(written) != Path(config.excel_log_path):
                print(f"[warn] log file was locked; wrote to sidecar: {written}")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] could not write log ({exc}); result not logged.")
        return result

    # 1. Find link.
    try:
        url = extract_form_url(email.body_html, email.body_text)
    except NoFormLinkError as exc:
        return _finish(status="fail", review_reason=f"No form link: {exc}")
    base["form_url"] = url
    base["form_type"] = _form_type(url)

    # 2. Run the agent loop.
    from formfiller.agent.trace import TraceWriter
    trace = TraceWriter(config.traces_dir, run_id=email.entry_id)
    try:
        with deps.open_page() as page:
            outcome = deps.run(page=page, url=url, config=config, profile=profile, trace=trace)
    except Exception as exc:  # noqa: BLE001 — isolate one bad form
        outcome = LoopOutcome(status="fail", reason=f"agent error: {exc}")

    # 3. Fallback ladder: abort/fail → deterministic pipeline (which logs its own row).
    if outcome.status in ("abort", "fail"):
        if det_hooks is not None:
            return process_email(email, config, profile, det_hooks)
        return _finish(status="fail",
                       review_reason=f"agent {outcome.status}: {outcome.reason}")

    # 4. Map agent outcome → JobResult (log exactly once).
    if outcome.status == "review":
        schema = outcome.schema or FormSchema(url=url, title="", questions=())
        mapping = outcome.mapping or MappingResult(answers=())
        park_for_review(queue_dir=config.review_queue_dir, job_id=email.entry_id,
                        schema=schema, result=mapping, reason=outcome.reason,
                        screenshot_bytes=outcome.screenshot)
        review_screenshot = (str(Path(config.review_queue_dir) / email.entry_id / "screenshot.png")
                             if outcome.screenshot else "")
        return _finish(status="manual", review_reason=outcome.reason,
                       fields_filled=outcome.fields_filled,
                       screenshot_path=review_screenshot)

    # submitted | dry_run — but never report success if nothing actually filled.
    if outcome.fields_filled == 0:
        return _finish(
            status="fail",
            review_reason="Agent finished but filled 0 fields (the form may not have rendered).",
        )

    preview_path = ""
    if outcome.status == "dry_run" and outcome.screenshot:
        preview = Path(config.excel_log_path).parent / "dry_run_preview" / f"{email.entry_id}.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(outcome.screenshot)
        preview_path = str(preview)

    reason = ("dry-run: filled but not submitted (preview saved — verify before enabling "
              "submission)" if outcome.status == "dry_run" else "")
    return _finish(status="success", fields_filled=outcome.fields_filled,
                   review_reason=reason, screenshot_path=preview_path)
