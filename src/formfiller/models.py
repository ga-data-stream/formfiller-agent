from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class QuestionType(str, Enum):
    TEXT = "text"
    EMAIL = "email"
    DATE = "date"
    NUMBER = "number"
    CHOICE_SINGLE = "choice_single"
    CHOICE_MULTI = "choice_multi"
    UNSUPPORTED = "unsupported"


_FROZEN = ConfigDict(frozen=True)


class FormQuestion(BaseModel):
    model_config = _FROZEN
    id: str
    label: str
    type: QuestionType
    required: bool
    options: tuple[str, ...] = ()


class FormSchema(BaseModel):
    model_config = _FROZEN
    url: str
    title: str
    questions: tuple[FormQuestion, ...]


MappingStatus = Literal["matched", "no_data", "ambiguous"]


class MappedAnswer(BaseModel):
    model_config = _FROZEN
    question_id: str
    profile_field: Optional[str]
    value: Optional[str]
    confidence: float
    status: MappingStatus
    rationale: str = ""


class MappingResult(BaseModel):
    model_config = _FROZEN
    answers: tuple[MappedAnswer, ...]

    def by_id(self, question_id: str) -> Optional[MappedAnswer]:
        for a in self.answers:
            if a.question_id == question_id:
                return a
        return None


class DecisionRecord(BaseModel):
    model_config = _FROZEN
    question_id: str
    label: str
    type: str
    required: bool
    profile_field: Optional[str]
    value: Optional[str]
    propose_status: str
    propose_confidence: float
    propose_rationale: str
    final_status: str
    final_confidence: float
    verify_rationale: str
    final_action: Literal["fill", "review", "blank"]


class MappingOutcome(BaseModel):
    model_config = _FROZEN
    result: MappingResult
    decisions: tuple[DecisionRecord, ...] = ()


class EmailMessage(BaseModel):
    model_config = _FROZEN
    entry_id: str
    sender: str
    subject: str
    received: str
    body_text: str
    body_html: str
