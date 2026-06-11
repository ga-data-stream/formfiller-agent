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

    Iterates every question, collecting the values it would fill (every matched
    answer with a value — including low-confidence ones, so the review screenshot
    shows the proposed entries) and the optional fields left blank. Any rule
    violation (unsupported type, required field unanswered/unmatched, ambiguous
    mapping, or a matched value below `threshold`) routes the whole form to
    review while STILL carrying those fills/blanks so the orchestrator can fill
    the form for the screenshot.
    """
    fields_to_fill: list[FillInstruction] = []
    blank_flagged: list[str] = []
    review_reasons: list[str] = []

    for q in schema.questions:
        if q.type == QuestionType.UNSUPPORTED:
            review_reasons.append(f"Unsupported question type for '{q.label}'.")
            continue

        answer = result.by_id(q.id)

        if answer is None:
            if q.required:
                review_reasons.append(f"Required question '{q.label}' is missing an answer.")
            else:
                blank_flagged.append(q.id)
            continue

        if answer.status == "ambiguous":
            review_reasons.append(f"Mapping for '{q.label}' is ambiguous.")
            continue

        if answer.status == "no_data" or answer.value is None:
            if q.required:
                review_reasons.append(f"Required question '{q.label}' has no matching profile data.")
            else:
                blank_flagged.append(q.id)
            continue

        # matched with a value — collect the fill (shown even if low-confidence)
        if answer.confidence < threshold:
            review_reasons.append(f"Low confidence ({answer.confidence:.2f}) mapping '{q.label}'.")
        fields_to_fill.append(FillInstruction(question_id=q.id, value=answer.value))

    if review_reasons:
        return GateDecision(
            action="review",
            reason=review_reasons[0],
            fields_to_fill=tuple(fields_to_fill),
            fields_blank_flagged=tuple(blank_flagged),
        )

    return GateDecision(
        action="submit",
        reason="All required fields filled with sufficient confidence.",
        fields_to_fill=tuple(fields_to_fill),
        fields_blank_flagged=tuple(blank_flagged),
    )
