from __future__ import annotations

import json
from typing import Any, Optional

from formfiller.agent.models import LoopOutcome


def run_loop(llm, executor, *, instructions: str, user_input: str,
             tools: list[dict], max_steps: int, no_progress_limit: int,
             trace) -> LoopOutcome:
    """Drive the function-calling loop until a terminal tool result, no action,
    the step budget, or the no-progress breaker. Returns a LoopOutcome."""
    prev_id: Optional[str] = None
    next_input: Any = user_input
    last_sig: Optional[str] = None
    no_progress = 0
    fields_filled = 0

    for step in range(1, max_steps + 1):
        turn = llm.respond(previous_response_id=prev_id, input=next_input, tools=tools)
        prev_id = turn.response_id

        if not turn.tool_calls:
            trace.write({"step": step, "event": "no_tool_calls", "text": turn.text,
                         "usage": turn.usage})
            return LoopOutcome(status="review", reason="agent stopped without an action",
                               fields_filled=fields_filled, steps=step)

        outputs = []
        for call in turn.tool_calls:
            result = executor.dispatch(call)
            trace.write({"step": step, "tool": call.name, "arguments": call.arguments,
                         "observation": result.output, "terminal": result.terminal,
                         "usage": turn.usage})
            if call.name == "answer_question" and result.output.get("status") == "filled":
                fields_filled += 1
            outputs.append({"type": "function_call_output", "call_id": call.call_id,
                            "output": json.dumps(result.output, default=str)})
            if result.terminal is not None:
                return LoopOutcome(status=result.terminal, reason=result.reason,
                                   fields_filled=fields_filled, steps=step,
                                   screenshot=result.screenshot, schema=result.schema,
                                   mapping=result.mapping)

        sig = executor.page_signature()
        no_progress = no_progress + 1 if sig == last_sig else 0
        last_sig = sig
        if no_progress >= no_progress_limit:
            trace.write({"step": step, "event": "no_progress_abort"})
            return LoopOutcome(status="abort", reason="no progress after repeated steps",
                               fields_filled=fields_filled, steps=step)

        next_input = outputs

    trace.write({"event": "max_steps_abort", "steps": max_steps})
    return LoopOutcome(status="abort", reason="max steps reached",
                       fields_filled=fields_filled, steps=max_steps)
