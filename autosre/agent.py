"""AutoSRE agent: LLM tool-use loop, fallback chain, rollback safety net."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Union

from scenarios import SCENARIOS
from trigger_fault import simulate

from autosre.config import AutoSREConfig
from autosre.logging import TraceContext, log_extra, setup_logging
from autosre.retry import retry_llm
from autosre.store import IncidentStore
from autosre.tools import TOOLS, _dispatch

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

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


def _sanitize_alert(scenario: dict) -> dict:
    """Hide ground-truth and metric hints from the LLM."""
    return {
        k: v
        for k, v in scenario.items()
        if not k.startswith("expected_") and k != "metrics"
    }


def _backend_for_provider(provider: str) -> Optional[dict]:
    """Build a backend config dict for a named provider, or None if unavailable."""
    provider = provider.lower()
    if provider == "groq":
        key = os.getenv("GROQ_API_KEY")
        if not key:
            return None
        return {
            "kind": "openai",
            "provider": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": key,
            "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        }
    if provider == "ollama":
        return {
            "kind": "openai",
            "provider": "ollama",
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            "api_key": "ollama",
            "model": os.getenv("OLLAMA_MODEL", "llama3.1"),
        }
    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            return None
        return {
            "kind": "openai",
            "provider": "openai",
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            "api_key": key,
            "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        }
    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            return None
        return {
            "kind": "openai",
            "provider": "gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "api_key": key,
            "model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        }
    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        return {
            "kind": "anthropic",
            "provider": "anthropic",
            "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        }
    return None


def resolve_backend(
    cfg: Optional[AutoSREConfig] = None,
) -> Union[dict, List[dict], None]:
    """Pick LLM backend(s) from env.

    Returns a single config dict (or None) by default. When ``LLM_FALLBACK_CHAIN``
    is set, returns a list of available backend configs in chain order.
    """
    cfg = cfg or AutoSREConfig.from_env()

    if cfg.fallback_chain:
        backends: List[dict] = []
        for name in cfg.fallback_chain:
            backend = _backend_for_provider(name)
            if backend:
                backends.append(backend)
            else:
                logger.warning("Fallback chain provider %r unavailable (missing key)", name)
        return backends or None

    provider = os.getenv("LLM_PROVIDER", "").lower()
    if not provider:
        if os.getenv("GROQ_API_KEY"):
            provider = "groq"
        elif os.getenv("GEMINI_API_KEY"):
            provider = "gemini"
        elif os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        else:
            return None

    return _backend_for_provider(provider)


def _normalize_backends(resolved) -> List[dict]:
    if resolved is None:
        return []
    if isinstance(resolved, list):
        return resolved
    return [resolved]


def _log_tool(name, tool_input, result):
    logger.info(
        "tool %s(%s) -> %s",
        name,
        json.dumps(tool_input),
        json.dumps(result)[:160],
        extra=log_extra(tool=name),
    )


def _deadline_exceeded(deadline: float) -> bool:
    return time.monotonic() > deadline


@retry_llm
def _openai_create(client, **kwargs):
    return client.chat.completions.create(**kwargs)


@retry_llm
def _anthropic_create(client, **kwargs):
    return client.messages.create(**kwargs)


def _run_openai_compatible(
    alert: dict, cfg: dict, *, max_iterations: int, deadline: float
) -> str:
    """Tool-use loop over any OpenAI-compatible API (Groq, Ollama, OpenAI, ...)."""
    from openai import OpenAI

    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
    tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOLS
    ]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Incident alert:\n{json.dumps(alert, indent=2)}"},
    ]

    for _ in range(max_iterations):
        if _deadline_exceeded(deadline):
            logger.warning("Incident timeout reached during OpenAI-compatible loop.")
            return ""
        resp = _openai_create(
            client,
            model=cfg["model"],
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=2048,
        )
        usage = getattr(resp, "usage", None)
        if usage:
            logger.info(
                "llm tokens",
                extra=log_extra(
                    tokens_in=getattr(usage, "prompt_tokens", None),
                    tokens_out=getattr(usage, "completion_tokens", None),
                ),
            )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = _dispatch(tc.function.name, args)
            _log_tool(tc.function.name, args, result)
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)}
            )
    logger.warning(
        "Agent hit MAX_ITERATIONS (%d). Exiting loop without final report.",
        max_iterations,
    )
    return ""


def _run_anthropic(
    alert: dict, cfg: dict, *, max_iterations: int, deadline: float
) -> str:
    """Tool-use loop over the Anthropic Messages API."""
    import anthropic

    client = anthropic.Anthropic()
    messages = [
        {"role": "user", "content": f"Incident alert:\n{json.dumps(alert, indent=2)}"}
    ]

    for _ in range(max_iterations):
        if _deadline_exceeded(deadline):
            logger.warning("Incident timeout reached during Anthropic loop.")
            return ""
        resp = _anthropic_create(
            client,
            model=cfg["model"],
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        usage = getattr(resp, "usage", None)
        if usage:
            logger.info(
                "llm tokens",
                extra=log_extra(
                    tokens_in=getattr(usage, "input_tokens", None),
                    tokens_out=getattr(usage, "output_tokens", None),
                ),
            )
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                result = _dispatch(block.name, block.input)
                _log_tool(block.name, block.input, result)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
        messages.append({"role": "user", "content": tool_results})
    logger.warning(
        "Agent hit MAX_ITERATIONS (%d). Exiting loop without final report.",
        max_iterations,
    )
    return ""


def _maybe_rollback(scenario: dict, cfg: AutoSREConfig) -> Optional[dict]:
    """Independent rollback safety net after remediation.

    If ``AUTOSRE_ROLLBACK_PLAYBOOK`` is set, re-query the first metric hint;
    if the latest value is still in the upper failure half of the fixture range
    (heuristic: latest >= avg of returned window and >= 70% of max), execute
    the rollback playbook.
    """
    playbook = cfg.rollback_playbook
    if not playbook:
        return None

    metrics_hint = scenario.get("metrics") or ""
    metric_name = metrics_hint.split(",")[0].strip() if metrics_hint else ""
    service = scenario.get("service", "")
    if not metric_name or not service:
        logger.warning("Rollback configured but no metric/service available; skipping.")
        return None

    from autosre.tools import _tool_query_metrics

    try:
        stats = _tool_query_metrics(service, metric_name)
    except Exception as exc:
        logger.error("Rollback metric check failed: %s", exc)
        return {"error": str(exc), "skipped": True}

    latest = stats.get("latest", 0)
    avg = stats.get("avg", 0)
    mx = stats.get("max", 0)
    still_failing = latest >= avg and (mx == 0 or latest >= 0.7 * mx)
    logger.info(
        "Rollback check metric=%s latest=%s avg=%s max=%s failing=%s",
        metric_name,
        latest,
        avg,
        mx,
        still_failing,
    )
    if not still_failing:
        return {"rollback": False, "reason": "metrics recovered"}

    result = _dispatch(
        "run_playbook",
        {"playbook": playbook, "hosts": [service]},
    )
    logger.info("Rollback playbook %s result: %s", playbook, result)
    return {"rollback": True, "playbook": playbook, "result": result}


def _write_report(scenario: dict, report: str) -> str:
    os.makedirs("reports", exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join("reports", f"incident-{scenario['alert_id']}-{stamp}.md")
    with open(path, "w") as f:
        f.write(report)
    logger.info("Report written to %s", path)
    return path


def run_agent(scenario_name: str, fallback: bool = False) -> int:
    """Run the live tool-use loop. Falls back to simulate() on failure if requested."""
    cfg = AutoSREConfig.from_env()
    backends = _normalize_backends(resolve_backend(cfg))

    if not backends:
        logger.info("No LLM backend configured -- running offline simulation instead.")
        logger.info(
            "Set GROQ_API_KEY (free, no credit card) to run the real agent. See .env.example."
        )
        return simulate(scenario_name)

    scenario = SCENARIOS[scenario_name]
    alert = _sanitize_alert(scenario)

    with TraceContext() as trace:
        start = time.monotonic()
        deadline = start + cfg.timeout
        logger.info(
            "Dispatching AutoSRE agent for '%s' (trace=%s, backends=%s)",
            scenario_name,
            trace.trace_id,
            [b.get("provider", b.get("kind")) for b in backends],
        )
        logger.info("  Alert: %s -- %s", alert["alert_id"], alert["description"])

        report = ""
        used_backend: Optional[dict] = None
        last_error: Optional[Exception] = None

        for backend in backends:
            if _deadline_exceeded(deadline):
                logger.error("Incident timeout before trying backend %s", backend.get("provider"))
                break
            try:
                logger.info(
                    "Trying backend %s (%s)",
                    backend.get("provider", backend["kind"]),
                    backend.get("model"),
                )
                if backend["kind"] == "anthropic":
                    report = _run_anthropic(
                        alert, backend, max_iterations=cfg.max_iterations, deadline=deadline
                    )
                else:
                    report = _run_openai_compatible(
                        alert, backend, max_iterations=cfg.max_iterations, deadline=deadline
                    )
                used_backend = backend
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                logger.error(
                    "Backend %s failed: %s: %s — trying next if available",
                    backend.get("provider", backend["kind"]),
                    type(exc).__name__,
                    exc,
                )
                continue

        if last_error is not None and not report:
            if fallback:
                logger.error(
                    "All backends failed (%s: %s). Falling back to simulation.",
                    type(last_error).__name__,
                    last_error,
                )
                return simulate(scenario_name)
            logger.error(
                "All backends failed (%s: %s). Use --fallback to simulate.",
                type(last_error).__name__,
                last_error,
            )
            raise last_error

        if not report:
            logger.info("Agent produced no report (hit iteration/timeout limit or empty response).")
            return 1

        logger.info("\n=== Incident report ===\n")
        logger.info("%s", report)
        report_path = _write_report(scenario, report)

        rollback_info = _maybe_rollback(scenario, cfg)

        duration_ms = int((time.monotonic() - start) * 1000)
        store = IncidentStore(cfg.db_path)
        store.save_incident(
            alert_id=scenario["alert_id"],
            service=scenario.get("service", ""),
            scenario=scenario_name,
            severity=scenario.get("severity", ""),
            status="resolved",
            report_path=report_path,
            report_text=report,
            backend=(used_backend or {}).get("provider", ""),
            model=(used_backend or {}).get("model", ""),
            duration_ms=duration_ms,
            metadata={
                "trace_id": trace.trace_id,
                "rollback": rollback_info,
            },
        )
        return 0


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        choices=sorted(SCENARIOS),
        help="fault scenario to resolve",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="offline walkthrough (no key/server needed)",
    )
    parser.add_argument(
        "--fallback",
        action="store_true",
        help="fallback to simulation on LLM error",
    )
    parser.add_argument(
        "--list", action="store_true", help="list available scenarios and exit"
    )
    args = parser.parse_args()

    if args.list:
        logger.info("Available scenarios:")
        for name, s in SCENARIOS.items():
            logger.info(
                "  %s %s %s", f"{name:8}", f"{s['service']:16}", s["description"]
            )
        return 0

    if not args.scenario:
        parser.print_help()
        return 1

    return (
        simulate(args.scenario)
        if args.simulate
        else run_agent(args.scenario, fallback=args.fallback)
    )


if __name__ == "__main__":
    sys.exit(main())
