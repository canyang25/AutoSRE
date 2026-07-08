"""Mock Prometheus service for AIOPS.

Serves randomly-generated metric time series.  Metric definitions are loaded
from ``fixtures/metrics.json``; if the file is missing the built-in defaults
are used instead.

Phase-3 state-aware endpoints:
  POST /api/v1/remediated  – override metric ranges after remediation
  POST /api/v1/reset       – clear all remediation overrides
"""

from flask import Flask, request, jsonify
from datetime import datetime
import json
import logging
import os
import random

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Default metric definitions (fallback when fixture file is absent)
# ---------------------------------------------------------------------------
_DEFAULT_METRICS = {
    "order-service": {
        "cpu_usage": {"min": 20, "max": 95, "points": 60},
        "memory_usage": {"min": 30, "max": 85, "points": 60},
        "db_connections": {"min": 150, "max": 200, "points": 60, "type": "int"},
        "response_time": {"min": 50, "max": 1500, "points": 60},
    },
    "file-service": {
        "disk_usage": {"min": 85, "max": 99, "points": 60},
        "io_wait": {"min": 5, "max": 50, "points": 60},
    },
    "payment-service": {
        "packet_loss": {"min": 0, "max": 50, "points": 60},
        "latency": {"min": 10, "max": 1200, "points": 60},
    },
}


def _load_metrics():
    """Load metric definitions from the fixture file, falling back to defaults."""
    fixture_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "metrics.json"
    )
    try:
        with open(fixture_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            logger.info("Loaded metric definitions from %s", fixture_path)
            return data
    except FileNotFoundError:
        logger.warning(
            "Fixture file %s not found – using built-in defaults", fixture_path
        )
        return _DEFAULT_METRICS


# Metric definitions (loaded once at import / startup)
_metrics = _load_metrics()

# ---------------------------------------------------------------------------
# Phase 3 – in-memory remediation state
# ---------------------------------------------------------------------------
_remediated: dict = {}


def _generate_value(spec: dict) -> float:
    """Generate a single random value according to a metric spec."""
    low, high = spec["min"], spec["max"]
    if spec.get("type") == "int":
        return float(random.randint(int(low), int(high)))
    return random.uniform(low, high)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# Main query route – disable strict slashes for Dify compatibility
@app.route("/api/v1/query_range", methods=["GET", "POST"], strict_slashes=False)
def query_range():
    logger.debug("Matched query_range route – args: %s", request.args)

    service = request.args.get("service", "")
    metric = request.args.get("metric", "cpu_usage")

    # Return 404 for unknown services
    if service not in _metrics:
        logger.warning("Unknown service requested: %s", service)
        return jsonify({"status": "error", "error": f"unknown service: {service}"}), 404

    service_metrics = _metrics[service]

    # Return 404 for unknown metrics within a known service
    if metric not in service_metrics:
        logger.warning("Unknown metric '%s' for service '%s'", metric, service)
        return (
            jsonify({
                "status": "error",
                "error": f"unknown metric: {metric} for service: {service}",
            }),
            404,
        )

    # Determine effective spec: remediation overrides take priority
    spec = service_metrics[metric]
    remediation = _remediated.get(service, {}).get(metric)
    if remediation is not None:
        # Merge remediation ranges into the original spec
        spec = {**spec, **remediation}
        logger.debug(
            "Using remediated ranges for %s/%s: %s", service, metric, remediation
        )

    # Build time series with values generated at request time
    points = spec.get("points", 60)
    end_time = int(datetime.now().timestamp())
    data = []
    for i in range(points):
        timestamp = end_time - (points - 1 - i) * 60
        value = _generate_value(spec)
        data.append([timestamp, value])

    return jsonify({
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"service": service, "__name__": metric},
                    "values": data,
                }
            ],
        },
    })


# ---------------------------------------------------------------------------
# Phase 3 – remediation / reset endpoints
# ---------------------------------------------------------------------------

@app.route("/api/v1/remediated", methods=["POST"], strict_slashes=False)
def set_remediated():
    """Store remediation overrides for a service's metrics."""
    payload = request.get_json(force=True, silent=True) or {}
    svc = payload.get("service")
    metrics_override = payload.get("metrics", {})

    if not svc:
        return jsonify({"status": "error", "error": "missing 'service' field"}), 400

    _remediated.setdefault(svc, {}).update(metrics_override)
    logger.info("Stored remediation for service '%s': %s", svc, metrics_override)
    return jsonify({"status": "ok", "service": svc, "overrides": _remediated[svc]})


@app.route("/api/v1/reset", methods=["POST"], strict_slashes=False)
def reset_state():
    """Clear all remediation overrides."""
    _remediated.clear()
    logger.info("All remediation state has been reset")
    return jsonify({"status": "ok", "message": "remediation state cleared"})


# ---------------------------------------------------------------------------
# Catch-all fallback route – intercepts unknown paths and delegates to
# query_range so that callers always receive a response.
# ---------------------------------------------------------------------------
@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT"])
def catch_all(path):
    logger.warning("Catch-all route triggered – unexpected path: /%s", path)
    logger.warning("Query parameters: %s", request.args)
    return query_range()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    app.run(host="0.0.0.0", port=9091, debug=False)