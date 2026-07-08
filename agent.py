"""Self-contained AIOps incident agent.

Runs the full incident loop with an LLM tool-use agent: given a fault alert it
queries metrics and logs, diagnoses a root cause, executes a remediation
playbook, and writes an incident report -- calling the mock Prometheus / ELK /
Ansible services in tools/ as its tools.

Works with any of these LLM backends (see .env.example):
    Groq       free API key, no credit card -- set GROQ_API_KEY   (recommended)
    Ollama     fully local, no key at all   -- set LLM_PROVIDER=ollama
    Anthropic  Claude                       -- set ANTHROPIC_API_KEY
    OpenAI     or any OpenAI-compatible API  -- set OPENAI_API_KEY (+ OPENAI_BASE_URL)

With no backend configured it falls back to an offline --simulate walkthrough,
so it never hard-fails.

Usage:
    python agent.py db                 # run the real agent loop
    python agent.py disk --simulate    # offline walkthrough, no key/server needed
    python agent.py --list
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Reuse the scenario catalog and offline walkthrough from the trigger script.
# Importing is safe: trigger_fault's CLI only runs under its own __main__ guard.
from trigger_fault import SCENARIOS, simulate

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass


PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9091").rstrip("/")
ELK_URL = os.getenv("ELK_URL", "http://localhost:9093").rstrip("/")
ANSIBLE_URL = os.getenv("ANSIBLE_URL", "http://localhost:9092").rstrip("/")

MAX_ITERATIONS = 12  # safety bound on the tool-use loop

SYSTEM_PROMPT = """You are an autonomous AIOps agent acting as an on-call SRE.

You are given a production fault alert. Investigate and resolve it end to end:
1. Gather signals: use query_metrics and search_logs to inspect the affected service.
2. Diagnose: state a single, specific root cause supported by the evidence you gathered.
3. Remediate: call run_playbook with the playbook that fixes that root cause. Known
   playbooks: call list_playbooks to discover available remediation playbooks.
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
    {
        "name": "list_playbooks",
        "description": "List available Ansible remediation playbooks and their descriptions. Call this to discover what playbooks are available before running one.",
        "input_schema": {
            "type": "object",
            "properties": {},
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


def _tool_list_playbooks() -> dict:
    resp = requests.get(f"{ANSIBLE_URL}/api/v1/playbooks", timeout=15)
    resp.raise_for_status()
    return resp.json()


TOOL_DISPATCH = {
    "query_metrics": _tool_query_metrics,
    "search_logs": _tool_search_logs,
    "run_playbook": _tool_run_playbook,
    "list_playbooks": _tool_list_playbooks,
}


def _dispatch(name: str, tool_input: dict) -> dict:
    try:
        return TOOL_DISPATCH[name](**tool_input)
    except Exception as exc:  # surface tool failures to the model instead of crashing
        return {"error": f"{type(exc).__name__}: {exc}"}


# --- Backend selection ---------------------------------------------------------

def resolve_backend():
    """Pick an LLM backend from env. Returns a config dict or None (=> simulate).

    Precedence: explicit LLM_PROVIDER, else auto-detect by whichever key is set.
    """
    provider = os.getenv("LLM_PROVIDER", "").lower()
    if not provider:
        if os.getenv("GROQ_API_KEY"):
            provider = "groq"
        elif os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        else:
            return None

    if provider == "groq":
        key = os.getenv("GROQ_API_KEY")
        if not key:
            return None
        return {"kind": "openai", "base_url": "https://api.groq.com/openai/v1",
                "api_key": key, "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")}
    if provider == "ollama":
        return {"kind": "openai", "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                "api_key": "ollama", "model": os.getenv("OLLAMA_MODEL", "llama3.1")}
    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            return None
        return {"kind": "openai", "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "api_key": key, "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini")}
    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        return {"kind": "anthropic", "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")}
    return None


# --- Agent loops ---------------------------------------------------------------

def _log_tool(name, tool_input, result):
    logger.debug("  [tool] %s(%s)", name, json.dumps(tool_input))
    logger.debug("         -> %s", json.dumps(result)[:160])


def _run_openai_compatible(alert: dict, cfg: dict) -> str:
    """Tool-use loop over any OpenAI-compatible API (Groq, Ollama, OpenAI, ...)."""
    from openai import OpenAI

    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
    tools = [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
        for t in TOOLS
    ]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Incident alert:\n{json.dumps(alert, indent=2)}"},
    ]

    for _ in range(MAX_ITERATIONS):
        resp = client.chat.completions.create(model=cfg["model"], messages=messages, tools=tools, tool_choice="auto", max_tokens=2048)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = _dispatch(tc.function.name, args)
            _log_tool(tc.function.name, args, result)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    return ""


def _run_anthropic(alert: dict, cfg: dict) -> str:
    """Tool-use loop over the Anthropic Messages API."""
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": f"Incident alert:\n{json.dumps(alert, indent=2)}"}]

    for _ in range(MAX_ITERATIONS):
        resp = client.messages.create(model=cfg["model"], max_tokens=2048, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                result = _dispatch(block.name, block.input)
                _log_tool(block.name, block.input, result)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
        messages.append({"role": "user", "content": tool_results})
    return ""


def run_agent(scenario_name: str) -> int:
    """Run the live tool-use loop. Falls back to simulate() on any failure."""
    cfg = resolve_backend()
    if cfg is None:
        logger.info("No LLM backend configured -- running offline simulation instead.")
        logger.info("Set GROQ_API_KEY (free, no credit card) to run the real agent. See .env.example.")
        return simulate(scenario_name)

    scenario = SCENARIOS[scenario_name]
    # Hide the ground-truth answers from the agent -- it must derive them.
    alert = {k: v for k, v in scenario.items() if not k.startswith("expected_")}

    logger.info("Dispatching AutoSRE agent for '%s' via %s (%s)", scenario_name, cfg["kind"], cfg["model"])
    logger.info("  Alert: %s -- %s", alert["alert_id"], alert["description"])

    try:
        if cfg["kind"] == "anthropic":
            report = _run_anthropic(alert, cfg)
        else:
            report = _run_openai_compatible(alert, cfg)
    except Exception as exc:
        logger.error("Agent run failed (%s: %s). Falling back to simulation.", type(exc).__name__, exc)
        return simulate(scenario_name)

    if not report:
        logger.info("Agent produced no report (hit iteration limit or empty response).")
        return 1

    logger.info("\n=== Incident report ===\n")
    logger.info("%s", report)
    _write_report(scenario, report)
    return 0


def _write_report(scenario: dict, report: str) -> None:
    os.makedirs("reports", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join("reports", f"incident-{scenario['alert_id']}-{stamp}.md")
    with open(path, "w") as f:
        f.write(report)
    logger.info("Report written to %s", path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS), help="fault scenario to resolve")
    parser.add_argument("--simulate", action="store_true", help="offline walkthrough (no key/server needed)")
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

    return simulate(args.scenario) if args.simulate else run_agent(args.scenario)


if __name__ == "__main__":
    sys.exit(main())
