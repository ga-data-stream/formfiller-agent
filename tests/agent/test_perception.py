from formfiller.agent.perception import build_snapshot, read_snapshot


def test_build_snapshot_maps_elements_and_blocker():
    raw = {
        "url": "https://forms.office.com/r/x",
        "title": "E-invoicing",
        "has_captcha_frame": False,
        "elements": [
            {"ref": "e0", "role": "button", "name": "Start now", "value": "", "type": "",
             "state": {}},
            {"ref": "e1", "role": "textbox", "name": "SIREN", "value": "", "type": "text",
             "state": {"required": True}},
        ],
    }
    snap = build_snapshot(raw)
    assert snap.url.endswith("/r/x")
    assert snap.title == "E-invoicing"
    assert snap.blocker is None
    assert [e.ref for e in snap.elements] == ["e0", "e1"]
    assert snap.elements[1].state["required"] is True


def test_build_snapshot_sets_blocker():
    raw = {"url": "https://login.microsoftonline.com/x", "title": "Sign in",
           "has_captcha_frame": False, "elements": []}
    assert build_snapshot(raw).blocker == "login"


class _FakePage:
    """Stands in for a Playwright Page: returns canned evaluate output."""
    def __init__(self, raw):
        self._raw = raw
        self.evaluated = []

    def evaluate(self, js):
        self.evaluated.append(js)
        return self._raw


def test_read_snapshot_uses_page_evaluate():
    raw = {"url": "u", "title": "t", "has_captcha_frame": False,
           "elements": [{"ref": "e0", "role": "textbox", "name": "A", "value": "",
                         "type": "text", "state": {}}]}
    page = _FakePage(raw)
    snap = read_snapshot(page)
    assert snap.elements[0].ref == "e0"
    assert page.evaluated, "read_snapshot must call page.evaluate"
