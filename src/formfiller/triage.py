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
