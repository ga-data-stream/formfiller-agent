from __future__ import annotations

import re

# Domains that indicate an actual form, in priority order of confidence.
_FORM_HOST_PATTERNS = (
    r"forms\.office\.com",
    r"forms\.microsoft\.com",
    r"forms\.gle",
    r"docs\.google\.com/forms",
)

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)


class NoFormLinkError(Exception):
    """Raised when no recognizable form link is found in an email."""


def _all_urls(html: str, text: str) -> list[str]:
    # href="..." first (most reliable), then any bare URLs in either field.
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    bare = _URL_RE.findall(html) + _URL_RE.findall(text)
    seen: list[str] = []
    for url in [*hrefs, *bare]:
        if url not in seen:
            seen.append(url)
    return seen


def extract_form_url(body_html: str, body_text: str) -> str:
    """Return the first URL whose host matches a known form provider.

    Searches href attributes first, then bare URLs. Raises NoFormLinkError
    if none match.
    """
    urls = _all_urls(body_html, body_text)
    for pattern in _FORM_HOST_PATTERNS:
        for url in urls:
            if re.search(pattern, url, re.IGNORECASE):
                return url
    raise NoFormLinkError("No Microsoft/Google form link found in the email.")
