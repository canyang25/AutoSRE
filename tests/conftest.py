"""Shared pytest fixtures for the AutoSRE test suite."""

import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Make the project root importable so tests can ``import agent``, etc.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.mock_prometheus import app as prometheus_app  # noqa: E402
from tools.mock_elk import app as elk_app  # noqa: E402
from tools.mock_ansible import app as ansible_app  # noqa: E402
from trigger_fault import SCENARIOS  # noqa: E402


# ---------------------------------------------------------------------------
# Flask test-client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def prometheus_client():
    """Flask test client for the mock Prometheus service."""
    prometheus_app.config["TESTING"] = True
    with prometheus_app.test_client() as client:
        yield client


@pytest.fixture()
def elk_client():
    """Flask test client for the mock ELK service."""
    elk_app.config["TESTING"] = True
    with elk_app.test_client() as client:
        yield client


@pytest.fixture()
def ansible_client():
    """Flask test client for the mock Ansible service."""
    ansible_app.config["TESTING"] = True
    with ansible_app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_scenario():
    """Return the 'db' scenario dict from SCENARIOS."""
    return SCENARIOS["db"]


# ---------------------------------------------------------------------------
# Environment cleanup (autouse)
# ---------------------------------------------------------------------------

_ENV_KEYS_TO_CLEAN = [
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "LLM_PROVIDER",
    "GROQ_MODEL",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OPENAI_MODEL",
    "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def env_cleanup():
    """Save and restore environment variables that tests might touch."""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS_TO_CLEAN}
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
