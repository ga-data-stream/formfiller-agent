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
