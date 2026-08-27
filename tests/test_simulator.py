"""Tests for the failure injection simulator."""

from __future__ import annotations

from settlegraph.engine.simulator import simulate_all_failures


def test_all_failure_simulations_pass() -> None:
    results = simulate_all_failures()

    assert len(results) == 5
    for r in results:
        assert r.passed, f"Scenario {r.scenario_id} ({r.name}) failed safe containment check!"
