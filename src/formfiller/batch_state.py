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


def save_ledger(path: str | Path, ids: set[str], retries: int = 3, delay: float = 0.5) -> None:
    """Écrit le registre anti-double-soumission. Résilient à un verrou passager
    (OneDrive/AV sur le fichier synchronisé) : retente brièvement, puis lève
    l'erreur si ça persiste — l'appelant doit alors arrêter le batch plutôt que
    de continuer sans garantie d'idempotence (mirrors result_logger.append_result)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(sorted(ids), ensure_ascii=False)
    last_exc: OSError | None = None
    for attempt in range(retries):
        try:
            p.write_text(payload, encoding="utf-8")
            return
        except OSError as exc:               # locked by sync/AV/editor — retry briefly
            last_exc = exc
            if attempt < retries - 1 and delay:
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def acquire_lock(path: str | Path, stale_seconds: int) -> bool:
    """Prend le verrou de façon atomique (os.O_CREAT | os.O_EXCL). Retourne False si
    un verrou FRAIS existe déjà. Un verrou plus vieux que stale_seconds est considéré
    périmé et repris — sauf si un autre processus le reprend entre-temps, auquel cas
    on renonce (False) plutôt que d'écraser son verrou."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - p.stat().st_mtime
        except FileNotFoundError:
            return False   # disparu entre-temps ; le prochain run réessaiera
        if age < stale_seconds:
            return False
        # périmé -> on reprend
        try:
            p.unlink()
        except FileNotFoundError:
            pass                # déjà supprimé → on peut recréer
        except OSError:
            return False        # un autre process le détient (ex. Windows WinError 32) → on renonce
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False   # un autre processus l'a repris entre-temps
    with os.fdopen(fd, "w") as fh:
        fh.write(str(os.getpid()))
    return True


def release_lock(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
