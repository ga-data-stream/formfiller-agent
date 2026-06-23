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


class FakeEmailSource:
    """In-memory source for tests."""

    def __init__(self, messages: list[EmailMessage]):
        self._messages = list(messages)

    def list_recent(self, count: int) -> list[EmailMessage]:
        return self._messages[:count]

    def get(self, entry_id: str) -> Optional[EmailMessage]:
        for m in self._messages:
            if m.entry_id == entry_id:
                return m
        return None


class OutlookEmailSource:
    """Reads the live Outlook inbox via the desktop COM interface.

    Requires Outlook installed and a logged-in profile (the POC runs on
    Pierre's own machine). Imports pywin32 lazily so the rest of the package
    imports cleanly on non-Windows CI.
    """

    def __init__(self) -> None:
        import win32com.client  # lazy import; Windows + Outlook only

        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        # 6 == olFolderInbox
        self._inbox = outlook.GetDefaultFolder(6)

    def list_recent(self, count: int) -> list[EmailMessage]:
        items = self._inbox.Items
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
            item = self._inbox.Session.GetItemFromID(entry_id)
        except Exception:
            return None
        return self._to_message(item)

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
