from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from formfiller.models import DecisionRecord


def _safe(name: str) -> str:
    stamp = "".join(c if c.isalnum() else "_" for c in name)
    return stamp or "form"


def write_decisions_md(decisions_dir, entry_id: str, form_title: str,
                       form_url: str,
                       decisions: Sequence[DecisionRecord]) -> Optional[Path]:
    """Write one human-readable markdown file per form capturing both passes'
    reasoning. Best-effort: never raises; returns the path written or None."""
    try:
        d = Path(decisions_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_safe(entry_id)}.md"
        lines = [f"# {form_title}", "", f"<{form_url}>", ""]
        for r in decisions:
            lines += [
                f"## {r.label}",
                f"- **field:** `{r.profile_field}`  **value:** {r.value!r}",
                f"- **action:** {r.final_action}  "
                f"(status: {r.final_status}, confidence {r.final_confidence:.2f})",
                f"- **propose:** {r.propose_rationale} "
                f"(status: {r.propose_status}, confidence {r.propose_confidence:.2f})",
                f"- **verify:** {r.verify_rationale}",
                "",
            ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except Exception as exc:  # noqa: BLE001 — logging must not crash a run
        print(f"[warn] decisions log write failed: {exc}")
        return None
