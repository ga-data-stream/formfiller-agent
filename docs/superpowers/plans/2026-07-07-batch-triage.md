# Plan A — Traitement à la chaîne + tri des mails — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un mode batch non-interactif qui traite à la chaîne tous les mails de `ligne adressage`, réutilise le pipeline existant, et déplace chaque mail vers un sous-dossier Outlook `Traité` / `Revue humaine` selon le résultat.

**Architecture:** Un module `batch.py` orchestre une boucle sur les mails, déléguant le traitement d'un mail au pipeline **existant** (`process_email` / `run_agent_pipeline`) et le routage à un module pur `triage.py`. L'idempotence repose sur un registre d'`entry_id` (`batch_state.py`) écrit **avant** le déplacement, plus un verrou anti-chevauchement. `OutlookEmailSource` gagne `move_to_subfolder`. La CLI interactive reste inchangée.

**Tech Stack:** Python ≥3.11, Pydantic v2, pytest, pywin32 (COM Outlook, non testé en CI).

## Global Constraints

- Python ≥ 3.11 (`requires-python = ">=3.11"`).
- Toute dépendance applicative va dans `pyproject.toml` — jamais ailleurs.
- **Ne jamais lire, logguer ou afficher** `.env`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`.
- **Ne jamais committer** `config.yaml` (overrides runtime locaux) ni `.env`.
- Travailler sur la branche `feat/batch-triage-deployment` (déjà créée) — jamais sur `main`.
- Changement **additif** : la CLI interactive (`formfiller`) et tous les tests existants doivent continuer à passer.
- Tout nouveau champ `AppConfig` a un **défaut rétrocompatible**.
- Statuts de `JobResult` : `"success"` | `"manual"` | `"fail"` (exact, verbatim).
- `pytest` doit passer à la fin de chaque tâche.

---

### Task 1: Nouveaux champs de configuration

**Files:**
- Modify: `src/formfiller/config.py` (classe `AppConfig`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `AppConfig.processed_subfolder: str = "Traité"`, `AppConfig.review_subfolder: str = "Revue humaine"`, `AppConfig.batch_lock_path: str = "./.batch.lock"`, `AppConfig.lock_stale_seconds: int = 3600`, `AppConfig.processed_ledger_path: str = "./processed_ids.json"`, `AppConfig.run_log_dir: str = "./logs"`.

- [ ] **Step 1: Write the failing test**

Ajouter à la fin de `tests/test_config.py` :

```python
def test_appconfig_batch_defaults():
    from formfiller.config import AppConfig
    cfg = AppConfig(excel_log_path="x.xlsx")
    assert cfg.processed_subfolder == "Traité"
    assert cfg.review_subfolder == "Revue humaine"
    assert cfg.batch_lock_path == "./.batch.lock"
    assert cfg.lock_stale_seconds == 3600
    assert cfg.processed_ledger_path == "./processed_ids.json"
    assert cfg.run_log_dir == "./logs"


def test_appconfig_batch_overrides():
    from formfiller.config import AppConfig
    cfg = AppConfig(
        excel_log_path="x.xlsx",
        processed_subfolder="Fait", review_subfolder="À revoir",
        batch_lock_path="./lock", lock_stale_seconds=60,
        processed_ledger_path="./ids.json", run_log_dir="./l",
    )
    assert cfg.processed_subfolder == "Fait"
    assert cfg.review_subfolder == "À revoir"
    assert cfg.lock_stale_seconds == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_appconfig_batch_defaults -v`
Expected: FAIL — `AttributeError`/`AssertionError` (champs inexistants).

- [ ] **Step 3: Write minimal implementation**

Dans `src/formfiller/config.py`, ajouter à la classe `AppConfig` (après `traces_dir`/`decisions_dir`, avant les champs `reasoning_effort`) :

```python
    # --- batch + tri des mails (Plan A) ---
    processed_subfolder: str = "Traité"        # mails traités avec succès
    review_subfolder: str = "Revue humaine"    # manual + fail
    batch_lock_path: str = "./.batch.lock"
    lock_stale_seconds: int = 3600             # verrou plus vieux → considéré périmé
    processed_ledger_path: str = "./processed_ids.json"
    run_log_dir: str = "./logs"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (nouveaux tests + tous les tests existants).

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/config.py tests/test_config.py
git commit -m "feat(config): champs de config pour le batch + tri"
```

---

### Task 2: `move_to_subfolder` sur la source d'emails

**Files:**
- Modify: `src/formfiller/email_source.py`
- Test: `tests/test_email_source.py`

**Interfaces:**
- Produces:
  - Protocole `EmailSource.move_to_subfolder(self, entry_id: str, name: str) -> bool`.
  - `FakeEmailSource(messages, *, move_fails: bool = False)` avec attribut `moves: list[tuple[str, str]]`.
  - Helper module-level `_resolve_or_create(parent, name) -> folder`.
  - `OutlookEmailSource.move_to_subfolder(entry_id, name) -> bool`.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_email_source.py` :

