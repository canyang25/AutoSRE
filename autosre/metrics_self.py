"""In-process counters for agent / webhook self-observability."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class AgentMetrics:
    """Simple process-local metrics (Stage 2). Export to Prometheus later."""

    incidents_accepted: int = 0
    incidents_resolved: int = 0
    incidents_failed: int = 0
    incidents_denied: int = 0
    remediations_allowed: int = 0
    remediations_blocked: int = 0
    webhook_rejected_auth: int = 0
    webhook_rejected_rate: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + n)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {
                "incidents_accepted": self.incidents_accepted,
                "incidents_resolved": self.incidents_resolved,
                "incidents_failed": self.incidents_failed,
                "incidents_denied": self.incidents_denied,
                "remediations_allowed": self.remediations_allowed,
                "remediations_blocked": self.remediations_blocked,
                "webhook_rejected_auth": self.webhook_rejected_auth,
                "webhook_rejected_rate": self.webhook_rejected_rate,
            }


METRICS = AgentMetrics()
