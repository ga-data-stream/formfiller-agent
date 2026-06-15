import json
from formfiller.agent.trace import TraceWriter


def test_trace_writes_jsonl_lines(tmp_path):
    w = TraceWriter(tmp_path / "traces", run_id="run-1")
    w.write({"step": 1, "tool": "read_snapshot"})
    w.write({"step": 2, "tool": "submit"})
    path = w.path
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["tool"] == "read_snapshot"
    assert json.loads(lines[1])["step"] == 2


def test_trace_run_id_in_filename(tmp_path):
    w = TraceWriter(tmp_path / "traces", run_id="abc")
    assert "abc" in w.path.name and w.path.suffix == ".jsonl"
