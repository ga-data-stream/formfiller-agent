from __future__ import annotations

import json
import logging
from typing import Literal, Optional, Sequence

from pydantic import BaseModel

from formfiller.choices import match_choice
from formfiller.config import ProfileField
from formfiller.models import FormSchema, MappedAnswer, MappingResult

logger = logging.getLogger(__name__)


class LLMMappedAnswer(BaseModel):
    question_id: str
    profile_field: Optional[str]
    value: Optional[str]
    confidence: float
    status: Literal["matched", "no_data", "ambiguous"]
    rationale: str = ""


class LLMMapping(BaseModel):
    answers: list[LLMMappedAnswer]


_SYSTEM = (
    "You map web-form questions to a fixed company data profile. Reason from each "
    "profile field's 'description' (what it is and when it applies), not just its "
    "name. For each question, choose the single best-matching profile field and "
    "return its value, a confidence in [0,1], a status, and a one-sentence "
    "'rationale' explaining your choice. Use status 'matched' when a profile field "
    "answers the question — commit to it even if the wording differs from the field "
    "name. Use 'no_data' when the profile genuinely has nothing relevant. Use "
    "'ambiguous' ONLY when two or more fields could each plausibly answer, or the "
    "question itself is unclear. When a question lists 'option' (a choice "
    "question), the value MUST be exactly one of those options, copied verbatim "
    "(same spelling, case, separators) — never a paraphrase. Never invent data: "
    "values must come from the profile. Respond with the structured data only."
)