```python
def test_fake_source_move_records_and_returns_true():
    source = FakeEmailSource([_msg("E1", "x")])
    assert source.move_to_subfolder("E1", "Traité") is True
    assert source.moves == [("E1", "Traité")]


def test_fake_source_move_can_simulate_failure():
    source = FakeEmailSource([_msg("E1", "x")], move_fails=True)
    assert source.move_to_subfolder("E1", "Traité") is False
    assert source.moves == []


class _FakeFolders(list):
    """Liste de dossiers qui sait en créer un nouveau (comme Outlook Folders.Add)."""
    def Add(self, name):
        f = _FakeFolder(name)
        self.append(f)
        return f


def test_resolve_or_create_returns_existing():
    from formfiller.email_source import _resolve_or_create
    existing = _FakeFolder("Traité")
    parent = _FakeFolder("Inbox")
    parent.Folders = _FakeFolders([_FakeFolder("Autre"), existing])
    assert _resolve_or_create(parent, "traité") is existing   # insensible à la casse


def test_resolve_or_create_creates_when_absent():
    from formfiller.email_source import _resolve_or_create
    parent = _FakeFolder("Inbox")
    parent.Folders = _FakeFolders([_FakeFolder("Autre")])
    created = _resolve_or_create(parent, "Revue humaine")
    assert created.Name == "Revue humaine"
    assert any(f.Name == "Revue humaine" for f in parent.Folders)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_email_source.py::test_fake_source_move_records_and_returns_true -v`
Expected: FAIL — `AttributeError: 'FakeEmailSource' object has no attribute 'move_to_subfolder'`.

- [ ] **Step 3: Write minimal implementation**

Dans `src/formfiller/email_source.py` :

Ajouter la méthode au `Protocol` `EmailSource` (après `get`) :

```python
    def move_to_subfolder(self, entry_id: str, name: str) -> bool:
        ...
```

Remplacer `FakeEmailSource.__init__` et ajouter la méthode :

```python
    def __init__(self, messages: list[EmailMessage], *, move_fails: bool = False):
        self._messages = list(messages)
        self.moves: list[tuple[str, str]] = []
        self._move_fails = move_fails
```

```python
    def move_to_subfolder(self, entry_id: str, name: str) -> bool:
        if self._move_fails:
            return False
        self.moves.append((entry_id, name))
        return True
```

Ajouter le helper module-level (près de `_resolve_subfolder`) :

```python
def _resolve_or_create(parent, name: str):
    """Retourne le sous-dossier `name` (insensible à la casse) sous `parent`,
    en le créant via `Folders.Add` s'il n'existe pas encore."""
    for f in parent.Folders:
        if str(f.Name).casefold() == name.casefold():
            return f
    return parent.Folders.Add(name)
```

Ajouter la méthode COM à `OutlookEmailSource` :

