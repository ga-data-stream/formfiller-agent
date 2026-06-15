from formfiller.agent.llm import LLMTurn
from formfiller.agent.models import ToolCall, ToolResult


class FakeLLM:
    """Replays scripted turns. Each scripted turn is a list of (name, args)."""
    def __init__(self, scripted_turns):
        self._turns = list(scripted_turns)
        self.calls = 0

    def respond(self, *, previous_response_id, input, tools):
        if not self._turns:
            return LLMTurn(response_id=f"r{self.calls}", tool_calls=())  # no action → review
        turn = self._turns.pop(0)
        self.calls += 1
        tcs = tuple(ToolCall(call_id=f"c{self.calls}_{i}", name=n, arguments=a)
                    for i, (n, a) in enumerate(turn))
        return LLMTurn(response_id=f"r{self.calls}", tool_calls=tcs)


class FakeExecutor:
    """Records dispatched calls; returns scripted ToolResults by tool name."""
    def __init__(self, results_by_name, signatures=None):
        self._results = results_by_name
        self._sigs = list(signatures or [])
        self.dispatched = []

    def dispatch(self, call: ToolCall) -> ToolResult:
        self.dispatched.append(call)
        spec = self._results.get(call.name, {"output": {"ok": True}})
        return ToolResult(call_id=call.call_id, name=call.name, **spec)

    def page_signature(self) -> str:
        return self._sigs.pop(0) if self._sigs else "static"
