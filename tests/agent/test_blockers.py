from formfiller.agent.blockers import detect_blocker


def _raw(url="https://forms.office.com/r/x", elements=None, has_captcha_frame=False):
    return {"url": url, "elements": elements or [], "has_captcha_frame": has_captcha_frame}


def test_detects_password_login():
    raw = _raw(elements=[{"role": "textbox", "name": "Password", "type": "password"}])
    assert detect_blocker(raw) == "login"


def test_detects_login_by_url():
    raw = _raw(url="https://login.microsoftonline.com/abc", elements=[])
    assert detect_blocker(raw) == "login"


def test_detects_consent_banner():
    raw = _raw(elements=[{"role": "button", "name": "Accept all cookies", "type": ""}])
    assert detect_blocker(raw) == "consent"


def test_detects_captcha_frame():
    assert detect_blocker(_raw(has_captcha_frame=True)) == "captcha"


def test_no_blocker_on_normal_form():
    raw = _raw(elements=[{"role": "textbox", "name": "SIREN", "type": "text"}])
    assert detect_blocker(raw) is None
