import pytest
from formfiller.link_extractor import extract_form_url, NoFormLinkError


def test_picks_ms_forms_link_from_html():
    html = '<p>Hi <a href="https://acme.com/news">news</a> please fill ' \
           '<a href="https://forms.office.com/r/abc123">this form</a></p>'
    assert extract_form_url(html, "") == "https://forms.office.com/r/abc123"


def test_picks_google_forms_short_link_from_plain_text():
    text = "Complete it here: https://forms.gle/XyZ123 — thanks"
    assert extract_form_url("", text) == "https://forms.gle/XyZ123"


def test_picks_google_docs_forms_link():
    text = "Form: https://docs.google.com/forms/d/e/1FAIpQL/viewform"
    assert extract_form_url("", text) == "https://docs.google.com/forms/d/e/1FAIpQL/viewform"


def test_prefers_form_link_over_other_links():
    text = "See https://acme.com and fill https://forms.microsoft.com/r/zzz"
    assert extract_form_url("", text) == "https://forms.microsoft.com/r/zzz"


def test_raises_when_no_form_link_present():
    with pytest.raises(NoFormLinkError):
        extract_form_url("<a href='https://acme.com'>x</a>", "no forms here")
