from formfiller.agent.loop import run_loop
from tests.agent.conftest import FakeLLM, FakeExecutor


def _trace(records):
    class T:
        def write(self, r):
            records.append(r)
    return T()


def test_loop_ends_on_terminal_submit(tmp_path):
    records = []
    llm = FakeLLM([[("read_snapshot", {})], [("submit", {"summary": "go"})]])
    ex = FakeExecutor({"submit": {"output": {"control": "dry_run"}, "terminal": "dry_run",
                                  "reason": "dry-run"}},
                      signatures=["s1", "s2"])
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=20, no_progress_limit=5, trace=_trace(records))
    assert out.status == "dry_run"
    assert out.steps == 2
    assert len(records) == 2


def test_loop_aborts_on_max_steps():
    llm = FakeLLM([[("read_snapshot", {})]] * 50)
    ex = FakeExecutor({}, signatures=[f"s{i}" for i in range(50)])
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=3, no_progress_limit=99, trace=_trace([]))
    assert out.status == "abort"
    assert "max steps" in out.reason.lower()


def test_loop_aborts_on_no_progress():
    llm = FakeLLM([[("read_snapshot", {})]] * 50)
    ex = FakeExecutor({}, signatures=["same"] * 50)   # signature never changes
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=50, no_progress_limit=3, trace=_trace([]))
    assert out.status == "abort"
    assert "progress" in out.reason.lower()


def test_loop_review_when_model_emits_no_tool_calls():
    llm = FakeLLM([])   # immediately returns no tool calls
    ex = FakeExecutor({})
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=5, no_progress_limit=5, trace=_trace([]))
    assert out.status == "review"


def test_loop_carries_screenshot_and_schema_from_terminal(tmp_path):
    from formfiller.models import FormSchema, MappingResult
    schema = FormSchema(url="u", title="t", questions=())
    mapping = MappingResult(answers=())
    llm = FakeLLM([[("submit", {"summary": "go"})]])
    ex = FakeExecutor({"submit": {"output": {}, "terminal": "submitted", "reason": "ok",
                                  "screenshot": b"\x89PNG", "schema": schema, "mapping": mapping}})
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=5, no_progress_limit=5, trace=_trace([]))
    assert out.status == "submitted"
    assert out.screenshot == b"\x89PNG"
    assert out.schema is schema and out.mapping is mapping


def test_loop_counts_only_filled_answer_questions():
    # turn 1: two answer_question calls (one filled, one not) + one click; turn 2: submit
    llm = FakeLLM([
        [("answer_question", {"question_id": "ms:0", "value": "a"}),
         ("answer_question", {"question_id": "ms:1", "value": "b"}),
         ("click", {"ref": "e0"})],
        [("submit", {"summary": "go"})],
    ])
    ex = FakeExecutor(
        {
            "answer_question": {"output": {"status": "filled", "question_id": "x"}},
            "click": {"output": {"ok": True}},
            "submit": {"output": {"control": "dry_run"}, "terminal": "dry_run", "reason": "dry"},
        },
        signatures=["s1", "s2"],
    )
    out = run_loop(llm, ex, instructions="sys", user_input="start",
                   tools=[], max_steps=10, no_progress_limit=5, trace=_trace([]))
    assert out.status == "dry_run"
    # both answer_question calls returned status "filled"; click must NOT count
    assert out.fields_filled == 2
