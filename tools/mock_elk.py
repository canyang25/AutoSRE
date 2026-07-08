"""Mock ELK (Elasticsearch) service for AIOPS.

Serves log entries for services.  Log data is loaded from
``fixtures/logs.json``; if the file is missing the built-in defaults are used.

Phase-3 state-aware endpoints:
  POST /_inject  – append a log entry for a service (called by Ansible mock)
  POST /_reset   – reload log data from fixtures (discards injected entries)
"""

from flask import Flask, request, jsonify
import json
import logging
import os

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Default log database (fallback when fixture file is absent)
# ---------------------------------------------------------------------------
_DEFAULT_LOGS = {
    "order-service": [
        {"timestamp": "2025-06-04T13:55:00Z", "level": "INFO", "message": "OrderService started successfully"},
        {"timestamp": "2025-06-04T14:00:00Z", "level": "ERROR", "message": "DB connection timeout (500ms > 200ms)"},
        {"timestamp": "2025-06-04T14:01:00Z", "level": "WARN", "message": "Database connection pool at 98% capacity"},
        {"timestamp": "2025-06-04T14:02:00Z", "level": "ERROR", "message": "DB connection timeout (520ms)"},
        {"timestamp": "2025-06-04T14:03:00Z", "level": "ERROR", "message": "Transaction rollback due to timeout"},
    ],
    "file-service": [
        {"timestamp": "2025-06-04T10:25:00Z", "level": "WARN", "message": "/data partition usage reached 90%"},
        {"timestamp": "2025-06-04T10:28:00Z", "level": "ERROR", "message": "No space left on device"},
        {"timestamp": "2025-06-04T10:30:00Z", "level": "ERROR", "message": "Failed to write log file: disk full"},
    ],
    "payment-service": [
        {"timestamp": "2025-06-04T16:40:00Z", "level": "WARN", "message": "Network latency increased to 800ms"},
        {"timestamp": "2025-06-04T16:45:00Z", "level": "ERROR", "message": "Connection reset by peer"},
        {"timestamp": "2025-06-04T16:46:00Z", "level": "ERROR", "message": "Payment gateway timeout"},
    ],
}

_FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "logs.json"
)


def _load_logs():
    """Load log entries from the fixture file, falling back to defaults."""
    try:
        with open(_FIXTURE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            logger.info("Loaded log data from %s", _FIXTURE_PATH)
            return data
    except FileNotFoundError:
        logger.warning(
            "Fixture file %s not found – using built-in defaults", _FIXTURE_PATH
        )
        # Return a deep copy so that runtime mutations don't affect the constant
        import copy
        return copy.deepcopy(_DEFAULT_LOGS)


# Mutable log database (loaded at startup, mutated by _inject / _reset)
_logs_database: dict = _load_logs()


# ---------------------------------------------------------------------------
# Search route – also serves as the catch-all fallback
# ---------------------------------------------------------------------------

@app.route("/_search", methods=["GET", "POST"], strict_slashes=False)
def search_logs_main():
    """Primary _search endpoint."""
    return _do_search()


# Catch-all fallback route – intercepts unknown paths
@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT"])
def catch_all(path=""):
    # Skip catch-all for the dedicated state-management endpoints
    if path in ("_inject", "_reset"):
        # Flask should not reach here for POST if routes are registered, but
        # guard against method/ordering surprises.
        pass
    logger.warning("Catch-all route triggered – unexpected path: /%s", path)
    return _do_search()


def _do_search():
    """Core search logic shared by the named route and the catch-all."""
    logger.debug("ELK received request – path: %s", request.path)

    # Extract the JSON request body
    data = request.get_json(force=True, silent=True) or {}
    query_field = data.get("query", {})

    service = None
    level = None

    # If the caller provided a proper JSON object query
    if isinstance(query_field, dict):
        service = query_field.get("service")
        level = query_field.get("level")
    # Fallback: if the caller sent a Lucene-style query string, do rough matching
    elif isinstance(query_field, str):
        for svc_name in _logs_database:
            if svc_name in query_field:
                service = svc_name
                break

        if "ERROR" in query_field:
            level = "ERROR"
        elif "WARN" in query_field:
            level = "WARN"

    # Return 404 for unknown or missing service instead of silently defaulting
    if not service:
        logger.warning("No service specified or matched in query")
        return (
            jsonify({"status": "error", "error": "no service specified in query"}),
            404,
        )

    if service not in _logs_database:
        logger.warning("Unknown service requested: %s", service)
        return (
            jsonify({"status": "error", "error": f"unknown service: {service}"}),
            404,
        )

    logger.debug("Extracted filter conditions: service=%s, level=%s", service, level)

    logs = _logs_database.get(service, [])
    filtered_logs = [log for log in logs if not level or log["level"] == level]

    return jsonify({
        "hits": {
            "total": {"value": len(filtered_logs)},
            "hits": [{"_source": log} for log in filtered_logs[-5:]],
        }
    })


# ---------------------------------------------------------------------------
# Phase 3 – state-aware endpoints
# ---------------------------------------------------------------------------

@app.route("/_inject", methods=["POST"], strict_slashes=False)
def inject_log():
    """Append a log entry for a service (called by Ansible mock after remediation)."""
    payload = request.get_json(force=True, silent=True) or {}
    svc = payload.get("service")
    log_entry = payload.get("log")

    if not svc or not log_entry:
        return (
            jsonify({"status": "error", "error": "missing 'service' or 'log' field"}),
            400,
        )

    _logs_database.setdefault(svc, []).append(log_entry)
    logger.info("Injected log entry for service '%s': %s", svc, log_entry)
    return jsonify({"status": "ok", "service": svc, "total_logs": len(_logs_database[svc])})


@app.route("/_reset", methods=["POST"], strict_slashes=False)
def reset_logs():
    """Reload log data from fixtures, discarding any injected entries."""
    global _logs_database
    _logs_database = _load_logs()
    logger.info("Log database has been reset from fixtures")
    return jsonify({"status": "ok", "message": "log database reloaded from fixtures"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    app.run(host="0.0.0.0", port=9093, debug=False)