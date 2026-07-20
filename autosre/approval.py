"""Operator approval gate for remediation playbooks."""

from __future__ import annotations

import logging
from typing import Optional

import requests

from autosre.config import AutoSREConfig, config as default_config

logger = logging.getLogger(__name__)


class ApprovalGate:
    """Gate remediation actions behind auto / prompt / webhook approval."""

    def __init__(self, cfg: Optional[AutoSREConfig] = None):
        self.cfg = cfg or default_config

    def request_approval(
        self,
        playbook: str,
        hosts: Optional[list] = None,
        context: Optional[dict] = None,
    ) -> bool:
        mode = (self.cfg.approval_mode or "auto").lower()
        hosts = hosts or ["localhost"]
        context = context or {}

        if mode == "auto":
            logger.info("Approval mode=auto — allowing playbook %s", playbook)
            return True

        if mode == "prompt":
            prompt = (
                f"Approve remediation playbook '{playbook}' on hosts {hosts}? [y/N] "
            )
            try:
                answer = input(prompt)
            except EOFError:
                logger.warning("No TTY for approval prompt; denying.")
                return False
            approved = answer.strip().lower() in {"y", "yes"}
            logger.info("Operator %s playbook %s", "approved" if approved else "denied", playbook)
            return approved

        if mode == "webhook":
            url = self.cfg.approval_webhook_url
            if not url:
                logger.error("AUTOSRE_APPROVAL_WEBHOOK_URL not set; denying remediation.")
                return False
            payload = {
                "playbook": playbook,
                "hosts": hosts,
                "context": context,
            }
            try:
                resp = requests.post(url, json=payload, timeout=30)
                resp.raise_for_status()
                body = resp.json() if resp.content else {}
                approved = bool(body.get("approved", body.get("allow", False)))
                logger.info(
                    "Webhook approval for %s: %s",
                    playbook,
                    "approved" if approved else "denied",
                )
                return approved
            except Exception as exc:
                logger.error("Approval webhook failed (%s); denying.", exc)
                return False

        logger.warning("Unknown approval mode %r; denying.", mode)
        return False
