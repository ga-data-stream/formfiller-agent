from __future__ import annotations

import json
from typing import Literal, Optional, Sequence

from pydantic import BaseModel

from formfiller.choices import match_choice
from formfiller.config import ProfileField
from formfiller.models import FormSchema, MappedAnswer, MappingResult


class LLMMappedAnswer(BaseModel):
    question_id: str
    profile_field: Optional[str]
    value: Optional[str]
    confidence: float
    status: Literal["matched", "no_data", "ambiguous"]


class LLMMapping(BaseModel):
    answers: list[LLMMappedAnswer]


_SYSTEM = (
    "You map web-form questions to a fixed company data profile. "
    "For each question, choose the single best-matching profile field and return "
    "its value, a confidence in [0,1], and a status. Use status 'matched' when a "
    "profile field clearly answers the question, 'no_data' when the profile has "
    "nothing relevant, and 'ambiguous' when two or more fields could plausibly "
    "apply or the question is unclear. When a question lists 'options' (a choice "
    "question), the value you return MUST be exactly one of those options, copied "
    "verbatim (same spelling, case, and separators) — never a paraphrase; pick the "
    "option that best fits the profile data. Respond directly with the structured "
    "data; do not add commentary."
)


def _build_user_prompt(schema: FormSchema, profile: Sequence[ProfileField]) -> str:
    profile_lines = [
        {"field": f.name, "value": f.value, "aliases": list(f.aliases)}
        for f in profile
    ]
    question_lines = [
        {
            "question_id": q.id,
            "label": q.label,
            "type": q.type.value,
            "required": q.required,
            "options": list(q.options),
        }
        for q in schema.questions
    ]
    return (
        "PROFILE (the only data you may use as values):\n"
        + json.dumps(profile_lines, ensure_ascii=False, indent=2)
        + "\n\nFORM QUESTIONS:\n"
        + json.dumps(question_lines, ensure_ascii=False, indent=2)
        + "\n\nReturn one answer object per question_id above."
    )


def _resolve_choice_values(schema: FormSchema, result: MappingResult) -> MappingResult:
    """Snap each choice answer to an exact option, deterministically.

    For questions with a fixed option set, `gpt-5.4-nano` tends to echo the
    descriptive profile value (e.g. 'SIREN + SIRET') and flag it ambiguous with
    low confidence rather than commit to an enum value. When that value resolves
    unambiguously to exactly one option, rewrite it to that option as a confident
    'matched' — a deterministic exact match is more reliable than the model's
    self-assessment. Values that resolve to no option are left untouched, so they
    still route to review.
    """
    options_by_id = {q.id: q.options for q in schema.questions if q.options}
    answers = []
    for a in result.answers:
        opts = options_by_id.get(a.question_id)
        if opts and a.value:
            idx = match_choice(list(opts), a.value)
            if idx is not None:
                a = a.model_copy(
                    update={"value": opts[idx], "status": "matched", "confidence": 1.0}
                )
        answers.append(a)
    return MappingResult(answers=tuple(answers))


def map_fields(
    client,
    deployment: str,
    schema: FormSchema,
    profile: Sequence[ProfileField],
    max_output_tokens: int = 16000,
) -> MappingResult:
    """Ask the LLM to map each form question to a profile field via the Azure
    AI Foundry v1 Responses API with structured outputs.

    `client` is an `openai.OpenAI`-compatible object (pointed at
    `<endpoint>/openai/v1/`) exposing `responses.parse(...)`. `deployment` is the
    model deployment name. Returns a MappingResult of validated answers. Raises
    RuntimeError if the model produced no parseable structured output (refusal
    or an incomplete/over-budget response).
    """
    completion = client.responses.parse(
        model=deployment,
        instructions=_SYSTEM,
        input=_build_user_prompt(schema, profile),
        text_format=LLMMapping,
        max_output_tokens=max_output_tokens,
    )
    parsed = getattr(completion, "output_parsed", None)
    if parsed is None:
        status = getattr(completion, "status", "unknown")
        raise RuntimeError(
            f"LLM returned no structured output (status={status}). "
            "If status is 'incomplete', raise max_output_tokens."
        )
    answers = tuple(
        MappedAnswer(
            question_id=a.question_id,
            profile_field=a.profile_field,
            value=a.value,
            confidence=a.confidence,
            status=a.status,
        )
        for a in parsed.answers
    )
    return _resolve_choice_values(schema, MappingResult(answers=answers))
