"""Tests for the mock Flask services (Prometheus, ELK, Ansible).

These tests exercise the Flask apps via their test clients, validating the
HTTP contract that agent.py's tool wrappers depend on.

Note: The mock_ansible service makes outbound HTTP calls to Prometheus/ELK
when a playbook remediates metrics.  We patch ``requests.post`` in those
tests so they run without live servers.
"""

import json
from unittest.mock import patch, MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# Mock Prometheus
# ═══════════════════════════════════════════════════════════════════════════

class TestMockPrometheus:
    """Tests for tools/mock_prometheus.py."""

    def test_query_range_valid_service(self, prometheus_client):
        resp = prometheus_client.get(
            "/api/v1/query_range",
            query_string={"service": "order-service", "metric": "cpu_usage"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"
        result = body["data"]["result"]
        assert len(result) > 0
        values = result[0]["values"]
        assert isinstance(values, list)
        assert len(values) > 0
        # Each value is [timestamp, number]
        assert len(values[0]) == 2

    def test_query_range_unknown_service(self, prometheus_client):
        resp = prometheus_client.get(
            "/api/v1/query_range",
            query_string={"service": "nonexistent-service", "metric": "cpu_usage"},
        )
        assert resp.status_code == 404

    def test_query_range_unknown_metric(self, prometheus_client):
        resp = prometheus_client.get(
            "/api/v1/query_range",
            query_string={"service": "order-service", "metric": "nonexistent_metric"},
        )
        assert resp.status_code == 404

    def test_remediation_state(self, prometheus_client):
        # Remediate with new ranges
        resp = prometheus_client.post(
            "/api/v1/remediated",
            json={
                "service": "order-service",
                "metrics": {
                    "cpu_usage": {"min": 10, "max": 30},
                },
            },
        )
        assert resp.status_code == 200

        # Query should now reflect the new ranges
        resp = prometheus_client.get(
            "/api/v1/query_range",
            query_string={"service": "order-service", "metric": "cpu_usage"},
        )
        assert resp.status_code == 200
        values = resp.get_json()["data"]["result"][0]["values"]
        for _, v in values:
            assert 10 <= v <= 30

    def test_reset_state(self, prometheus_client):
        # Remediate first
        prometheus_client.post(
            "/api/v1/remediated",
            json={
                "service": "order-service",
                "metrics": {
                    "cpu_usage": {"min": 1, "max": 2},
                },
            },
        )

        # Reset to original ranges
        resp = prometheus_client.post("/api/v1/reset")
        assert resp.status_code == 200

        # Query should return values in the original range (20-95 for cpu_usage)
        resp = prometheus_client.get(
            "/api/v1/query_range",
            query_string={"service": "order-service", "metric": "cpu_usage"},
        )
        assert resp.status_code == 200
        values = resp.get_json()["data"]["result"][0]["values"]
        for _, v in values:
            assert 20 <= v <= 95


# ═══════════════════════════════════════════════════════════════════════════
# Mock ELK
# ═══════════════════════════════════════════════════════════════════════════

class TestMockELK:
    """Tests for tools/mock_elk.py."""

    def test_search_logs_valid(self, elk_client):
        resp = elk_client.post(
            "/_search",
            json={"query": {"service": "order-service"}},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "hits" in body
        assert body["hits"]["total"]["value"] > 0
        assert len(body["hits"]["hits"]) > 0

    def test_search_logs_with_level_filter(self, elk_client):
        resp = elk_client.post(
            "/_search",
            json={"query": {"service": "order-service", "level": "ERROR"}},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        for hit in body["hits"]["hits"]:
            assert hit["_source"]["level"] == "ERROR"

    def test_search_logs_unknown_service(self, elk_client):
        resp = elk_client.post(
            "/_search",
            json={"query": {"service": "nonexistent-service"}},
        )
        assert resp.status_code == 404

    def test_inject_log(self, elk_client):
        new_log = {
            "service": "order-service",
            "log": {
                "timestamp": "2025-06-04T15:00:00Z",
                "level": "WARN",
                "message": "Injected test log entry",
            },
        }
        resp = elk_client.post("/_inject", json=new_log)
        assert resp.status_code == 200

        # Search and verify the injected log appears
        resp = elk_client.post(
            "/_search",
            json={"query": {"service": "order-service"}},
        )
        body = resp.get_json()
        messages = [h["_source"]["message"] for h in body["hits"]["hits"]]
        assert "Injected test log entry" in messages

    def test_reset_logs(self, elk_client):
        # Inject a log
        elk_client.post(
            "/_inject",
            json={
                "service": "order-service",
                "entry": {
                    "timestamp": "2025-06-04T15:05:00Z",
                    "level": "INFO",
                    "message": "Temporary log for reset test",
                },
            },
        )

        # Reset
        resp = elk_client.post("/_reset")
        assert resp.status_code == 200

        # Verify the injected log is gone
        resp = elk_client.post(
            "/_search",
            json={"query": {"service": "order-service"}},
        )
        body = resp.get_json()
        messages = [h["_source"]["message"] for h in body["hits"]["hits"]]
        assert "Temporary log for reset test" not in messages


# ═══════════════════════════════════════════════════════════════════════════
# Mock Ansible
# ═══════════════════════════════════════════════════════════════════════════

class TestMockAnsible:
    """Tests for tools/mock_ansible.py.

    Outbound HTTP calls that playbooks may trigger (to Prometheus/ELK for
    remediation side-effects) are mocked out so tests don't need live servers.
    """

    @patch("requests.post", return_value=MagicMock(status_code=200))
    def test_execute_known_playbook(self, mock_post, ansible_client):
        resp = ansible_client.post(
            "/api/v1/execute",
            json={"playbook": "restore_db_pool.yml"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "success"

    @patch("requests.post", return_value=MagicMock(status_code=200))
    def test_execute_fuzzy_match(self, mock_post, ansible_client):
        resp = ansible_client.post(
            "/api/v1/execute",
            json={"playbook": "fix_database_pool"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        # The fuzzy matcher should resolve to restore_db_pool.yml
        assert body["status"] == "success"

    @patch("requests.post", return_value=MagicMock(status_code=200))
    def test_execute_unknown_playbook(self, mock_post, ansible_client):
        resp = ansible_client.post(
            "/api/v1/execute",
            json={"playbook": "nonexistent.yml"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "unknown"

    @patch("requests.post", return_value=MagicMock(status_code=200))
    def test_list_playbooks(self, mock_post, ansible_client):
        resp = ansible_client.get("/api/v1/playbooks")
        assert resp.status_code == 200
        body = resp.get_json()
        playbooks = body["playbooks"]
        assert "restore_db_pool.yml" in playbooks
        assert "clean_disk_space.yml" in playbooks
        assert "restart_service.yml" in playbooks
        for name, info in playbooks.items():
            assert "description" in info
