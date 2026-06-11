import pytest
from formfiller.models import EmailMessage
from formfiller.email_source import FakeEmailSource, EmailSource


def _msg(entry_id, subject):
    return EmailMessage(
        entry_id=entry_id, sender="a@b.com", subject=subject,
        received="2026-06-10T09:00:00", body_text="t", body_html="<p>t</p>",
    )


def test_fake_source_lists_and_fetches_by_entry_id():
    source: EmailSource = FakeEmailSource([_msg("E1", "First"), _msg("E2", "Second")])
    listed = source.list_recent(10)
    assert [m.subject for m in listed] == ["First", "Second"]
    assert source.get("E2").subject == "Second"


def test_fake_source_list_respects_count():
    source = FakeEmailSource([_msg(f"E{i}", str(i)) for i in range(5)])
    assert len(source.list_recent(3)) == 3


def test_fake_source_get_missing_returns_none():
    source = FakeEmailSource([_msg("E1", "x")])
    assert source.get("nope") is None
