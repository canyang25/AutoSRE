"""Tests for the remediation approval gate."""

from autosre.approval import ApprovalGate
from autosre.config import AutoSREConfig
from autosre.tools import _dispatch


def test_auto_mode_approves():
    gate = ApprovalGate(AutoSREConfig(approval_mode="auto"))
    assert gate.request_approval("restore_db_pool.yml", ["host1"]) is True


def test_prompt_mode_yes(monkeypatch):
    gate = ApprovalGate(AutoSREConfig(approval_mode="prompt"))
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert gate.request_approval("clean_disk_space.yml") is True


def test_prompt_mode_no(monkeypatch):
    gate = ApprovalGate(AutoSREConfig(approval_mode="prompt"))
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert gate.request_approval("clean_disk_space.yml") is False


def test_prompt_mode_eof_denies(monkeypatch):
    def _boom(_):
        raise EOFError

    gate = ApprovalGate(AutoSREConfig(approval_mode="prompt"))
    monkeypatch.setattr("builtins.input", _boom)
    assert gate.request_approval("restart_service.yml") is False


def test_webhook_mode_approved(monkeypatch):
    class _Resp:
        content = b'{"approved": true}'
        def raise_for_status(self):
            return None
        def json(self):
            return {"approved": True}

    monkeypatch.setattr(
        "autosre.approval.requests.post",
        lambda *a, **k: _Resp(),
    )
    gate = ApprovalGate(
        AutoSREConfig(
            approval_mode="webhook",
            approval_webhook_url="http://example.test/approve",
        )
    )
    assert gate.request_approval("restore_db_pool.yml") is True


def test_webhook_mode_missing_url_denies():
    gate = ApprovalGate(AutoSREConfig(approval_mode="webhook", approval_webhook_url=""))
    assert gate.request_approval("restore_db_pool.yml") is False


def test_dispatch_denies_run_playbook(monkeypatch):
    cfg = AutoSREConfig(approval_mode="prompt")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    result = _dispatch(
        "run_playbook",
        {"playbook": "restore_db_pool.yml", "hosts": ["localhost"]},
        cfg=cfg,
    )
    assert result == {"error": "Remediation denied by operator"}
