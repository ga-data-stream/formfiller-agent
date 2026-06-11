from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from formfiller.models import FormSchema, MappingResult


def park_for_review(
    queue_dir: str | Path,
    job_id: str,
    schema: FormSchema,
    result: MappingResult,
    reason: str,
    screenshot_bytes: Optional[bytes],
) -> Path:
    """Persist a held job to `<queue_dir>/<job_id>/` for a human to finish.

    Writes payload.json (form URL, schema, proposed answers, hold reason) and,
    when provided, screenshot.png of the filled-but-unsubmitted form.
    Returns the per-job directory.
    """
    out_dir = Path(queue_dir) / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "job_id": job_id,
        "reason": reason,
        "form_url": schema.url,
        "form_title": schema.title,
        "questions": [q.model_dump() for q in schema.questions],
        "answers": [a.model_dump() for a in result.answers],
    }
    (out_dir / "payload.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if screenshot_bytes is not None:
        (out_dir / "screenshot.png").write_bytes(screenshot_bytes)

    return out_dir
