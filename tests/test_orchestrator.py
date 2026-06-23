import pytest
from formfiller.config import AppConfig, ProfileField
from formfiller.models import (
    QuestionType, FormQuestion, FormSchema, EmailMessage, MappedAnswer, MappingResult,
)
from formfiller.orchestrator import process_email, PipelineHooks


def _email(body):
    return EmailMessage(
        entry_id="E1", sender="client@acme.com", subject="Fill this",
        received="2026-06-10T09:00:00", body_text=body, body_html="",
    )


def _config(tmp_path, dry_run=False):
    return AppConfig(
        confidence_threshold=0.8,
        dry_run=dry_run,
        excel_log_path=str(tmp_path / "log.xlsx"),
        review_queue_dir=str(tmp_path / "queue"),
        inbox_list_count=10,
        azure_openai_deployment="gpt-4o",
        azure_api_version="2024-10-21",
    )


_PROFILE = (ProfileField(name="company_legal_name", value="Ginesis Finance SAS", aliases=()),)

_SCHEMA = FormSchema(
    url="https://forms.office.com/r/x", title="Vendor",
    questions=(FormQuestion(id="q1", label="Company name", type=QuestionType.TEXT, required=True),),
)


def test_high_confidence_logs_success(tmp_path):
    mapping = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="company_legal_name",
                     value="Ginesis Finance SAS", confidence=0.95, status="matched"),
    ))
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: mapping,
        fill_and_submit=lambda url, instr, dry_run: (b"\x89PNG", (not dry_run) and len(instr) > 0, len(instr)),
    )
    result = process_email(_email("link https://forms.office.com/r/x"),
                           _config(tmp_path), _PROFILE, hooks)
    assert result.status == "success"
    assert result.fields_filled == 1


def test_low_confidence_parks_for_review_and_logs_manual(tmp_path):
    mapping = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="company_legal_name",
                     value="Ginesis Finance SAS", confidence=0.4, status="matched"),
    ))
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: mapping,
        fill_and_submit=lambda url, instr, dry_run: (b"\x89PNG", False, len(instr)),
    )
    cfg = _config(tmp_path)
    result = process_email(_email("link https://forms.office.com/r/x"), cfg, _PROFILE, hooks)
    assert result.status == "manual"
    assert "confidence" in result.review_reason.lower()
    # a review-queue folder was created (keyed on the email entry_id)
    assert (tmp_path / "queue" / "E1").exists()


def test_no_form_link_logs_fail(tmp_path):
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: MappingResult(answers=()),
        fill_and_submit=lambda url, instr, dry_run: (b"", False, 0),
    )
    result = process_email(_email("no link here"), _config(tmp_path), _PROFILE, hooks)
    assert result.status == "fail"
    assert "link" in result.review_reason.lower()


def test_dry_run_saves_filled_form_preview(tmp_path):
    mapping = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="company_legal_name",
                     value="Ginesis Finance SAS", confidence=0.95, status="matched"),
    ))
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: mapping,
        fill_and_submit=lambda url, instr, dry_run: (b"\x89PNG", False, len(instr)),
    )
    cfg = _config(tmp_path, dry_run=True)
    result = process_email(_email("link https://forms.office.com/r/x"), cfg, _PROFILE, hooks)
    assert result.status == "success"
    assert result.screenshot_path  # non-empty
    preview = tmp_path / "dry_run_preview" / "E1.png"
    assert preview.exists()
    assert preview.read_bytes() == b"\x89PNG"
    assert str(preview) == result.screenshot_path


def test_zero_fields_filled_is_not_reported_success(tmp_path):
    # Regression: a dry-run where the mapping matched a field but NOTHING actually
    # landed on the page (selectors missed) must NOT be reported as success.
    mapping = MappingResult(answers=(
        MappedAnswer(question_id="q1", profile_field="company_legal_name",
                     value="Ginesis Finance SAS", confidence=0.95, status="matched"),
    ))
    hooks = PipelineHooks(
        read_form=lambda url: _SCHEMA,
        map_fields=lambda schema: mapping,
        # gate proposes 1 fill, but 0 actually landed on the page
        fill_and_submit=lambda url, instr, dry_run: (b"\x89PNG", False, 0),
    )
    cfg = _config(tmp_path, dry_run=True)
    result = process_email(_email("link https://forms.office.com/r/x"), cfg, _PROFILE, hooks)
    assert result.status == "fail"
    assert result.fields_filled == 0
    assert "fill" in result.review_reason.lower()
    # the (empty) preview is still saved so the operator can see nothing landed
    assert result.screenshot_path


def test_form_with_no_questions_is_not_reported_success(tmp_path):
    # Regression: an empty schema (e.g. an MS Forms page that never rendered)
    # must become a fail row, not a success with 0 fields.
    empty_schema = FormSchema(url="https://forms.office.com/r/x", title="", questions=())
    hooks = PipelineHooks(
        read_form=lambda url: empty_schema,
        map_fields=lambda schema: MappingResult(answers=()),
        fill_and_submit=lambda url, instr, dry_run: (b"\x89PNG", False, 0),
    )
    cfg = _config(tmp_path, dry_run=True)
    result = process_email(_email("link https://forms.office.com/r/x"), cfg, _PROFILE, hooks)
    assert result.status == "fail"
    assert result.fields_filled == 0
    assert "question" in result.review_reason.lower()
