from __future__ import annotations

from typing import Any, Optional

_LOGIN_HOSTS = ("login.microsoftonline.com", "login.live.com", "accounts.google.com", "/login")
_CONSENT_TEXTS = ("accept all", "accept cookies", "accept all cookies", "agree", "tout accepter",
                  "accepter", "j'accepte")


def detect_blocker(raw: dict[str, Any]) -> Optional[str]:
    """Heuristically classify a page as a login / consent / captcha blocker.

    `raw` is the dict produced by perception's page evaluate:
    {"url": str, "elements": [{"role","name","type"}], "has_captcha_frame": bool}.
    Order matters: captcha first (never auto-solve), then login, then consent.
    """
    if raw.get("has_captcha_frame"):
        return "captcha"

    url = (raw.get("url") or "").lower()
    if any(h in url for h in _LOGIN_HOSTS):
        return "login"

    elements = raw.get("elements") or []
    for e in elements:
        if (e.get("type") or "").lower() == "password":
            return "login"

    for e in elements:
        name = (e.get("name") or "").strip().lower()
        if (e.get("role") or "") == "button" and any(t == name or t in name for t in _CONSENT_TEXTS):
            return "consent"

    return None