```python
    def move_to_subfolder(self, entry_id: str, name: str) -> bool:
        """Déplace le mail `entry_id` vers un sous-dossier frère du dossier
        source (créé si absent). Retourne False sur toute erreur COM (jamais
        d'exception propagée : le batch doit continuer)."""
        try:
            target = _resolve_or_create(self._folder.Parent, name)
            item = self._folder.Session.GetItemFromID(entry_id)
            item.Move(target)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] déplacement de {entry_id} vers {name!r} impossible: {exc}")
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_source.py -v`
Expected: PASS (nouveaux + existants ; `FakeEmailSource` reste conforme au Protocol).

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/email_source.py tests/test_email_source.py
git commit -m "feat(email): move_to_subfolder (Fake + Outlook COM)"
```

---

### Task 3: Module de routage `triage.py`

**Files:**
- Create: `src/formfiller/triage.py`
- Test: `tests/test_triage.py`

**Interfaces:**
- Consumes: `AppConfig.processed_subfolder`, `AppConfig.review_subfolder` (Task 1) ; `EmailSource.move_to_subfolder` (Task 2).
- Produces: `target_subfolder(status: str, config: AppConfig) -> str` ; `route(source: EmailSource, entry_id: str, status: str, config: AppConfig) -> bool`.

- [ ] **Step 1: Write the failing test**

Créer `tests/test_triage.py` :

```python
from formfiller import triage
from formfiller.config import AppConfig
from formfiller.email_source import FakeEmailSource
from formfiller.models import EmailMessage


def _cfg():
    return AppConfig(excel_log_path="x.xlsx",
                     processed_subfolder="Traité", review_subfolder="Revue humaine")


def _msg(entry_id):
    return EmailMessage(entry_id=entry_id, sender="a@b.com", subject="s",
                        received="2026-07-07T09:00:00", body_text="t", body_html="<p>t</p>")


def test_target_subfolder_success_goes_to_processed():
    assert triage.target_subfolder("success", _cfg()) == "Traité"


def test_target_subfolder_manual_and_fail_go_to_review():
    assert triage.target_subfolder("manual", _cfg()) == "Revue humaine"
    assert triage.target_subfolder("fail", _cfg()) == "Revue humaine"


def test_route_moves_to_target_and_returns_result():
    source = FakeEmailSource([_msg("E1")])
    assert triage.route(source, "E1", "success", _cfg()) is True
    assert source.moves == [("E1", "Traité")]


def test_route_propagates_move_failure():
    source = FakeEmailSource([_msg("E1")], move_fails=True)
    assert triage.route(source, "E1", "manual", _cfg()) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_triage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'formfiller.triage'`.

- [ ] **Step 3: Write minimal implementation**

Créer `src/formfiller/triage.py` :

```python
from __future__ import annotations

from formfiller.config import AppConfig
from formfiller.email_source import EmailSource


def target_subfolder(status: str, config: AppConfig) -> str:
    """Dossier Outlook cible pour un statut de JobResult.

    success → dossier « traité » ; manual/fail → dossier « revue humaine ».
    """
    if status == "success":
        return config.processed_subfolder
    return config.review_subfolder


def route(source: EmailSource, entry_id: str, status: str, config: AppConfig) -> bool:
    """Déplace le mail vers son dossier cible. Retourne True si déplacé."""
    return source.move_to_subfolder(entry_id, target_subfolder(status, config))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_triage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/triage.py tests/test_triage.py
git commit -m "feat(triage): routage statut -> sous-dossier Outlook"
```

---

### Task 4: État du batch — registre + verrou (`batch_state.py`)

**Files:**
- Create: `src/formfiller/batch_state.py`
- Test: `tests/test_batch_state.py`

**Interfaces:**
- Produces: `load_ledger(path) -> set[str]` ; `save_ledger(path, ids: set[str]) -> None` ; `acquire_lock(path, stale_seconds: int) -> bool` ; `release_lock(path) -> None`.

- [ ] **Step 1: Write the failing test**

Créer `tests/test_batch_state.py` :

```python
import os
import time
from formfiller.batch_state import load_ledger, save_ledger, acquire_lock, release_lock


def test_load_ledger_missing_returns_empty(tmp_path):
    assert load_ledger(tmp_path / "nope.json") == set()


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "ids.json"
    save_ledger(p, {"E1", "E2"})
    assert load_ledger(p) == {"E1", "E2"}


def test_load_ledger_corrupt_returns_empty(tmp_path):
    p = tmp_path / "ids.json"
    p.write_text("{ not json", encoding="utf-8")
    assert load_ledger(p) == set()


def test_acquire_lock_on_free_path_succeeds(tmp_path):
    p = tmp_path / ".lock"
    assert acquire_lock(p, stale_seconds=3600) is True
    assert p.exists()


