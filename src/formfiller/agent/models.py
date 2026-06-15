from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict

from formfiller.models import FormSchema, MappingResult

_FROZEN = ConfigDict(frozen=True)


class SnapshotElement(BaseModel):
    model_config = _FROZEN
    ref: str                       # data-ff-ref id, e.g. "e3"
    role: str                      # accessible role / tag, e.g. "textbox", "button", "radio"
    name: str                      # accessible name / visible text
    value: str = ""
    state: dict[str, Any] = {}     # checked / required / disabled / etc.


class PageSnapshot(BaseModel):
    model_config = _FROZEN
    url: str
    title: str
    elements: tuple[SnapshotElement, ...]
    blocker: Optional[str] = None  # "login" | "consent" | "captcha" | None

    def signature(self) -> str:
        """Stable string capturing structure+values, for no-progress detection."""
        parts = [self.url, self.title, self.blocker or ""]
        for e in self.elements:
            parts.append(f"{e.ref}|{e.role}|{e.name}|{e.value}|{json.dumps(e.state, sort_keys=True)}")
        return "\n".join(parts)


class ToolCall(BaseModel):
    model_config = _FROZEN
    call_id: str
    name: str
    arguments: dict[str, Any] = {}


class ToolResult(BaseModel):
    model_config = _FROZEN
    call_id: str
    name: str
    output: dict[str, Any]
    # When set, the loop ends with this status.
    terminal: Optional[Literal["submitted", "dry_run", "review", "fail"]] = None
    reason: str = ""
    screenshot: Optional[bytes] = None
    schema: Optional[FormSchema] = None
    mapping: Optional[MappingResult] = None


class LoopOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    status: Literal["submitted", "dry_run", "review", "abort", "fail"]
    reason: str = ""
    fields_filled: int = 0
    steps: int = 0
    screenshot: Optional[bytes] = None
    schema: Optional[FormSchema] = None
    mapping: Optional[MappingResult] = None
