from __future__ import annotations

import json
import os
import time
from pathlib import Path


def load_ledger(path: str | Path) -> set[str]:
    """Ensemble des entry_id déjà traités. Fichier absent ou corrompu → set()
    (le déplacement Outlook reste le garde-fou visuel)."""
    p = Path(path)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 — corrompu : repartir vide
        print(f"[warn] registre {p} illisible; on repart d'une liste vide.")
        return set()


def save_ledger(path: str | Path, ids: set[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(ids), ensure_ascii=False), encoding="utf-8")


def acquire_lock(path: str | Path, stale_seconds: int) -> bool:
    """Prend le verrou. Retourne False si un verrou FRAIS existe déjà. Un verrou
    plus vieux que stale_seconds est considéré périmé et repris."""
    p = Path(path)
    if p.exists():
        age = time.time() - p.stat().st_mtime
        if age < stale_seconds:
            return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
