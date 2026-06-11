from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from formfiller.config import AppConfig, ProfileField
from formfiller.confidence import FillInstruction, evaluate_gate
from formfiller.link_extractor import NoFormLinkError, extract_form_url
from formfiller.models import EmailMessage, FormSchema, MappingResult
from formfiller.result_logger import JobResult, append_result
from formfiller.review_queue import park_for_review


@dataclass
class PipelineHooks:
    """Injected steps that touch external systems, so the orchestrator stays
    testable. In production these wrap Playwright and Azure OpenAI (see cli.py)."""
    read_form: Callable[[str], FormSchema]
    map_fields: Callable[[FormSchema], MappingResult]
    # returns (screenshot_bytes, submitted?)
    fill_and_submit: Callable[[str, tuple[FillInstruction, ...], bool], tuple[bytes, bool]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _overall_confidence(result: MappingResult) -> float:
    confs = [a.confidence for a in result.answers if a.status == "matched"]
    return round(min(confs), 3) if confs else 0.0


def process_email(
    email: EmailMessage,
    config: AppConfig,
    profile: tuple[ProfileField, ...],
    hooks: PipelineHooks,
) -> JobResult:
    """Run the full pipeline for one chosen email and return the logged result.

    Always appends exactly one row to the Excel log. Never raises for expected
    failures (no link, browser error) — those become status='fail' rows.
    """
    base = dict(
        timestamp=_now_iso(),
        sender=email.sender,
        client_name=email.sender.split("@")[-1].split(".")[0] if "@" in email.sender else email.sender,
        form_url="",
        form_type="",
        overall_confidence=0.0,
        fields_filled=0,
        fields_blank_flagged="",
        review_reason="",
        screenshot_path="",
    )

    def _finish(**overrides) -> JobResult:
        result = JobResult(**{**base, **overrides})
        append_result(config.excel_log_path, result)
        return result

    # 1. Find the form link.
    try:
        url = extract_form_url(email.body_html, email.body_text)
    except NoFormLinkError as exc:
        return _finish(status="fail", review_reason=f"No form link: {exc}")

    base["form_url"] = url
    base["form_type"] = _form_type(url)

    # 2. Read + 3. Map (wrapped so any browser/LLM error becomes a fail row).
    try:
        schema = hooks.read_form(url)
        mapping = hooks.map_fields(schema)
    except Exception as exc:  # noqa: BLE001 — isolate one bad form
        return _finish(status="fail", review_reason=f"Read/map error: {exc}")

    base["overall_confidence"] = _overall_confidence(mapping)

    # 4. Gate.
    decision = evaluate_gate(schema, mapping, config.confidence_threshold)

    if decision.action == "review":
        try:
            screenshot, _ = hooks.fill_and_submit(url, decision.fields_to_fill, True)
        except Exception:  # noqa: BLE001
            screenshot = None
        park_for_review(
            queue_dir=config.review_queue_dir,
            job_id=email.entry_id,
            schema=schema,
            result=mapping,
            reason=decision.reason,
            screenshot_bytes=screenshot,
        )
        return _finish(
            status="manual",
            review_reason=decision.reason,
            fields_blank_flagged=",".join(decision.fields_blank_flagged),
            screenshot_path=str(_job_screenshot_path(config, email.entry_id)),
        )

    # 5. Submit (respecting dry_run).
    try:
        _screenshot, submitted = hooks.fill_and_submit(
            url, decision.fields_to_fill, config.dry_run
        )
    except Exception as exc:  # noqa: BLE001
        return _finish(status="fail", review_reason=f"Fill/submit error: {exc}")

    status = "success" if (submitted or config.dry_run) else "fail"
    reason = "dry-run: filled but not submitted" if config.dry_run else ""
    return _finish(
        status=status,
        fields_filled=len(decision.fields_to_fill),
        fields_blank_flagged=",".join(decision.fields_blank_flagged),
        review_reason=reason,
    )


def _form_type(url: str) -> str:
    if "google" in url or "forms.gle" in url:
        return "google_forms"
    if "office.com" in url or "microsoft.com" in url:
        return "ms_forms"
    return "other"


def _job_screenshot_path(config: AppConfig, job_id: str):
    from pathlib import Path
    return Path(config.review_queue_dir) / job_id / "screenshot.png"
