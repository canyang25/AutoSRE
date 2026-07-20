"""Tests for HTTP retry helpers."""

import time
from unittest.mock import MagicMock

import pytest
import requests

from autosre.retry import retry_http


def test_retry_http_succeeds_after_failures():
    calls = {"n": 0}

    @retry_http
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("boom")
        return {"ok": True}

    start = time.monotonic()
    result = flaky()
    elapsed = time.monotonic() - start

    assert result == {"ok": True}
    assert calls["n"] == 3
    # Backoff 1s + 2s ≈ 3s (allow some slack)
    assert elapsed >= 2.5


def test_retry_http_exhausted():
    @retry_http
    def always_fail():
        raise requests.Timeout("nope")

    with pytest.raises(requests.Timeout):
        always_fail()


def test_retry_http_on_5xx():
    calls = {"n": 0}

    @retry_http
    def server_error():
        calls["n"] += 1
        resp = MagicMock()
        resp.status_code = 503
        err = requests.HTTPError("503")
        err.response = resp
        if calls["n"] < 2:
            raise err
        return "recovered"

    assert server_error() == "recovered"
    assert calls["n"] == 2


def test_retry_http_does_not_retry_4xx():
    calls = {"n": 0}

    @retry_http
    def client_error():
        calls["n"] += 1
        resp = MagicMock()
        resp.status_code = 404
        err = requests.HTTPError("404")
        err.response = resp
        raise err

    with pytest.raises(requests.HTTPError):
        client_error()
    assert calls["n"] == 1
