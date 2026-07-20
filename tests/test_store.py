"""Tests for the SQLite incident store."""

from autosre.store import IncidentStore


def test_save_and_get_incident(tmp_path):
    db = tmp_path / "test.db"
    store = IncidentStore(str(db))

    incident_id = store.save_incident(
        alert_id="ALERT-DB-001",
        service="order-service",
        scenario="db",
        severity="critical",
        status="resolved",
        report_path="reports/test.md",
        report_text="# Incident\nRoot cause: pool",
        backend="groq",
        model="llama-3.3-70b-versatile",
        duration_ms=1234,
        metadata={"trace_id": "abc"},
    )

    assert isinstance(incident_id, int)
    assert incident_id >= 1

    row = store.get_incident(incident_id)
    assert row is not None
    assert row["alert_id"] == "ALERT-DB-001"
    assert row["service"] == "order-service"
    assert row["scenario"] == "db"
    assert row["backend"] == "groq"
    assert row["metadata"]["trace_id"] == "abc"


def test_get_history_order_and_filter(tmp_path):
    db = tmp_path / "hist.db"
    store = IncidentStore(str(db))

    store.save_incident(alert_id="A1", scenario="db", service="s1")
    store.save_incident(alert_id="A2", scenario="disk", service="s2")
    store.save_incident(alert_id="A1", scenario="db", service="s1")

    history = store.get_history(limit=10)
    assert len(history) == 3
    # Newest first
    assert history[0]["id"] > history[1]["id"]

    filtered = store.get_history(alert_id="A1")
    assert len(filtered) == 2
    assert all(r["alert_id"] == "A1" for r in filtered)


def test_get_missing_incident(tmp_path):
    store = IncidentStore(str(tmp_path / "empty.db"))
    assert store.get_incident(999) is None


def test_module_helpers(tmp_path, monkeypatch):
    from autosre import store as store_mod

    db = str(tmp_path / "helpers.db")
    monkeypatch.setattr(store_mod, "_default_store", None)

    iid = store_mod.save_incident(alert_id="X", scenario="db", db_path=db)
    assert store_mod.get_incident(iid, db_path=db)["alert_id"] == "X"
    assert len(store_mod.get_history(db_path=db)) == 1
