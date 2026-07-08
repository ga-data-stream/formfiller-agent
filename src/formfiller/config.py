from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    confidence_threshold: float = 0.8
    dry_run: bool = False
    excel_log_path: str
    review_queue_dir: str = "./review_queue"
    inbox_list_count: int = 25
    inbox_subfolder: str = ""   # blank → Inbox root; else a direct subfolder of the Inbox to read
    azure_openai_deployment: str = "gpt-4o"
    azure_api_version: str = "2024-10-21"
    # --- agent fill strategy (additive; deterministic stays the default) ---
    fill_strategy: Literal["deterministic", "agent"] = "deterministic"
    agent_model_deployment: str = ""   # blank → reuse azure_openai_deployment
    max_steps: int = 20
    no_progress_limit: int = 5
    traces_dir: str = "./traces"
    decisions_dir: str = "./decisions"
    mapping_verify: bool = True
    # --- batch + tri des mails (Plan A) ---
    processed_subfolder: str = "Traité"        # mails traités avec succès
    review_subfolder: str = "Revue humaine"    # manual + fail
    batch_lock_path: str = "./.batch.lock"
    lock_stale_seconds: int = 3600             # verrou plus vieux → considéré périmé
    processed_ledger_path: str = "./processed_ids.json"
    run_log_dir: str = "./logs"
    # Reasoning depth sent to the gpt-5 family on every Responses API call.
    # gpt-5.4 defaults to 'none' (no reasoning) unless set explicitly; 'xhigh'
    # is the deepest level (supported on gpt-5.1-codex-max and later).
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] = "medium"
    # Verifier (pass 2) may run on a different model / reasoning depth than the
    # proposer (pass 1). Blank → reuse azure_openai_deployment; None → reuse
    # reasoning_effort.
    verifier_model_deployment: str = ""
    verifier_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None


class ProfileField(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    value: str
    description: str = ""
    aliases: tuple[str, ...] = ()


def load_config(path: str | Path) -> AppConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppConfig(**data)


def load_profile(path: str | Path) -> tuple[ProfileField, ...]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return tuple(ProfileField(**f) for f in data["fields"])


def azure_v1_base_url(raw_endpoint: str) -> str:
    """Normalize a (possibly messy) Azure endpoint into the v1 base URL the
    OpenAI() client expects: 'https://<host>/openai/v1/'.

    Repairs a doubled-h scheme typo ('hhttps://') and strips any extra path
    (e.g. a pasted '/openai/v1/responses'), so a stray value in .env can't
    break the client.
    """
    e = (raw_endpoint or "").strip().strip('"').strip("'")
    if e.startswith("hhttp"):
        e = e[1:]
    if not e.startswith(("http://", "https://")):
        e = "https://" + e
    u = urlparse(e)
    scheme = "https" if u.scheme in ("", "hhttps") else u.scheme
    return f"{scheme}://{u.netloc}/openai/v1/"
