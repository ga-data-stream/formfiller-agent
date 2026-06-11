from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    confidence_threshold: float = 0.8
    dry_run: bool = False
    excel_log_path: str
    review_queue_dir: str = "./review_queue"
    inbox_list_count: int = 25
    azure_openai_deployment: str = "gpt-4o"
    azure_api_version: str = "2024-10-21"


class ProfileField(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    value: str
    aliases: tuple[str, ...] = ()


def load_config(path: str | Path) -> AppConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return AppConfig(**data)


def load_profile(path: str | Path) -> tuple[ProfileField, ...]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return tuple(ProfileField(**f) for f in data["fields"])
