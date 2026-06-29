from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Union, runtime_checkable

from formfiller.agent.models import ToolCall


@dataclass
class LLMTurn:
    response_id: str
    tool_calls: tuple[ToolCall, ...]
    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentLLM(Protocol):
    def respond(self, *, previous_response_id: Optional[str],
                input: Union[str, list[dict]], tools: list[dict]) -> LLMTurn: ...


class OpenAIResponsesAgentLLM:
    """Drives the Azure v1 Responses API function-calling loop.

    `client` is an openai.OpenAI pointed at <endpoint>/openai/v1/ (see cli.py).
    Instructions are sent only on the first turn; continuation turns rely on
    previous_response_id to carry context.
    """

    def __init__(self, client, *, deployment: str, instructions: str,
                 max_output_tokens: int = 16000,
                 reasoning_effort: str = "medium") -> None:
        self.client = client
        self.deployment = deployment
        self.instructions = instructions
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort

    def respond(self, *, previous_response_id, input, tools) -> LLMTurn:
        kwargs: dict[str, Any] = {
            "model": self.deployment,
            "input": input,
            "tools": tools,
            "tool_choice": "auto",
            "max_output_tokens": self.max_output_tokens,
            "reasoning": {"effort": self.reasoning_effort},
        }
        if previous_response_id is None:
            kwargs["instructions"] = self.instructions
        else:
            kwargs["previous_response_id"] = previous_response_id

        resp = self.client.responses.create(**kwargs)

        calls = []
        for item in (getattr(resp, "output", None) or []):
            if getattr(item, "type", None) == "function_call":
                try:
                    args = json.loads(item.arguments) if item.arguments else {}
                except (TypeError, ValueError):
                    args = {}
                calls.append(ToolCall(call_id=item.call_id, name=item.name, arguments=args))

        usage = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = {"input_tokens": getattr(u, "input_tokens", None),
                     "output_tokens": getattr(u, "output_tokens", None)}

        text = getattr(resp, "output_text", "") or ""
        return LLMTurn(response_id=resp.id, tool_calls=tuple(calls), text=text, usage=usage)