def test_acquire_lock_fails_when_fresh_lock_present(tmp_path):
    p = tmp_path / ".lock"
    assert acquire_lock(p, stale_seconds=3600) is True
    assert acquire_lock(p, stale_seconds=3600) is False   # 2e run concurrent


def test_acquire_lock_overrides_stale_lock(tmp_path):
    p = tmp_path / ".lock"
    p.write_text("123", encoding="utf-8")
    old = time.time() - 10_000
    os.utime(p, (old, old))
    assert acquire_lock(p, stale_seconds=3600) is True   # verrou périmé -> repris


def test_release_lock_removes_file_and_tolerates_missing(tmp_path):
    p = tmp_path / ".lock"
    acquire_lock(p, stale_seconds=3600)
    release_lock(p)
    assert not p.exists()
    release_lock(p)   # ne lève pas
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batch_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'formfiller.batch_state'`.

- [ ] **Step 3: Write minimal implementation**

Créer `src/formfiller/batch_state.py` :

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_batch_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/batch_state.py tests/test_batch_state.py
git commit -m "feat(batch): registre d'idempotence + verrou anti-chevauchement"
```

---

### Task 5: Cœur du runner `run_batch`

**Files:**
- Create: `src/formfiller/batch.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Consumes: `triage.route`, `triage.target_subfolder` (Task 3) ; `load_ledger`, `save_ledger` (Task 4) ; `EmailSource` (Task 2) ; `AppConfig` (Task 1).
- Produces: `BatchSummary(processed, review, failed, not_moved, skipped)` (dataclass frozen, défauts 0) ; `run_batch(*, source, process: Callable[[EmailMessage], JobResult], config: AppConfig, log: Callable[[str], None]) -> BatchSummary`. `run_batch` charge/écrit le registre lui-même ; il **n'acquiert pas** le verrou (c'est `main`, Task 6).

- [ ] **Step 1: Write the failing test**

Créer `tests/test_batch.py` :

```python
from formfiller.batch import run_batch, BatchSummary
from formfiller.batch_state import load_ledger, save_ledger
from formfiller.config import AppConfig
from formfiller.email_source import FakeEmailSource
from formfiller.models import EmailMessage


class _Result:
    def __init__(self, status): self.status = status


def _msg(entry_id):
    return EmailMessage(entry_id=entry_id, sender="a@b.com", subject="s",
                        received="2026-07-07T09:00:00", body_text="t", body_html="<p>t</p>")


def _cfg(tmp_path):
    return AppConfig(excel_log_path=str(tmp_path / "log.xlsx"),
                     processed_ledger_path=str(tmp_path / "ids.json"),
                     inbox_list_count=10,
                     processed_subfolder="Traité", review_subfolder="Revue humaine")


def _process_from(mapping):
    return lambda email: _Result(mapping[email.entry_id])


def test_run_batch_routes_each_status_to_its_folder(tmp_path):
    source = FakeEmailSource([_msg("E1"), _msg("E2"), _msg("E3")])
    process = _process_from({"E1": "success", "E2": "manual", "E3": "fail"})
    summary = run_batch(source=source, process=process, config=_cfg(tmp_path), log=lambda m: None)
    assert summary.processed == 1 and summary.review == 1 and summary.failed == 1
    assert ("E1", "Traité") in source.moves
    assert ("E2", "Revue humaine") in source.moves
    assert ("E3", "Revue humaine") in source.moves


def test_run_batch_isolates_exceptions_as_fail(tmp_path):
    source = FakeEmailSource([_msg("E1"), _msg("E2")])
    def process(email):
        if email.entry_id == "E1":
            raise RuntimeError("boom")
        return _Result("success")
    summary = run_batch(source=source, process=process, config=_cfg(tmp_path), log=lambda m: None)
    assert summary.failed == 1 and summary.processed == 1
    assert ("E1", "Revue humaine") in source.moves   # l'exception -> revue humaine


