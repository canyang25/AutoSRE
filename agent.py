"""Self-contained AIOps incident agent.

Runs the full incident loop with an LLM tool-use agent (Anthropic): given a
fault alert it queries metrics and logs, diagnoses a root cause, executes a
remediation playbook, and writes an incident report -- calling the mock
Prometheus / ELK / Ansible services in tools/ as its tools.

Configuration (see .env.example):
    ANTHROPIC_API_KEY     required for the live agent (falls back to --simulate without it)
    ANTHROPIC_MODEL       default: claude-sonnet-5  (claude-opus-4-8 is the more capable option)
    PROMETHEUS_URL        default: http://localhost:9091
    ELK_URL               default: http://localhost:9093
    ANSIBLE_URL           default: http://localhost:9092

Usage:
    python agent.py db                 # run the real agent loop
    python agent.py disk --simulate    # offline walkthrough, no key/server needed
    python agent.py --list
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import requests

# Reuse the scenario catalog and offline walkthrough from the trigger script.
# Importing is safe: trigger_fault's CLI only runs under its own __main__ guard.
from trigger_fault import SCENARIOS, simulate

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass


MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9091").rstrip("/")
ELK_URL = os.getenv("ELK_URL", "http://localhost:9093").rstrip("/")
ANSIBLE_URL = os.getenv("ANSIBLE_URL", "http://localhost:9092").rstrip("/")

MAX_ITERATIONS = 12  # safety bound on the tool-use loop

SYSTEM_PROMPT = """You are an autonomous AIOps agent acting as an on-call SRE.

You are given a production fault alert. Investigate and resolve it end to end:
1. Gather signals: use query_metrics and search_logs to inspect the affected service.
2. Diagnose: state a single, specific root cause supported by the evidence you gathered.
3. Remediate: call run_playbook with the playbook that fixes that root cause. Known
   playbooks: restore_db_pool.yml, clean_disk_space.yml, restart_service.yml.
4. Report: after remediation succeeds, write a concise incident report in Markdown with
   these sections: Summary, Timeline, Root Cause, Remediation, Verification.

Use tools before concluding -- do not guess a root cause without checking metrics and logs.
When you have written the final report, stop calling tools and return only the report."""


# --- Tools: thin wrappers over the mock services -------------------------------

TOOLS = [
    {
        "name": "query_metrics",
        "description": "Query time-series metrics for a service from Prometheus. "
        "Returns summary stats (min/max/avg/latest) over the recent window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "e.g. order-service, file-service, payment-service"},
                "metric": {"type": "string", "description": "e.g. response_time, db_connections, disk_usage, io_wait, packet_loss, latency"},
            },
            "required": ["service", "metric"],
        },
    },
    {
        "name": "search_logs",
        "description": "Search recent logs for a service in ELK, optionally filtered by level.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "level": {"type": "string", "enum": ["INFO", "WARN", "ERROR"], "description": "optional level filter"},
            },
            "required": ["service"],
        },
    },
    {
        "name": "run_playbook",
        "description": "Execute an Ansible remediation playbook against the given hosts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "playbook": {"type": "string", "description": "e.g. restore_db_pool.yml"},
                "hosts": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["playbook"],
        },
    },
]


def _tool_query_metrics(service: str, metric: str) -> dict:
    resp = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query_range",
        params={"service": service, "metric": metric},
        timeout=15,
    )
    resp.raise_for_status()
    values = resp.json()["data"]["result"][0]["values"]
    nums = [v for _, v in values]
    return {
        "service": service,
        "metric": metric,
        "points": len(nums),
        "min": round(min(nums), 2),
        "max": round(max(nums), 2),
        "avg": round(sum(nums) / len(nums), 2),
        "latest": round(nums[-1], 2),
    }


def _tool_search_logs(service: str, level: str = None) -> dict:
    query = {"service": service}
    if level:
        query["level"] = level
    resp = requests.post(f"{ELK_URL}/_search", json={"query": query}, timeout=15)
    resp.raise_for_status()
    hits = resp.json()["hits"]
    return {
        "service": service,
        "level": level,
        "total": hits["total"]["value"],
        "logs": [h["_source"] for h in hits["hits"]],
    }


def _tool_run_playbook(playbook: str, hosts: list = None) -> dict:
    resp = requests.post(
        f"{ANSIBLE_URL}/api/v1/execute",
        json={"playbook": playbook, "hosts": hosts or ["localhost"]},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


TOOL_DISPATCH = {
    "query_metrics": _tool_query_metrics,
    "search_logs": _tool_search_logs,
    "run_playbook": _tool_run_playbook,
}


def _dispatch(name: str, tool_input: dict) -> dict:
    try:
        return TOOL_DISPATCH[name](**tool_input)
    except Exception as exc:  # surface tool failures to the model instead of crashing
        return {"error": f"{type(exc).__name__}: {exc}"}


# --- Agent loop ----------------------------------------------------------------

def run_agent(scenario_name: str) -> int:
    """Run the live tool-use loop. Falls back to simulate() on any failure."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY set -- running offline simulation instead.\n")
        return simulate(scenario_name)

    try:
        import anthropic
    except ImportError:
        print("The 'anthropic' package is not installed (pip install -r requirements.txt).")
        print("Falling back to offline simulation.\n")
        return simulate(scenario_name)

    scenario = SCENARIOS[scenario_name]
    # Hide the ground-truth answers from the agent -- it must derive them.
    alert = {k: v for k, v in scenario.items() if not k.startswith("expected_")}

    print(f"Dispatching AIOps agent for scenario '{scenario_name}' (model: {MODEL})")
    print(f"  Alert: {alert['alert_id']} -- {alert['description']}\n")

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": f"Incident alert:\n{json.dumps(alert, indent=2)}"}]

    try:
        final_report = None
        for _ in range(MAX_ITERATIONS):
            resp = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                final_report = "".join(b.text for b in resp.content if b.type == "text")
                break

            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    print(f"  [tool] {block.name}({json.dumps(block.input)})")
                    result = _dispatch(block.name, block.input)
                    print(f"         -> {json.dumps(result)[:160]}")
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
        else:
            print("Reached max iterations without a final report.")
            return 1
    except Exception as exc:
        print(f"Agent run failed ({type(exc).__name__}: {exc}). Falling back to simulation.\n")
        return simulate(scenario_name)

    if not final_report:
        print("Agent produced no report.")
        return 1

    print("\n=== Incident report ===\n")
    print(final_report)
    _write_report(scenario, final_report)
    return 0


def _write_report(scenario: dict, report: str) -> None:
    os.makedirs("reports", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join("reports", f"incident-{scenario['alert_id']}-{stamp}.md")
    with open(path, "w") as f:
        f.write(report)
    print(f"\nReport written to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS), help="fault scenario to resolve")
    parser.add_argument("--simulate", action="store_true", help="offline walkthrough (no key/server needed)")
    parser.add_argument("--list", action="store_true", help="list available scenarios and exit")
    args = parser.parse_args()

    if args.list:
        print("Available scenarios:")
        for name, s in SCENARIOS.items():
            print(f"  {name:8} {s['service']:16} {s['description']}")
        return 0

    if not args.scenario:
        parser.print_help()
        return 1

    return simulate(args.scenario) if args.simulate else run_agent(args.scenario)


if __name__ == "__main__":
    sys.exit(main())
