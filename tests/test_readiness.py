"""Readiness, metrics, and rate-limit tests."""

from fastapi.testclient import TestClient

from scenarios import SCENARIOS
from autosre.config import AutoSREConfig
from autosre.webhook import create_app


def _payload():
    name = sorted(SCENARIOS.keys())[0]
    scenario = SCENARIOS[name]
    labels = {"service": scenario["service"]}
    labels.update(scenario.get("webhook_labels") or {})
    return name, {
        "alerts": [
            {
                "status": "firing",
                "labels": labels,
                "annotations": {"summary": scenario["description"]},
            }
        ]
    }


def test_ready_ok_for_mock_mode(tmp_path):
    cfg = AutoSREConfig(db_path=str(tmp_path / "r.db"), backend_mode="mock")
    with TestClient(create_app(cfg)) as client:
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["ready"] is True


def test_ready_fails_when_token_required_missing(tmp_path):
    cfg = AutoSREConfig(
        db_path=str(tmp_path / "r2.db"),
        require_webhook_token=True,
        webhook_token="",
    )
    with TestClient(create_app(cfg)) as client:
        resp = client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["ready"] is False


def test_metrics_endpoint(tmp_path):
    cfg = AutoSREConfig(db_path=str(tmp_path / "m.db"))
    with TestClient(create_app(cfg)) as client:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "incidents_accepted" in resp.json()["metrics"]


def test_webhook_rate_limiter_blocks(tmp_path):
    from autosre.webhook import _RateLimiter

    limiter = _RateLimiter(2)
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False

    cfg = AutoSREConfig(
        db_path=str(tmp_path / "rl.db"),
        webhook_rate_limit_per_minute=1,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )
    _name, payload = _payload()
    with TestClient(create_app(cfg)) as client:
        client.app.state.busy = False
        # Pre-exhaust limiter so the next webhook is rate limited.
        assert client.app.state.rate_limiter.allow() is True
        assert client.app.state.rate_limiter.allow() is False
        limited = client.post("/webhook/alertmanager", json=payload)
        assert limited.status_code == 429
        assert limited.json()["status"] == "rate_limited"


def test_config_validate_real_needs_auth():
    cfg = AutoSREConfig(backend_mode="real", http_authorization="")
    ok, errors = cfg.validate()
    assert ok is False
    assert any("HTTP_AUTHORIZATION" in e for e in errors)
