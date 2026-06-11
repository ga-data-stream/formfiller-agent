import pytest
from formfiller.models import EmailMessage
from formfiller.cli import format_inbox_line, parse_selection


def _msg(i):
    return EmailMessage(
        entry_id=f"E{i}", sender=f"s{i}@acme.com", subject=f"Subject {i}",
        received="2026-06-10T09:00:00", body_text="", body_html="",
    )


def test_format_inbox_line_includes_index_sender_subject():
    line = format_inbox_line(0, _msg(0))
    assert line.startswith("[0]")
    assert "s0@acme.com" in line
    assert "Subject 0" in line


def test_parse_selection_valid_index():
    msgs = [_msg(0), _msg(1), _msg(2)]
    assert parse_selection("1", msgs).entry_id == "E1"


def test_parse_selection_out_of_range_returns_none():
    msgs = [_msg(0)]
    assert parse_selection("5", msgs) is None


def test_parse_selection_non_numeric_returns_none():
    msgs = [_msg(0)]
    assert parse_selection("abc", msgs) is None
