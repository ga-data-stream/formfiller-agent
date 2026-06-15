from formfiller.agent.models import (
    SnapshotElement, PageSnapshot, ToolCall, ToolResult, LoopOutcome,
)


def test_snapshot_and_signature():
    el = SnapshotElement(ref="e1", role="textbox", name="SIREN", value="", state={"required": True})
    snap = PageSnapshot(url="http://x", title="Form", elements=(el,), blocker=None)
    assert snap.signature() == snap.signature()           # stable
    el2 = SnapshotElement(ref="e1", role="textbox", name="SIREN", value="123", state={})
    other = PageSnapshot(url="http://x", title="Form", elements=(el2,), blocker=None)
    assert snap.signature() != other.signature()          # value change shows progress


def test_toolresult_terminal_defaults_none():
    tc = ToolCall(call_id="c1", name="read_snapshot", arguments={})
    res = ToolResult(call_id="c1", name="read_snapshot", output={"ok": True})
    assert res.terminal is None
    assert tc.arguments == {}


def test_loopoutcome_fields():
    out = LoopOutcome(status="dry_run", reason="ok", fields_filled=3, steps=7)
    assert out.status == "dry_run"
    assert out.screenshot is None and out.schema is None and out.mapping is None
