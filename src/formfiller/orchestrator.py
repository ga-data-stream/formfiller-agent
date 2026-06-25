from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from formfiller.config import AppConfig, ProfileField
from formfiller.confidence import FillInstruction, evaluate_gate
from formfiller.link_extractor import NoFormLinkError, extract_form_url
from formfiller.models import EmailMessage, FormSchema, MappingResult, MappingOutcome
from formfiller.decision_log import write_decisions_md
from formfiller.result_logger import JobResult, append_result
from formfiller.review_queue import park_for_review


@dataclass
class PipelineHooks:
    """Injected steps that touch external systems, so the orchestrator stays
    testable. In production these wrap Playwright and Azure OpenAI (see cli.py)."""
    read_form: Callable[[str], FormSchema]
    map_fields: Callable[[FormSchema], MappingOutcome]
    # returns (screenshot_bytes, submitted?, fields_actually_filled)
    fill_and_submit: Callable[[str, tuple[FillInstruction, ...], bool], tuple[bytes, bool, int]]


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
        try:
            written = append_result(config.excel_log_path, result)
            from pathlib import Path as _Path
            if _Path(written) != _Path(config.excel_log_path):
                print(f"[warn] log file was locked; wrote to sidecar: {written}")
        except Exception as exc:  # noqa: BLE001 — logging must never crash the run
            print(f"[warn] could not write log ({exc}); result not logged.")
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
        outcome = hooks.map_fields(schema)
    except Exception as exc:  # noqa: BLE001 — isolate one bad form
        return _finish(status="fail", review_reason=f"Read/map error: {exc}")

    mapping = outcome.result
    write_decisions_md(config.decisions_dir, email.entry_id, schema.title, url,
                       outcome.decisions)
    base["overall_confidence"] = _overall_confidence(mapping)

    # The form yielded no questions (e.g. it never rendered): there is nothing to
    # fill, so this is a failure — never a silent success.
    if not schema.questions:
        return _finish(
            status="fail",
            review_reason="No questions detected on the form (it may not have rendered).",
        )

    # 4. Gate.
    decision = evaluate_gate(schema, mapping, config.confidence_threshold)

    if decision.action == "review":
        try:
            screenshot, _, _ = hooks.fill_and_submit(url, decision.fields_to_fill, True)
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
        screenshot_bytes, submitted, filled_count = hooks.fill_and_submit(
            url, decision.fields_to_fill, config.dry_run
        )
    except Exception as exc:  # noqa: BLE001
        return _finish(status="fail", review_reason=f"Fill/submit error: {exc}")

    preview_path = ""
    if config.dry_run and screenshot_bytes:
        preview = _dry_run_preview_path(config, email.entry_id)
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(screenshot_bytes)
        preview_path = str(preview)

    intended = len(decision.fields_to_fill)
    blank = ",".join(decision.fields_blank_flagged)

    # Truthful accounting: success requires fields to have ACTUALLY landed on the
    # page. Reporting success while nothing was filled is the bug this guards.
    if filled_count == 0:
        return _finish(
            status="fail",
            fields_filled=0,
            fields_blank_flagged=blank,
            review_reason=(
                f"No fields were filled on the form (0 of {intended} landed); the form "
                "may not have rendered or its fields did not match the mapped answers."
            ),
            screenshot_path=preview_path,
        )

    status = "success" if (submitted or config.dry_run) else "fail"
    if config.dry_run:
        reason = "dry-run: filled but not submitted (preview saved — verify before enabling submission)"
    else:
        reason = ""
    if filled_count < intended:
        note = f"partial fill: only {filled_count} of {intended} fields landed."
        reason = f"{reason} {note}".strip()
    return _finish(
        status=status,
        fields_filled=filled_count,
        fields_blank_flagged=blank,
        review_reason=reason,
        screenshot_path=preview_path,
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


def _dry_run_preview_path(config: AppConfig, job_id: str):
    from pathlib import Path
    return Path(config.excel_log_path).parent / "dry_run_preview" / f"{job_id}.png"
