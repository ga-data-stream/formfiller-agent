from __future__ import annotations

import json
from typing import Literal, Optional, Sequence

from pydantic import BaseModel

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
    "apply or the question is unclear. Respond directly with the structured data; "
    "do not add commentary."
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


def map_fields(
    client,
    deployment: str,
    schema: FormSchema,
    profile: Sequence[ProfileField],
) -> MappingResult:
    """Ask the LLM to map each form question to a profile field.

    `client` is an `openai.AzureOpenAI`-compatible object exposing
    `beta.chat.completions.parse(...)`. `deployment` is the Azure OpenAI
    deployment name (passed as `model`). Returns a MappingResult of validated
    answers. Raises RuntimeError if the model refuses or returns no parse.
    """
    completion = client.beta.chat.completions.parse(
        model=deployment,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user_prompt(schema, profile)},
        ],
        response_format=LLMMapping,
    )
    message = completion.choices[0].message
    if getattr(message, "refusal", None):
        raise RuntimeError(f"LLM refused to map fields: {message.refusal}")
    parsed: LLMMapping = message.parsed
    if parsed is None:
        raise RuntimeError("LLM returned no structured output.")
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
    return MappingResult(answers=answers)
