from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from formfiller.models import EmailMessage


@runtime_checkable
class EmailSource(Protocol):
    """A source of inbox emails the user can pick from."""

    def list_recent(self, count: int) -> list[EmailMessage]:
        ...

    def get(self, entry_id: str) -> Optional[EmailMessage]:
        ...

    def move_to_subfolder(self, entry_id: str, name: str) -> bool:
        ...


class FakeEmailSource:
    """In-memory source for tests."""

    def __init__(self, messages: list[EmailMessage], *, move_fails: bool = False):
        self._messages = list(messages)
        self.moves: list[tuple[str, str]] = []
        self._move_fails = move_fails

    def list_recent(self, count: int) -> list[EmailMessage]:
        return self._messages[:count]

    def get(self, entry_id: str) -> Optional[EmailMessage]:
        for m in self._messages:
            if m.entry_id == entry_id:
                return m
        return None

    def move_to_subfolder(self, entry_id: str, name: str) -> bool:
        if self._move_fails:
            return False
        self.moves.append((entry_id, name))
        return True


def _resolve_subfolder(inbox, subfolder: str):
    """Return the Inbox subfolder named `subfolder` (case-insensitive), or
    `inbox` itself when `subfolder` is blank. Raises RuntimeError listing the
    available subfolders when the name is not found."""
    if not subfolder:
        return inbox
    for f in inbox.Folders:
        if str(f.Name).casefold() == subfolder.casefold():
            return f
    available = [str(f.Name) for f in inbox.Folders]
    raise RuntimeError(
        f"Outlook subfolder {subfolder!r} not found under the Inbox. "
        f"Available: {available}"
    )


def _resolve_or_create(parent, name: str):
    """Retourne le sous-dossier `name` (insensible à la casse) sous `parent`,
    en le créant via `Folders.Add` s'il n'existe pas encore."""
    for f in parent.Folders:
        if str(f.Name).casefold() == name.casefold():
            return f
    return parent.Folders.Add(name)


class OutlookEmailSource:
    """Reads the live Outlook inbox via the desktop COM interface.

    Requires Outlook installed and a logged-in profile (the POC runs on
    Pierre's own machine). Imports pywin32 lazily so the rest of the package
    imports cleanly on non-Windows CI.
    """

    def __init__(self, subfolder: str = "") -> None:
        import win32com.client  # lazy import; Windows + Outlook only

        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        inbox = outlook.GetDefaultFolder(6)  # 6 == olFolderInbox
        self._folder = _resolve_subfolder(inbox, subfolder)

    def list_recent(self, count: int) -> list[EmailMessage]:
        items = self._folder.Items
        items.Sort("[ReceivedTime]", True)  # newest first
        out: list[EmailMessage] = []
        # GetFirst/GetNext is the robust idiom for a sorted live collection;
        # a plain `for item in items` enumerator over it is known to be flaky.
        item = items.GetFirst()
        while item is not None and len(out) < count:
            try:
                if getattr(item, "Class", None) == 43:  # 43 == olMail
                    out.append(self._to_message(item))
            except Exception as exc:  # item Outlook can't resolve right now — skip it
                subject = getattr(item, "Subject", "<unknown>")
                print(f"[warn] skipping unreadable inbox item {subject!r}: {exc}")
            item = items.GetNext()
        return out

    def get(self, entry_id: str) -> Optional[EmailMessage]:
        try:
            item = self._folder.Session.GetItemFromID(entry_id)
        except Exception:
            return None
        return self._to_message(item)

    def move_to_subfolder(self, entry_id: str, name: str) -> bool:
        """Déplace le mail `entry_id` vers un sous-dossier frère du dossier
        source (créé si absent). Retourne False sur toute erreur COM (jamais
        d'exception propagée : le batch doit continuer)."""
        try:
            # self._folder.Parent is a sibling of the source folder: with a
            # blank inbox_subfolder self._folder is the Inbox itself, so
            # Traité/Revue humaine would land at the mailbox-store root (prod
            # config always sets inbox_subfolder, so this doesn't happen there).
            target = _resolve_or_create(self._folder.Parent, name)
            item = self._folder.Session.GetItemFromID(entry_id)
            item.Move(target)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] déplacement de {entry_id} vers {name!r} impossible: {exc}")
            return False

    @staticmethod
    def _to_message(item) -> EmailMessage:
        received = getattr(item, "ReceivedTime", None)
        return EmailMessage(
            entry_id=str(item.EntryID),
            sender=str(getattr(item, "SenderEmailAddress", "") or ""),
            subject=str(getattr(item, "Subject", "") or ""),
            received=received.isoformat() if received is not None else "",
            body_text=str(getattr(item, "Body", "") or ""),
            body_html=str(getattr(item, "HTMLBody", "") or ""),
        )
