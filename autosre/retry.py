"""Retry helpers for HTTP tool calls and LLM API calls."""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)


def _is_retryable_http(exc: BaseException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError):
        resp = getattr(exc, "response", None)
        if resp is not None and (resp.status_code == 429 or resp.status_code >= 500):
            return True
    return False


def retry_http(fn: F) -> F:
    """Retry HTTP tool wrappers: 3 tries with 1s / 2s / 4s backoff."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception(_is_retryable_http),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )(fn)


def retry_llm(fn: F) -> F:
    """Retry LLM create calls once after a 2s delay."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_fixed(2),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )(fn)
