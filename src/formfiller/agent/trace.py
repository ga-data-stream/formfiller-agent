from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TraceWriter:
    """Append-only JSONL writer, one file per run. Never raises on write
    (tracing must not crash a run)."""

    def __init__(self, traces_dir: str | Path, run_id: str) -> None:
        self.dir = Path(traces_dir)
        self.run_id = run_id
        self.path = self.dir / f"{run_id}.jsonl"
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 — tracing is best-effort
            print(f"[warn] trace write failed: {exc}")
