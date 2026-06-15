from formfiller.agent.llm import OpenAIResponsesAgentLLM, LLMTurn


class _FnCall:
    type = "function_call"
    def __init__(self, name, args, call_id):
        self.name = name
        self.arguments = args
        self.call_id = call_id


class _Resp:
    def __init__(self, output, rid="resp-1"):
        self.output = output
        self.id = rid
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


class _Client:
    def __init__(self, resp):
        self.responses = self
        self._resp = resp
        self.last_kwargs = None
    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._resp


def test_respond_parses_function_calls():
    resp = _Resp([_FnCall("read_snapshot", "{}", "call_1"),
                  _FnCall("answer_question", '{"question_id":"ms:0","value":"x"}', "call_2")])
    llm = OpenAIResponsesAgentLLM(_Client(resp), deployment="gpt-5.4-nano",
                                  instructions="sys")
    turn = llm.respond(previous_response_id=None, input="go", tools=[{"type": "function",
            "name": "read_snapshot", "parameters": {"type": "object", "properties": {}}}])
    assert isinstance(turn, LLMTurn)
    assert turn.response_id == "resp-1"
    assert [c.name for c in turn.tool_calls] == ["read_snapshot", "answer_question"]
    assert turn.tool_calls[1].arguments == {"question_id": "ms:0", "value": "x"}


def test_respond_passes_previous_id_and_tools():
    client = _Client(_Resp([]))
    llm = OpenAIResponsesAgentLLM(client, deployment="d", instructions="sys")
    llm.respond(previous_response_id="resp-prev",
                input=[{"type": "function_call_output", "call_id": "c", "output": "{}"}],
                tools=[])
    assert client.last_kwargs["previous_response_id"] == "resp-prev"
    assert client.last_kwargs["model"] == "d"
    # first turn sends instructions; continuation turns should not resend them
    assert client.last_kwargs.get("instructions") in (None, "sys")
