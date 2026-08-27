"""Tests for ADWIN drift detector."""

from __future__ import annotations

from settlegraph.engine.drift import ADWINDetector


def test_adwin_stationary_stream_no_drift() -> None:
    detector = ADWINDetector(delta=0.01)
    # Stream from stationary distribution around 0.95
    for _ in range(50):
        drift = detector.add_element(0.95)
        assert not drift


def test_adwin_detects_abrupt_distribution_shift() -> None:
    detector = ADWINDetector(delta=0.05)
    # Stream high confidence values
    for _ in range(30):
        detector.add_element(0.98)

    # Abrupt drop in settlement confidence (e.g. gateway outage or format change)
    drift_seen = False
    for _ in range(30):
        if detector.add_element(0.40):
            drift_seen = True
            break

    assert drift_seen
    assert len(detector.drift_history) > 0
    assert detector.drift_history[0]["mean_before"] > detector.drift_history[0]["mean_after"]
