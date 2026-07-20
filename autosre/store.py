"""SQLite incident history store."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional

logger = logging.getLogger(__name__)

# Schema for the incidents table.
INCIDENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL,
    service TEXT,
    scenario TEXT,
    severity TEXT,
    status TEXT NOT NULL DEFAULT 'resolved',
    report_path TEXT,
    report_text TEXT,
    backend TEXT,
    model TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_alert_id ON incidents(alert_id);
CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents(created_at);
"""


class IncidentStore:
    """Persist and query incident records in SQLite."""

    def __init__(self, db_path: str = "autosre.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True) if Path(db_path).parent != Path(".") else None
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(INCIDENTS_SCHEMA)

    def save_incident(
        self,
        *,
        alert_id: str,
        service: str = "",
        scenario: str = "",
        severity: str = "",
        status: str = "resolved",
        report_path: str = "",
        report_text: str = "",
        backend: str = "",
        model: str = "",
        duration_ms: Optional[int] = None,
        metadata: Optional[dict] = None,
        created_at: Optional[str] = None,
    ) -> int:
        """Insert an incident row and return its id."""
        ts = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta_json = json.dumps(metadata or {})
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO incidents (
                    alert_id, service, scenario, severity, status,
                    report_path, report_text, backend, model, duration_ms,
                    created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert_id,
                    service,
                    scenario,
                    severity,
                    status,
                    report_path,
                    report_text,
                    backend,
                    model,
                    duration_ms,
                    ts,
                    meta_json,
                ),
            )
            incident_id = int(cur.lastrowid)
        logger.info("Saved incident id=%s alert_id=%s", incident_id, alert_id)
        return incident_id

    def get_incident(self, incident_id: int) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_history(self, limit: int = 50, alert_id: Optional[str] = None) -> List[dict]:
        with self._conn() as conn:
            if alert_id:
                rows = conn.execute(
                    "SELECT * FROM incidents WHERE alert_id = ? ORDER BY id DESC LIMIT ?",
                    (alert_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM incidents ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        meta = data.get("metadata")
        if isinstance(meta, str) and meta:
            try:
                data["metadata"] = json.loads(meta)
            except json.JSONDecodeError:
                pass
        return data


# Convenience module-level helpers using the default DB path.
_default_store: Optional[IncidentStore] = None


def _store(db_path: str = "autosre.db") -> IncidentStore:
    global _default_store
    if _default_store is None or _default_store.db_path != db_path:
        _default_store = IncidentStore(db_path)
    return _default_store


def save_incident(**kwargs) -> int:
    db_path = kwargs.pop("db_path", "autosre.db")
    return _store(db_path).save_incident(**kwargs)


def get_history(limit: int = 50, alert_id: Optional[str] = None, db_path: str = "autosre.db") -> List[dict]:
    return _store(db_path).get_history(limit=limit, alert_id=alert_id)


def get_incident(incident_id: int, db_path: str = "autosre.db") -> Optional[dict]:
    return _store(db_path).get_incident(incident_id)
