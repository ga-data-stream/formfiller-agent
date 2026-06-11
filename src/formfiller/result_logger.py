from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from pydantic import BaseModel, ConfigDict

COLUMNS = (
    "timestamp",
    "sender",
    "client_name",
    "form_url",
    "form_type",
    "status",
    "overall_confidence",
    "fields_filled",
    "fields_blank_flagged",
    "review_reason",
    "screenshot_path",
)


class JobResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    timestamp: str
    sender: str
    client_name: str
    form_url: str
    form_type: str
    status: str  # "success" | "manual" | "fail"
    overall_confidence: float
    fields_filled: int
    fields_blank_flagged: str
    review_reason: str
    screenshot_path: str


def append_result(path: str | Path, result: JobResult) -> None:
    """Append one outcome row to the Excel log, creating it with a header row
    if it does not yet exist. Writes to the local (synced) copy of the file."""
    path = Path(path)
    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = "log"
        ws.append(list(COLUMNS))

    ws.append([getattr(result, col) for col in COLUMNS])
    wb.save(path)
