import pytest
from formfiller.models import EmailMessage
from formfiller.email_source import FakeEmailSource, EmailSource, _resolve_subfolder


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


class _FakeFolder:
    """Mimics an Outlook folder: a .Name and a .Folders collection of children."""
    def __init__(self, name, children=()):
        self.Name = name
        self.Folders = list(children)


def test_resolve_subfolder_blank_returns_inbox():
    inbox = _FakeFolder("Inbox", [_FakeFolder("ligne adressage")])
    assert _resolve_subfolder(inbox, "") is inbox


def test_resolve_subfolder_exact_match_returns_child():
    target = _FakeFolder("ligne adressage")
    inbox = _FakeFolder("Inbox", [_FakeFolder("Autre"), target])
    assert _resolve_subfolder(inbox, "ligne adressage") is target


def test_resolve_subfolder_is_case_insensitive():
    target = _FakeFolder("ligne adressage")
    inbox = _FakeFolder("Inbox", [target])
    assert _resolve_subfolder(inbox, "Ligne Adressage") is target


def test_resolve_subfolder_missing_raises_listing_available():
    inbox = _FakeFolder("Inbox", [_FakeFolder("Autre"), _FakeFolder("Divers")])
    with pytest.raises(RuntimeError) as exc:
        _resolve_subfolder(inbox, "ligne adressage")
    msg = str(exc.value)
    assert "ligne adressage" in msg
    assert "Autre" in msg and "Divers" in msg


def test_fake_source_move_records_and_returns_true():
    source = FakeEmailSource([_msg("E1", "x")])
    assert source.move_to_subfolder("E1", "Traité") is True
    assert source.moves == [("E1", "Traité")]


def test_fake_source_move_can_simulate_failure():
    source = FakeEmailSource([_msg("E1", "x")], move_fails=True)
    assert source.move_to_subfolder("E1", "Traité") is False
    assert source.moves == []


class _FakeFolders(list):
    """Liste de dossiers qui sait en créer un nouveau (comme Outlook Folders.Add)."""
    def Add(self, name):
        f = _FakeFolder(name)
        self.append(f)
        return f


def test_resolve_or_create_returns_existing():
    from formfiller.email_source import _resolve_or_create
    existing = _FakeFolder("Traité")
    parent = _FakeFolder("Inbox")
    parent.Folders = _FakeFolders([_FakeFolder("Autre"), existing])
    assert _resolve_or_create(parent, "traité") is existing   # insensible à la casse


def test_resolve_or_create_creates_when_absent():
    from formfiller.email_source import _resolve_or_create
    parent = _FakeFolder("Inbox")
    parent.Folders = _FakeFolders([_FakeFolder("Autre")])
    created = _resolve_or_create(parent, "Revue humaine")
    assert created.Name == "Revue humaine"
    assert any(f.Name == "Revue humaine" for f in parent.Folders)
