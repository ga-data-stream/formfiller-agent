from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from formfiller.models import FormSchema, MappingResult, QuestionType


class FillInstruction(BaseModel):
    model_config = ConfigDict(frozen=True)
    question_id: str
    value: str


class GateDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: Literal["submit", "review"]
    reason: str
    fields_to_fill: tuple[FillInstruction, ...]
    fields_blank_flagged: tuple[str, ...]


def evaluate_gate(
    schema: FormSchema, result: MappingResult, threshold: float
) -> GateDecision:
    """Decide whether to auto-submit the filled form or hold it for review.

    Rules (from the spec):
      * Unsupported question type anywhere -> review.
      * Required question with no answer / no matching data -> review.
      * Any answer flagged 'ambiguous' -> review.
      * Any matched answer below `threshold` -> review.
      * Optional question with no matching data -> leave blank, flag, keep going.
      * Otherwise -> submit, filling every matched answer.
    """
    fields_to_fill: list[FillInstruction] = []
    blank_flagged: list[str] = []

    for q in schema.questions:
        if q.type == QuestionType.UNSUPPORTED:
            return _review(f"Unsupported question type for '{q.label}'.")

        answer = result.by_id(q.id)

        if answer is None:
            if q.required:
                return _review(f"Required question '{q.label}' is missing an answer.")
            blank_flagged.append(q.id)
            continue

        if answer.status == "ambiguous":
            return _review(f"Mapping for '{q.label}' is ambiguous.")

        if answer.status == "no_data" or answer.value is None:
            if q.required:
                return _review(f"Required question '{q.label}' has no matching profile data.")
            blank_flagged.append(q.id)
            continue

        # status == "matched" with a value
        if answer.confidence < threshold:
            return _review(
                f"Low confidence ({answer.confidence:.2f}) mapping '{q.label}'."
            )
        fields_to_fill.append(FillInstruction(question_id=q.id, value=answer.value))

    return GateDecision(
        action="submit",
        reason="All required fields filled with sufficient confidence.",
        fields_to_fill=tuple(fields_to_fill),
        fields_blank_flagged=tuple(blank_flagged),
    )


def _review(reason: str) -> GateDecision:
    return GateDecision(
        action="review", reason=reason, fields_to_fill=(), fields_blank_flagged=()
    )