def test_run_batch_skips_entries_already_in_ledger(tmp_path):
    cfg = _cfg(tmp_path)
    save_ledger(cfg.processed_ledger_path, {"E1"})
    source = FakeEmailSource([_msg("E1"), _msg("E2")])
    process = _process_from({"E1": "success", "E2": "success"})
    summary = run_batch(source=source, process=process, config=cfg, log=lambda m: None)
    assert summary.skipped == 1 and summary.processed == 1
    assert source.moves == [("E2", "Traité")]         # E1 non retraité


def test_run_batch_records_ledger_after_processing(tmp_path):
    cfg = _cfg(tmp_path)
    source = FakeEmailSource([_msg("E1")])
    run_batch(source=source, process=_process_from({"E1": "success"}), config=cfg, log=lambda m: None)
    assert "E1" in load_ledger(cfg.processed_ledger_path)


def test_run_batch_move_failure_still_ledgers_so_no_reprocess(tmp_path):
    cfg = _cfg(tmp_path)
    source = FakeEmailSource([_msg("E1")], move_fails=True)
    summary = run_batch(source=source, process=_process_from({"E1": "success"}), config=cfg, log=lambda m: None)
    assert summary.not_moved == 1
    assert "E1" in load_ledger(cfg.processed_ledger_path)   # filet anti-double-soumission
    # un 2e run ne retraite pas E1
    source2 = FakeEmailSource([_msg("E1")])
    summary2 = run_batch(source=source2, process=_process_from({"E1": "success"}), config=cfg, log=lambda m: None)
    assert summary2.skipped == 1 and summary2.processed == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'formfiller.batch'`.

- [ ] **Step 3: Write minimal implementation**

Créer `src/formfiller/batch.py` (cœur uniquement — `main` en Task 6) :

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from formfiller import triage
from formfiller.batch_state import load_ledger, save_ledger
from formfiller.config import AppConfig
from formfiller.email_source import EmailSource
from formfiller.models import EmailMessage
from formfiller.result_logger import JobResult


@dataclass(frozen=True)
class BatchSummary:
    processed: int = 0
    review: int = 0
    failed: int = 0
    not_moved: int = 0
    skipped: int = 0


def run_batch(*, source: EmailSource,
              process: Callable[[EmailMessage], JobResult],
              config: AppConfig,
              log: Callable[[str], None]) -> BatchSummary:
    """Traite à la chaîne les mails de la source, route chacun vers son
    sous-dossier, et retient les entry_id traités (registre). Le verrou est géré
    par l'appelant (main)."""
    ledger = load_ledger(config.processed_ledger_path)
    processed = review = failed = not_moved = skipped = 0

    for email in source.list_recent(config.inbox_list_count):
        if email.entry_id in ledger:
            skipped += 1
            log(f"[skip] {email.entry_id} déjà traité")
            continue

        try:
            status = process(email).status
        except Exception as exc:  # noqa: BLE001 — isoler un mail défaillant
            log(f"[error] {email.entry_id}: {exc}")
            status = "fail"

        # Écrire au registre AVANT le déplacement : même si le move échoue, le
        # mail ne sera pas rejoué (donc pas de formulaire soumis deux fois).
        ledger.add(email.entry_id)
        save_ledger(config.processed_ledger_path, ledger)

        moved = triage.route(source, email.entry_id, status, config)
        target = triage.target_subfolder(status, config)

        if status == "success":
            processed += 1
        elif status == "fail":
            failed += 1
        else:  # "manual"
            review += 1
        if not moved:
            not_moved += 1

        suffix = "" if moved else " (NON DÉPLACÉ)"
        log(f"[{status}] {email.entry_id} → {target}{suffix}")

    summary = BatchSummary(processed=processed, review=review, failed=failed,
                           not_moved=not_moved, skipped=skipped)
    log(f"Récap: {processed} traités, {review} revue, {failed} échecs, "
        f"{not_moved} non-déplacés, {skipped} sautés")
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_batch.py -v`
Expected: PASS (les 5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/formfiller/batch.py tests/test_batch.py
git commit -m "feat(batch): coeur run_batch (boucle, tri, idempotence, récap)"
```

---

### Task 6: Point d'entrée `main` + mode non-interactif + script console

**Files:**
- Modify: `src/formfiller/batch.py` (ajout de `main`, `_build_process`, `_auto_confirm`, `_make_logger`)
- Modify: `src/formfiller/cli.py` (paramètre `confirm` sur `build_agent_run` et `_build_agent_deps`)
- Modify: `pyproject.toml` (`[project.scripts]`)
- Test: `tests/test_batch.py` (ajouts) ; `tests/test_cli_agent.py` (ajout signature)

**Interfaces:**
- Consumes: `run_batch` (Task 5) ; `cli._build_hooks`, `cli._build_agent_deps`, `cli.choose_pipeline` ; `orchestrator.process_email` ; `agent.pipeline.run_agent_pipeline` ; `email_source.OutlookEmailSource` ; `batch_state.acquire_lock/release_lock`.
- Produces: `batch.main() -> int` (entrée console `formfiller-batch`) ; `_auto_confirm(summary: str) -> bool` (toujours `True`) ; `cli.build_agent_run(*, client, config, profile, confirm=_terminal_confirm)` ; `cli._build_agent_deps(config, profile, confirm=_terminal_confirm)`.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_batch.py` :

```python
def test_auto_confirm_always_true():
    from formfiller.batch import _auto_confirm
    assert _auto_confirm("prêt à soumettre") is True


def test_build_agent_run_accepts_confirm_param():
    import inspect
    from formfiller.cli import build_agent_run
    params = inspect.signature(build_agent_run).parameters
    assert "confirm" in params   # paramétrable pour le mode non-interactif
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batch.py::test_auto_confirm_always_true tests/test_batch.py::test_build_agent_run_accepts_confirm_param -v`
Expected: FAIL — `ImportError`/`AttributeError` (`_auto_confirm` absent) puis `AssertionError` (`confirm` absent de la signature).

- [ ] **Step 3: Write minimal implementation**

**3a — `src/formfiller/cli.py`** : rendre le `confirm` paramétrable (défaut = comportement interactif actuel).

Remplacer la signature de `build_agent_run` :

```python
def build_agent_run(*, client, config, profile, confirm=None):
    """Build the AgentDeps.run callable. `confirm` gates irreversible submits;
    defaults to the interactive terminal prompt (batch mode injects auto-True)."""
    if confirm is None:
        confirm = _terminal_confirm
```

Dans le corps de `run(...)`, remplacer `confirm=_terminal_confirm` par `confirm=confirm` dans la construction de `ToolExecutor`.

Remplacer la signature et l'appel dans `_build_agent_deps` :

```python
def _build_agent_deps(config, profile, confirm=None):
    """Production AgentDeps: real Playwright page + Azure client."""
    import os
    from openai import OpenAI
    from formfiller.agent.pipeline import AgentDeps
    from formfiller.form_reader import open_page

    client = OpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"],
                    base_url=azure_v1_base_url(os.environ["AZURE_OPENAI_ENDPOINT"]),
                    default_query={"api-version": config.azure_api_version})
    run = build_agent_run(client=client, config=config, profile=profile, confirm=confirm)
```

(le reste de `_build_agent_deps` inchangé.)

**3b — `src/formfiller/batch.py`** : ajouter en tête des imports (haut du fichier) rien de plus ; ajouter à la fin du fichier :

```python
def _auto_confirm(summary: str) -> bool:
    """Mode batch non-interactif : confirmer toute soumission (cohérent avec
    l'auto-soumission au-dessus du seuil de confiance)."""
    return True


def _make_logger(run_log_dir: str) -> Callable[[str], None]:
    """Logger qui écrit sur stdout ET dans logs/batch-<horodatage>.log."""
    from datetime import datetime
    from pathlib import Path
    Path(run_log_dir).mkdir(parents=True, exist_ok=True)
    logfile = Path(run_log_dir) / f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

    def log(msg: str) -> None:
        print(msg)
        with logfile.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")

    return log


def _build_process(config: AppConfig, profile) -> Callable[[EmailMessage], JobResult]:
    """Construit la fonction process(email)->JobResult à partir de la config,
    en réutilisant le câblage de cli.py, avec confirmation auto (non-interactif)."""
    from formfiller.cli import _build_hooks, _build_agent_deps, choose_pipeline
    from formfiller.orchestrator import process_email

    if choose_pipeline(config.fill_strategy) == "agent":
        from formfiller.agent.pipeline import run_agent_pipeline
        det_hooks = _build_hooks(config, profile)
        agent_deps = _build_agent_deps(config, profile, confirm=_auto_confirm)
        return lambda email: run_agent_pipeline(email, config, profile, agent_deps, det_hooks)

    hooks = _build_hooks(config, profile)
    return lambda email: process_email(email, config, profile, hooks)


def main() -> int:
    from pathlib import Path
    from dotenv import load_dotenv
    from formfiller.config import load_config, load_profile
    from formfiller.email_source import OutlookEmailSource
    from formfiller.batch_state import acquire_lock, release_lock

    load_dotenv()
    root = Path.cwd()
    config = load_config(root / "config.yaml")
    profile = load_profile(root / "profile.yaml")

    if not acquire_lock(config.batch_lock_path, config.lock_stale_seconds):
        print("Un batch est déjà en cours (verrou présent). Sortie.")
        return 0
    try:
        log = _make_logger(config.run_log_dir)
        if config.dry_run:
            log("(dry-run : formulaires remplis mais NON soumis)")
        source = OutlookEmailSource(subfolder=config.inbox_subfolder)
        process = _build_process(config, profile)
        run_batch(source=source, process=process, config=config, log=log)
    finally:
        release_lock(config.batch_lock_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**3c — `pyproject.toml`** : ajouter l'entrée console sous `[project.scripts]` :

```toml
[project.scripts]
formfiller = "formfiller.cli:main"
formfiller-batch = "formfiller.batch:main"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_batch.py tests/test_cli.py tests/test_cli_agent.py -v`
Expected: PASS (nouveaux + CLI interactive inchangée).

- [ ] **Step 5: Full suite + register the console script**

Run: `pytest`
Expected: toute la suite PASS.

Run: `pip install -e ".[dev]"`
Expected: la commande `formfiller-batch` est enregistrée (vérifier `formfiller-batch` importable ; l'exécution réelle nécessite Outlook + Azure, donc validée au smoke test du Plan B).

- [ ] **Step 6: Commit**

```bash
git add src/formfiller/batch.py src/formfiller/cli.py pyproject.toml tests/test_batch.py tests/test_cli_agent.py
git commit -m "feat(batch): main non-interactif, confirm paramétrable, script formfiller-batch"
```

---

## Self-Review

**Spec coverage** (contre `2026-07-07-batch-triage-design.md`) :
- §1 batch runner → Tasks 5 + 6. §2 triage → Task 3. §3 move_to_subfolder + protocole → Task 2. §4 mode non-interactif (auto-confirm, pas de picker) → Task 6. §5 config → Task 1. §6 idempotence (registre avant move) + verrou → Tasks 4 + 5 + 6. §7 observabilité (log par run, récap) → Tasks 5 (récap) + 6 (`_make_logger`). Excel/review_queue → déjà produits par le pipeline réutilisé. ✅
- Table de gestion d'erreur : exception par mail (Task 5 test `isolates_exceptions`), échec de déplacement (Task 5 test `move_failure_still_ledgers`), verrou frais (Task 4 test `fails_when_fresh`), registre corrompu (Task 4 test `corrupt_returns_empty`). ✅

**Placeholder scan** : aucun TBD/TODO ; chaque étape de code montre le code complet.

**Type consistency** : `move_to_subfolder(entry_id, name) -> bool` (Tasks 2/3/5) ; `route`/`target_subfolder(status, config)` (Tasks 3/5) ; `run_batch(*, source, process, config, log)` et `BatchSummary(processed, review, failed, not_moved, skipped)` (Tasks 5/6) ; `build_agent_run(..., confirm=...)` / `_build_agent_deps(..., confirm=...)` (Task 6). Cohérents. Statuts `success|manual|fail` verbatim.

## Notes d'exécution

- Ce plan est **additif** ; il touche > 3 fichiers → il tient lieu de plan validé (règle CLAUDE.md).
- `config.yaml` réel (prod) est traité par le **Plan B** ; ici on ne modifie pas `config.yaml`.
- Prérequis d'exécution réelle (Outlook, Azure) → hors CI, validés par le smoke test du Plan B.
