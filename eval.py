"""Evaluate AIOps agent accuracy against known fault scenarios.

Runs each scenario (or a specific one) through the agent, then inspects the
generated incident report to verify that the expected root-cause and
remediation keywords appear.  Outputs a summary table and exits with code 0
if every scenario passes, 1 otherwise.

Usage:
    python eval.py                  # evaluate all scenarios
    python eval.py db               # evaluate a single scenario
    python eval.py --simulate       # use offline simulation mode
    python eval.py disk --simulate  # simulate a single scenario
    python eval.py --json           # machine-readable JSON summary
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import re
import sys

from trigger_fault import SCENARIOS
from autosre.agent import run_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")


def _latest_report(alert_id: str) -> str | None:
    """Return the path of the most-recent report matching *alert_id*, or None."""
    pattern = os.path.join(REPORTS_DIR, f"incident-{alert_id}-*.md")
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def _significant_words(phrase: str) -> list[str]:
    """Tokenize *phrase* into significant keywords (length > 2), stripping edge punctuation."""
    words = []
    for raw in phrase.lower().split():
        cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", raw)
        if len(cleaned) > 2:
            words.append(cleaned)
    return words


def _keywords_present(text: str, phrase: str) -> bool:
    """Check consecutive keyword n-grams from *phrase* appear in *text*.

    Significant words (len > 2, punctuation-stripped) are extracted from
    *phrase*. When two or more words are present, every consecutive bigram
    must appear in order in the report, allowing short filler between the
    two tokens. A single significant word falls back to a unigram check.
    """
    text_lower = text.lower()
    words = _significant_words(phrase)
    if not words:
        return True
    if len(words) == 1:
        return words[0] in text_lower
    for i in range(len(words) - 1):
        # Allow up to ~40 chars of filler/whitespace between consecutive keywords
        pattern = re.escape(words[i]) + r".{0,40}?" + re.escape(words[i + 1])
        if not re.search(pattern, text_lower, flags=re.DOTALL):
            return False
    return True


def evaluate_scenario(name: str, simulate: bool = False) -> dict:
    """Run one scenario and check the resulting report.

    Returns a dict with keys: scenario, root_cause_match, remediation_match,
    score, passed.
    """
    scenario = SCENARIOS[name]
    logger.info("Evaluating scenario '%s' ...", name)

    if simulate:
        from trigger_fault import simulate as sim_fn

        exit_code = sim_fn(name)
    else:
        exit_code = run_agent(name)

    result = {
        "scenario": name,
        "root_cause_match": False,
        "remediation_match": False,
        "score": 0.0,
        "passed": False,
    }

    if simulate:
        # In simulate mode we only check for a clean exit
        ok = exit_code == 0
        result["root_cause_match"] = ok
        result["remediation_match"] = ok
        result["score"] = 1.0 if ok else 0.0
        result["passed"] = ok
        return result

    report_path = _latest_report(scenario["alert_id"])
    if report_path is None:
        logger.warning("No report found for alert_id=%s", scenario["alert_id"])
        return result

    logger.info("Reading report: %s", report_path)
    with open(report_path, "r") as fh:
        report_text = fh.read()

    root_ok = _keywords_present(report_text, scenario["expected_root_cause"])
    remed_ok = _keywords_present(report_text, scenario["expected_remediation"])

    score = (0.5 if root_ok else 0.0) + (0.5 if remed_ok else 0.0)
    result["root_cause_match"] = root_ok
    result["remediation_match"] = remed_ok
    result["score"] = score
    result["passed"] = score == 1.0

    if root_ok:
        logger.info("  Root cause keywords matched.")
    else:
        logger.warning(
            "  Root cause keywords NOT matched.  Expected: %s",
            scenario["expected_root_cause"],
        )

    if remed_ok:
        logger.info("  Remediation keywords matched.")
    else:
        logger.warning(
            "  Remediation keywords NOT matched.  Expected: %s",
            scenario["expected_remediation"],
        )

    return result


def print_summary(results: list[dict]) -> None:
    """Print a human-readable results table."""
    hdr = (
        f"{'Scenario':<12} | {'Root Cause Match':<16} | "
        f"{'Remediation Match':<17} | {'Score':<5} | {'Status'}"
    )
    sep = f"{'-' * 12}-+-{'-' * 16}-+-{'-' * 17}-+-{'-' * 5}-+-{'-' * 6}"
    print()
    print(hdr)
    print(sep)
    for r in results:
        rc = "\u2713" if r["root_cause_match"] else "\u2717"
        rm = "\u2713" if r["remediation_match"] else "\u2717"
        st = "PASS" if r["passed"] else "FAIL"
        print(
            f"{r['scenario']:<12} | {rc:<16} | {rm:<17} | "
            f"{r.get('score', 0):<5.1f} | {st}"
        )
    print()


def main() -> int:
    """Entry point for the evaluation CLI."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        choices=sorted(SCENARIOS),
        help="Run evaluation for a single scenario (default: all)",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use offline simulation mode (no LLM / server needed)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit results as JSON instead of a table",
    )
    args = parser.parse_args()

    names = [args.scenario] if args.scenario else list(SCENARIOS.keys())

    results = []
    for name in names:
        results.append(evaluate_scenario(name, simulate=args.simulate))

    all_passed = all(r["passed"] for r in results)
    avg_score = (
        sum(r.get("score", 0.0) for r in results) / len(results) if results else 0.0
    )

    if args.as_json:
        print(
            json.dumps(
                {
                    "results": results,
                    "all_passed": all_passed,
                    "average_score": avg_score,
                    "count": len(results),
                },
                indent=2,
            )
        )
    else:
        print_summary(results)
        if all_passed:
            logger.info("All %d scenario(s) PASSED.", len(results))
        else:
            failed = [r["scenario"] for r in results if not r["passed"]]
            logger.error("FAILED scenarios: %s", ", ".join(failed))

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