def _build_user_prompt(schema: FormSchema, profile: Sequence[ProfileField]) -> str:
    profile_lines = [
        {"field": f.name, "value": f.value,
         "description": f.description, "aliases": list(f.aliases)}
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


class LLMVerifiedAnswer(BaseModel):
    question_id: str
    profile_field: Optional[str]
    value: Optional[str]
    confidence: float
    status: Literal["matched", "no_data", "ambiguous"]
    rationale: str = ""


class LLMVerification(BaseModel):
    answers: list[LLMVerifiedAnswer]


_VERIFY_SYSTEM = (
    "You are a strict reviewer of a first-pass mapping from form questions to a "
    "company data profile. For each question you are given the proposed field, "
    "value, status and rationale. Decide the FINAL answer: confirm it, correct the "
    "field/value (using ONLY profile values), or flag it. Return status 'matched' "
    "when a profile field clearly answers it (commit even if wording differs), "
    "'no_data' when the profile has nothing relevant, and 'ambiguous' ONLY when two "
    "or more fields genuinely compete or the question is unclear. For choice "
    "questions the value MUST be exactly one of the listed options, verbatim. Give "
    "a one-sentence 'rationale' for your verdict. Never invent data."
)


def _verify(client, deployment: str, schema: FormSchema,
            profile: Sequence[ProfileField], proposed: MappingResult,
            max_output_tokens: int, reasoning_effort: str = "medium") -> LLMVerification:
    proposed_payload = [
        {"question_id": a.question_id, "profile_field": a.profile_field,
         "value": a.value, "status": a.status, "rationale": a.rationale}
        for a in proposed.answers
    ]
    user = (
        _build_user_prompt(schema, profile)
        + "\n\nFIRST-PASS PROPOSALS (review each):\n"
        + json.dumps(proposed_payload, ensure_ascii=False, indent=2)
        + "\n\nReturn one verified answer object per question_id."
    )
    completion = client.responses.parse(
        model=deployment, instructions=_VERIFY_SYSTEM, input=user,
        text_format=LLMVerification, max_output_tokens=max_output_tokens,
        reasoning={"effort": reasoning_effort},
    )
    parsed = getattr(completion, "output_parsed", None)
    if parsed is None:
        status = getattr(completion, "status", "unknown")
        raise RuntimeError(f"verify pass returned no structured output (status={status}).")
    return parsed


def _action_for(q, ans) -> str:
    from formfiller.models import QuestionType
    if q.type == QuestionType.UNSUPPORTED:
        return "review"
    if ans is None:
        return "review" if q.required else "blank"
    if ans.status == "ambiguous":
        return "review"
    if ans.status == "no_data" or ans.value is None:
        return "review" if q.required else "blank"
    return "fill"


def _outcome_from_single(schema: FormSchema, proposed: MappingResult,
                         verify_note: str = "") -> "MappingOutcome":
    from formfiller.models import DecisionRecord, MappingOutcome
    by_id = {a.question_id: a for a in proposed.answers}
    decisions = []
    for q in schema.questions:
        a = by_id.get(q.id)
        decisions.append(DecisionRecord(
            question_id=q.id, label=q.label, type=q.type.value, required=q.required,
            profile_field=a.profile_field if a else None,
            value=a.value if a else None,
            propose_status=a.status if a else "no_data",
            propose_confidence=a.confidence if a else 0.0,
            propose_rationale=a.rationale if a else "",
            final_status=a.status if a else "no_data",
            final_confidence=a.confidence if a else 0.0,
            verify_rationale=verify_note,
            final_action=_action_for(q, a),
        ))
    return MappingOutcome(result=proposed, decisions=tuple(decisions))


def map_and_verify(client, deployment: str, schema: FormSchema,
                   profile: Sequence[ProfileField], verify: bool = True,
                   max_output_tokens: int = 16000,
                   reasoning_effort: str = "medium",
                   verifier_deployment: str = "",
                   verifier_reasoning_effort: str | None = None) -> "MappingOutcome":
    """Two-pass mapping. Pass 1 proposes (with rationale); pass 2 verifies and
    sets the final status. Returns a MappingOutcome (result for the gate +
    decisions for the reasoning log). Falls back to pass-1 if verify fails."""
    from formfiller.models import DecisionRecord, MappedAnswer, MappingOutcome
    proposed = map_fields(client, deployment, schema, profile, max_output_tokens,
                          reasoning_effort=reasoning_effort)
    if not verify:
        return _outcome_from_single(schema, proposed)
    v_dep = verifier_deployment or deployment
    v_effort = verifier_reasoning_effort or reasoning_effort
    try:
        verification = _verify(client, v_dep, schema, profile, proposed,
                               max_output_tokens, reasoning_effort=v_effort)
    except Exception as exc:  # noqa: BLE001 — verify is best-effort
        logger.warning("verify pass failed (%s); using pass-1 mapping.", exc)
        return _outcome_from_single(schema, proposed, verify_note="(verification unavailable)")

    allowed = {f.value for f in profile if f.value}
    opt_sets = {q.id: set(q.options) for q in schema.questions if q.options}
    proposed_by_id = {a.question_id: a for a in proposed.answers}
    verified_by_id = {v.question_id: v for v in verification.answers}

    merged = []
    for q in schema.questions:
        p = proposed_by_id.get(q.id)
        v = verified_by_id.get(q.id)
        if v is None:
            merged.append(p or MappedAnswer(question_id=q.id, profile_field=None,
                          value=None, confidence=0.0, status="no_data",
                          rationale="(no verifier response)"))
            continue
        ok = (v.value in allowed) or (q.id in opt_sets and v.value in opt_sets[q.id])
        value = v.value if ok else (p.value if p else None)
        field = v.profile_field if ok else (p.profile_field if p else None)
        status = v.status
        # Hardening: a 'matched' verdict with no usable value must not fill.
        if value is None and status == "matched":
            status = "no_data"
        merged.append(MappedAnswer(question_id=q.id, profile_field=field, value=value,
                      confidence=v.confidence, status=status, rationale=v.rationale))

    final_result = _resolve_choice_values(schema, MappingResult(answers=tuple(merged)))
    final_by_id = {a.question_id: a for a in final_result.answers}

    decisions = []
    for q in schema.questions:
        p = proposed_by_id.get(q.id)
        fa = final_by_id.get(q.id)
        decisions.append(DecisionRecord(
            question_id=q.id, label=q.label, type=q.type.value, required=q.required,
            profile_field=fa.profile_field if fa else None,
            value=fa.value if fa else None,
            propose_status=p.status if p else "no_data",
            propose_confidence=p.confidence if p else 0.0,
            propose_rationale=p.rationale if p else "",
            final_status=fa.status if fa else "no_data",
            final_confidence=fa.confidence if fa else 0.0,
            verify_rationale=fa.rationale if fa else "",
            final_action=_action_for(q, fa),
        ))
    return MappingOutcome(result=final_result, decisions=tuple(decisions))


def map_fields(
    client,
    deployment: str,
    schema: FormSchema,
    profile: Sequence[ProfileField],
    max_output_tokens: int = 16000,
    reasoning_effort: str = "medium",
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
        reasoning={"effort": reasoning_effort},
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
            rationale=a.rationale,
        )
        for a in parsed.answers
    )
    return _resolve_choice_values(schema, MappingResult(answers=answers))
