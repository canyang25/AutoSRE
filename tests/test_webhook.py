"""Tests for the Alertmanager webhook FastAPI app."""

import pytest
from fastapi.testclient import TestClient

from autosre.config import AutoSREConfig
from autosre.webhook import create_app, _map_alertmanager_to_scenario


@pytest.fixture()
def client(tmp_path):
    cfg = AutoSREConfig(db_path=str(tmp_path / "wh.db"), port=8080)
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_incidents_empty(client):
    resp = client.get("/incidents")
    assert resp.status_code == 200
    assert resp.json()["incidents"] == []


def test_alertmanager_accepts_known_scenario(client, monkeypatch):
    # Avoid actually running the agent worker's run_agent in the thread for long;
    # the queue acceptance is what we care about here.
    monkeypatch.setattr("autosre.agent.run_agent", lambda *a, **k: 0)

    payload = {
        "alerts": [
            {
                "labels": {"service": "order-service", "alertname": "HighLatency"},
                "annotations": {"summary": "DB pool exhausted"},
            }
        ]
    }
    resp = client.post("/webhook/alertmanager", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["scenario"] == "db"
    assert "trace_id" in body


def test_alertmanager_rejects_unknown(client):
    resp = client.post(
        "/webhook/alertmanager",
        json={"alerts": [{"labels": {"service": "unknown-svc"}, "annotations": {}}]},
    )
    assert resp.status_code == 422


def test_map_explicit_scenario_label():
    payload = {
        "alerts": [
            {
                "labels": {"scenario": "disk", "service": "file-service"},
                "annotations": {},
            }
        ]
    }
    assert _map_alertmanager_to_scenario(payload) == "disk"


def test_busy_queue_returns_429(client, monkeypatch):
    monkeypatch.setattr("autosre.agent.run_agent", lambda *a, **k: 0)

    # Fill the queue without letting the worker drain it by replacing the queue.
    import asyncio

    async def _block_forever():
        await asyncio.Event().wait()

    # Put an item directly so the queue is full (maxsize=1).
    q = client.app.state.queue
    q.put_nowait({"scenario": "db", "trace_id": "x"})

    payload = {
        "alerts": [
            {
                "labels": {"scenario": "db"},
                "annotations": {"summary": "test"},
            }
        ]
    }
    resp = client.post("/webhook/alertmanager", json=payload)
    assert resp.status_code == 429
    # Drain so lifespan shutdown is clean
    try:
        q.get_nowait()
        q.task_done()
    except Exception:
        pass
