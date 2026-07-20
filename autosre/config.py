"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass
class AutoSREConfig:
    """Runtime configuration for AutoSRE."""

    prometheus_url: str = "http://localhost:9091"
    elk_url: str = "http://localhost:9093"
    ansible_url: str = "http://localhost:9092"
    approval_mode: str = "auto"  # auto | prompt | webhook
    approval_webhook_url: str = ""
    timeout: int = 300
    fallback_chain: List[str] = field(default_factory=list)
    rollback_playbook: str = ""
    port: int = 8080
    db_path: str = "autosre.db"
    max_iterations: int = 12

    @classmethod
    def from_env(cls) -> "AutoSREConfig":
        chain_raw = _env("LLM_FALLBACK_CHAIN", "")
        chain = [p.strip().lower() for p in chain_raw.split(",") if p.strip()]
        return cls(
            prometheus_url=_env("PROMETHEUS_URL", "http://localhost:9091").rstrip("/"),
            elk_url=_env("ELK_URL", "http://localhost:9093").rstrip("/"),
            ansible_url=_env("ANSIBLE_URL", "http://localhost:9092").rstrip("/"),
            approval_mode=_env("AUTOSRE_APPROVAL_MODE", "auto").lower() or "auto",
            approval_webhook_url=_env("AUTOSRE_APPROVAL_WEBHOOK_URL", ""),
            timeout=int(_env("AUTOSRE_TIMEOUT", "300") or "300"),
            fallback_chain=chain,
            rollback_playbook=_env("AUTOSRE_ROLLBACK_PLAYBOOK", ""),
            port=int(_env("AUTOSRE_PORT", "8080") or "8080"),
            db_path=_env("AUTOSRE_DB_PATH", "autosre.db") or "autosre.db",
            max_iterations=int(_env("AUTOSRE_MAX_ITERATIONS", "12") or "12"),
        )


# Module-level singleton refreshed from env on import; callers may rebuild via from_env().
config = AutoSREConfig.from_env()
