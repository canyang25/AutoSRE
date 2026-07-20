"""Structured JSON logging with per-incident trace IDs."""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")


class TraceContext:
    """Context manager / helper that binds a UUID trace_id for the current task."""

    def __init__(self, trace_id: Optional[str] = None):
        self.trace_id = trace_id or str(uuid.uuid4())
        self._token = None

    def __enter__(self) -> "TraceContext":
        self._token = _trace_id.set(self.trace_id)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _trace_id.reset(self._token)

    @staticmethod
    def current() -> str:
        return _trace_id.get() or ""

    @staticmethod
    def set(trace_id: str) -> None:
        _trace_id.set(trace_id)


class TraceFilter(logging.Filter):
    """Inject the current trace_id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get() or getattr(record, "trace_id", "") or ""
        if not hasattr(record, "tool"):
            record.tool = ""
        if not hasattr(record, "duration_ms"):
            record.duration_ms = None
        if not hasattr(record, "tokens_in"):
            record.tokens_in = None
        if not hasattr(record, "tokens_out"):
            record.tokens_out = None
        return True


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "") or "",
        }
        tool = getattr(record, "tool", "") or ""
        if tool:
            payload["tool"] = tool
        duration_ms = getattr(record, "duration_ms", None)
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        tokens_in = getattr(record, "tokens_in", None)
        if tokens_in is not None:
            payload["tokens_in"] = tokens_in
        tokens_out = getattr(record, "tokens_out", None)
        if tokens_out is not None:
            payload["tokens_out"] = tokens_out
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: int = logging.INFO, json_logs: bool = True) -> None:
    """Configure root logging. Safe to call multiple times."""
    root = logging.getLogger()
    root.setLevel(level)

    # Replace existing handlers so we don't double-log.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.addFilter(TraceFilter())
    if json_logs:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
    root.addHandler(handler)


def log_extra(
    tool: str = "",
    duration_ms: Optional[float] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
) -> dict:
    """Build an ``extra`` dict for structured log fields."""
    return {
        "tool": tool,
        "duration_ms": duration_ms,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
