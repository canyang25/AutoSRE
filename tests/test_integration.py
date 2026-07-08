"""Integration tests – scenario loading and offline simulation.

These tests verify that the scenario catalog is well-formed and that the
``simulate()`` function from ``trigger_fault`` runs through successfully for
every defined scenario.
"""

import pytest

from trigger_fault import SCENARIOS, simulate


# ---------------------------------------------------------------------------
# Scenario catalog validation
# ---------------------------------------------------------------------------

class TestScenarioLoading:
    """Verify the SCENARIOS dict has the expected structure."""

    EXPECTED_KEYS = {"db", "disk", "network"}
    REQUIRED_FIELDS = {"alert_id", "service", "description", "expected_root_cause"}

    def test_scenario_loading(self):
        """SCENARIOS contains all three expected scenario keys."""
        assert set(SCENARIOS.keys()) == self.EXPECTED_KEYS

    def test_scenario_has_required_fields(self):
        """Every scenario dict contains the mandatory fields."""
        for name, scenario in SCENARIOS.items():
            for field in self.REQUIRED_FIELDS:
                assert field in scenario, (
                    f"Scenario '{name}' is missing required field '{field}'"
                )


# ---------------------------------------------------------------------------
# Offline simulation
# ---------------------------------------------------------------------------

class TestSimulation:
    """Verify simulate() completes without error for all scenarios."""

    def test_simulate_mode(self):
        """simulate('db') returns 0 (success)."""
        assert simulate("db") == 0

    def test_simulate_all_scenarios(self):
        """simulate() returns 0 for every registered scenario."""
        for name in SCENARIOS:
            assert simulate(name) == 0, f"simulate('{name}') did not return 0"
