"""Tests for agent backend resolution, tool dispatch, and data imports."""

from scenarios import SCENARIOS
from autosre.agent import resolve_backend
from autosre.tools import TOOLS, _dispatch


def _clear_llm_env(monkeypatch):
    """Remove all LLM-related env vars so tests start from a clean slate."""
    for key in (
        "GROQ_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "LLM_PROVIDER",
        "LLM_FALLBACK_CHAIN",
    ):
        monkeypatch.delenv(key, raising=False)


class TestResolveBackend:
    """Verify resolve_backend picks the right provider from env vars."""

    def test_resolve_backend_groq(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_123")

        cfg = resolve_backend()

        assert cfg is not None
        assert cfg["kind"] == "openai"
        assert "groq.com" in cfg["base_url"]
        assert cfg["model"] == "llama-3.3-70b-versatile"
        assert cfg["api_key"] == "gsk_test_key_123"

    def test_resolve_backend_anthropic(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        cfg = resolve_backend()

        assert cfg is not None
        assert cfg["kind"] == "anthropic"
        assert cfg["model"] == "claude-sonnet-5"

    def test_resolve_backend_ollama(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "ollama")

        cfg = resolve_backend()

        assert cfg is not None
        assert cfg["kind"] == "openai"
        assert "localhost" in cfg["base_url"]
        assert cfg["api_key"] == "ollama"

    def test_resolve_backend_none(self, monkeypatch):
        _clear_llm_env(monkeypatch)

        cfg = resolve_backend()

        assert cfg is None

    def test_resolve_backend_explicit_provider(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "gsk_explicit")

        cfg = resolve_backend()

        assert cfg is not None
        assert cfg["kind"] == "openai"
        assert "groq.com" in cfg["base_url"]
        assert cfg["api_key"] == "gsk_explicit"

    def test_resolve_backend_fallback_chain(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_FALLBACK_CHAIN", "groq,anthropic")
        monkeypatch.setenv("GROQ_API_KEY", "gsk_chain")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-chain")

        cfg = resolve_backend()

        assert isinstance(cfg, list)
        assert len(cfg) == 2
        assert cfg[0]["provider"] == "groq"
        assert cfg[1]["provider"] == "anthropic"


class TestDispatch:
    """Verify _dispatch handles unknown tools gracefully."""

    def test_dispatch_unknown_tool(self):
        result = _dispatch("nonexistent_tool", {"arg": "value"})
        assert isinstance(result, dict)
        assert "error" in result


class TestSanitizeAlert:
    """Alert payload must not leak ground-truth hints to the LLM."""

    def test_metrics_and_expected_stripped(self):
        from autosre.agent import _sanitize_alert

        scenario = SCENARIOS["db"]
        alert = _sanitize_alert(scenario)
        assert "metrics" not in alert
        assert "expected_root_cause" not in alert
        assert "expected_remediation" not in alert
        assert "alert_id" in alert
        assert "description" in alert


class TestDataIntegrity:
    """Verify that SCENARIOS and TOOLS are imported and well-formed."""

    def test_scenarios_imported(self):
        assert isinstance(SCENARIOS, dict)
        assert len(SCENARIOS) > 0

    def test_tool_definitions_valid(self):
        assert isinstance(TOOLS, list)
        assert len(TOOLS) > 0
        for tool in TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            schema = tool["input_schema"]
            assert schema.get("type") == "object"
            assert "properties" in schema
            if schema["properties"]:
                assert "required" in schema
