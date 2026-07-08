"""Trigger a fault scenario against the AIOps agent.

Sends a fault alert to a Dify Workflow app, which then autonomously queries
metrics/logs, diagnoses a root cause, runs a remediation playbook, and writes
an incident report.

Configuration comes from environment variables (see .env.example):
    DIFY_API_BASE           e.g. http://localhost/v1
    DIFY_WORKFLOW_API_KEY   your Dify workflow app key (starts with "app-")

Usage:
    python trigger_fault.py db                 # send the DB scenario to Dify
    python trigger_fault.py disk --simulate    # offline walkthrough, no server
    python trigger_fault.py --list             # list available scenarios
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional; env vars still work without it.
    pass


DIFY_API_BASE = os.getenv("DIFY_API_BASE", "http://localhost/v1")
DIFY_WORKFLOW_API_KEY = os.getenv("DIFY_WORKFLOW_API_KEY", "")
WORKFLOW_URL = f"{DIFY_API_BASE.rstrip('/')}/workflows/run"

# Hardcoded default scenarios used as a fallback when scenarios.json is missing.
_DEFAULT_SCENARIOS = {
    "db": {
        "alert_id": "ALERT-DB-001",
        "service": "order-service",
        "severity": "critical",
        "description": "Order API latency increased from 200ms to 1.5s, user complaints rising.",
        "timestamp": "2025-06-04T14:00:00Z",
        "metrics": "response_time, db_connections",
        "expected_root_cause": "Database connection pool misconfiguration",
        "expected_remediation": "restore_db_pool.yml (max_connections 50 -> 200)",
    },
    "disk": {
        "alert_id": "ALERT-DISK-001",
        "service": "file-service",
        "severity": "high",
        "description": "/data partition usage reached 98%, service unavailable.",
        "timestamp": "2025-06-04T10:30:00Z",
        "metrics": "disk_usage, io_wait",
        "expected_root_cause": "Disk space exhausted",
        "expected_remediation": "clean_disk_space.yml (free ~15GB of temp files)",
    },
    "network": {
        "alert_id": "ALERT-NET-001",
        "service": "payment-service",
        "severity": "critical",
        "description": "Payment service network abnormal, failure rate increased.",
        "timestamp": "2025-06-04T16:45:00Z",
        "metrics": "packet_loss, latency",
        "expected_root_cause": "Network partition fault",
        "expected_remediation": "restart_service.yml (restart payment-service)",
    },
}


def _load_scenarios() -> dict:
    """Load scenarios from scenarios.json if it exists, otherwise fall back to _DEFAULT_SCENARIOS.

    When loading from JSON, ``timestamp_offset_minutes`` is converted to a
    dynamic ISO 8601 timestamp relative to *now*.  If the field is absent the
    original fixed timestamp from the defaults is used instead.
    """
    json_path = Path(__file__).resolve().parent / "scenarios.json"
    if not json_path.is_file():
        logger.info("scenarios.json not found; using built-in default scenarios.")
        return _DEFAULT_SCENARIOS

    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            raw: dict = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load scenarios.json (%s); falling back to defaults.", exc)
        return _DEFAULT_SCENARIOS

    now = datetime.now(timezone.utc)
    scenarios: dict = {}
    for name, data in raw.items():
        scenario = dict(data)  # shallow copy so we don't mutate the parsed JSON
        offset = scenario.pop("timestamp_offset_minutes", None)
        if offset is not None:
            ts = now + timedelta(minutes=offset)
            scenario["timestamp"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif "timestamp" not in scenario:
            # No offset and no fixed timestamp -- use the default if available.
            default = _DEFAULT_SCENARIOS.get(name, {})
            scenario["timestamp"] = default.get("timestamp", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        scenarios[name] = scenario

    logger.info("Loaded %d scenario(s) from %s.", len(scenarios), json_path)
    return scenarios


# Module-level scenarios dict so ``from trigger_fault import SCENARIOS`` works.
SCENARIOS = _load_scenarios()


def send_to_dify(scenario_name: str) -> int:
    """Send the scenario to the Dify Workflow API and print the result."""
    scenario = SCENARIOS[scenario_name]

    if not DIFY_WORKFLOW_API_KEY:
        logger.error("DIFY_WORKFLOW_API_KEY is not set.")
        logger.error("Copy .env.example to .env and add your key, or run with --simulate.")
        return 1

    payload = {
        "inputs": scenario,
        "response_mode": "blocking",
        "user": f"aiops-{scenario_name}",
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DIFY_WORKFLOW_API_KEY}",
    }

    logger.info("Triggering fault scenario '%s' -> %s", scenario_name, WORKFLOW_URL)
    logger.info("  %s", scenario["description"])

    try:
        response = requests.post(WORKFLOW_URL, json=payload, headers=headers, timeout=60)
    except requests.RequestException as exc:
        logger.error("Request failed: %s", exc)
        return 1

    if response.status_code != 200:
        logger.error("Workflow call failed (HTTP %d):\n%s", response.status_code, response.text)
        return 1

    try:
        result = response.json()
    except ValueError:
        logger.error("Response was not valid JSON:\n%s", response.text)
        return 1

    logger.info("Workflow Run ID: %s", result.get("workflow_run_id", "N/A"))
    outputs = (result.get("data") or {}).get("outputs") or {}
    logger.info("=== Agent output ===")
    logger.info("%s", outputs)
    return 0


def simulate(scenario_name: str) -> int:
    """Print the closed-loop the agent runs, without contacting any server."""
    scenario = SCENARIOS[scenario_name]

    logger.info("[SIMULATED] Fault scenario: %s", scenario_name)
    logger.info("  Alert:   %s (%s)", scenario["alert_id"], scenario["severity"])
    logger.info("  Service: %s", scenario["service"])
    logger.info("  Symptom: %s", scenario["description"])

    steps = [
        f"Query metrics from Prometheus  -> {scenario['service']}: {scenario['metrics']}",
        f"Retrieve logs from ELK         -> scanning for ERROR/WARN on {scenario['service']}",
        f"Diagnose root cause (LLM)      -> {scenario['expected_root_cause']}",
        f"Execute remediation (Ansible)  -> {scenario['expected_remediation']}",
        "Generate incident report        -> summary + timeline + resolution",
    ]
    for i, step in enumerate(steps, 1):
        logger.info("  %d. %s", i, step)

    logger.info("Run the real loop by pointing DIFY_* env vars at a live Dify workflow.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS), help="fault scenario to trigger")
    parser.add_argument("--simulate", action="store_true", help="print the agent loop offline (no server needed)")
    parser.add_argument("--list", action="store_true", help="list available scenarios and exit")
    args = parser.parse_args()

    if args.list:
        logger.info("Available scenarios:")
        for name, s in SCENARIOS.items():
            logger.info("  %s %s %s", f"{name:8}", f"{s['service']:16}", s["description"])
        return 0

    if not args.scenario:
        parser.print_help()
        return 1

    return simulate(args.scenario) if args.simulate else send_to_dify(args.scenario)


if __name__ == "__main__":
    sys.exit(main())
