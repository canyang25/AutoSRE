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
import logging
import os
import sys

import requests
from scenarios import SCENARIOS

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

    logger.info("Run the real loop via agent.py instead of trigger_fault.py.")
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
