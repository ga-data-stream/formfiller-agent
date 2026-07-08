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
