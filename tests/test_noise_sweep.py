"""Unit tests for scripts/noise_sweep.py's pure helper functions.

Deliberately does NOT run a real noise sweep (that means generating data and
running the full pipeline several times over) -- see scripts/noise_sweep.py
itself, run manually, for that. These tests exercise `analyze_breaking_point`,
`format_breaking_point_analysis`, `format_table`, and `parse_levels` directly
against synthetic metric rows, which is exactly why noise_sweep.py keeps its
analysis logic as pure functions separate from any I/O.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.noise_sweep import (  # noqa: E402
    analyze_breaking_point,
    format_breaking_point_analysis,
    format_table,
    parse_levels,
)


def _row(
    anomaly_rate: float,
    precision: float = 1.0,
    abstention_rate: float = 0.0,
    **overrides,
) -> dict:
    base = {
        "anomaly_rate": anomaly_rate,
        "precision": precision,
        "recall": 0.9,
        "f1": 0.9,
        "safe_auto_resolution_rate": 0.9,
        "false_auto_book_rate": 0.0,
        "dangerous_miss_rate": 0.0,
        "exception_recall": 1.0,
        "auto_match": 100,
        "likely_match": 10,
        "exception": 5,
        "abstention_rate": abstention_rate,
        "throughput_rps": 500.0,
    }
    base.update(overrides)
    return base


# --- parse_levels -----------------------------------------------------------


def test_parse_levels_parses_and_sorts() -> None:
    assert parse_levels("0.10,0.0,0.05") == [0.0, 0.05, 0.10]


def test_parse_levels_rejects_empty() -> None:
    try:
        parse_levels("")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty --levels")


# --- analyze_breaking_point: healthy curve ----------------------------------


def test_healthy_curve_precision_held_abstention_rose() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.05, precision=1.0, abstention_rate=0.04),
        _row(0.10, precision=1.0, abstention_rate=0.08),
        _row(0.20, precision=1.0, abstention_rate=0.15),
        _row(0.30, precision=1.0, abstention_rate=0.25),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["verdict"] == "HEALTHY"
    assert analysis["reasons"] == []
    assert analysis["abstention_rose"] is True
    assert analysis["precision_collapsed"] is False
    assert analysis["first_precision_drop_level"] is None


def test_healthy_curve_survives_a_tiny_precision_dip_that_is_not_a_collapse() -> None:
    # Precision drops below 100% but not below the collapse threshold --
    # the sweep still reports the first-drop level, but the verdict stays
    # healthy because that alone isn't the failure mode being guarded
    # against.
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.10, precision=0.99, abstention_rate=0.06),
        _row(0.20, precision=0.98, abstention_rate=0.12),
        _row(0.30, precision=0.97, abstention_rate=0.20),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["verdict"] == "HEALTHY"
    assert analysis["first_precision_drop_level"] == 0.10
    assert analysis["precision_collapsed"] is False


# --- analyze_breaking_point: precision-collapse curve -----------------------


def test_precision_collapse_curve_is_unhealthy() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.05, precision=0.95, abstention_rate=0.05),
        _row(0.10, precision=0.80, abstention_rate=0.10),
        _row(0.20, precision=0.60, abstention_rate=0.15),
        _row(0.30, precision=0.40, abstention_rate=0.20),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["verdict"] == "UNHEALTHY"
    assert analysis["precision_collapsed"] is True
    assert analysis["first_precision_drop_level"] == 0.05
    assert any("collapsed" in reason for reason in analysis["reasons"])


# --- analyze_breaking_point: flat-abstention curve --------------------------


def test_flat_abstention_curve_is_unhealthy_even_with_perfect_precision() -> None:
    # Precision never wavers, but the system auto-matches at the exact same
    # rate regardless of how noisy the input gets -- exactly the dangerous
    # "confidently auto-matching bad data" failure mode this harness exists
    # to catch.
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.05),
        _row(0.05, precision=1.0, abstention_rate=0.05),
        _row(0.10, precision=1.0, abstention_rate=0.05),
        _row(0.20, precision=1.0, abstention_rate=0.05),
        _row(0.30, precision=1.0, abstention_rate=0.05),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["verdict"] == "UNHEALTHY"
    assert analysis["abstention_rose"] is False
    assert analysis["precision_collapsed"] is False
    assert any("did not meaningfully rise" in reason for reason in analysis["reasons"])


def test_declining_abstention_curve_is_unhealthy() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.20),
        _row(0.10, precision=1.0, abstention_rate=0.10),
        _row(0.30, precision=1.0, abstention_rate=0.02),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["verdict"] == "UNHEALTHY"
    assert analysis["abstention_rose"] is False


def test_analyze_breaking_point_is_order_independent() -> None:
    rows = [
        _row(0.30, precision=1.0, abstention_rate=0.25),
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.10, precision=1.0, abstention_rate=0.08),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["abstention_start"] == 0.01
    assert analysis["abstention_end"] == 0.25
    assert analysis["verdict"] == "HEALTHY"


def test_analyze_breaking_point_rejects_empty_rows() -> None:
    try:
        analyze_breaking_point([])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty rows")


# --- format_breaking_point_analysis -----------------------------------------


def test_format_breaking_point_analysis_names_first_drop_level() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.10, precision=0.90, abstention_rate=0.10),
    ]
    text = format_breaking_point_analysis(analyze_breaking_point(rows))
    assert "BREAKING POINT ANALYSIS" in text
    assert "0.10" in text
    assert "VERDICT: UNHEALTHY" in text


def test_format_breaking_point_analysis_healthy_verdict_text() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.30, precision=1.0, abstention_rate=0.20),
    ]
    text = format_breaking_point_analysis(analyze_breaking_point(rows))
    assert "VERDICT: HEALTHY" in text
    assert "never dropped below 100%" in text


# --- format_table ------------------------------------------------------------


def test_format_table_has_header_and_one_row_per_level() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.10, precision=1.0, abstention_rate=0.05),
        _row(0.30, precision=1.0, abstention_rate=0.20),
    ]
    text = format_table(rows)
    lines = text.splitlines()
    # header + separator + 3 data rows
    assert len(lines) == 5
    assert "Precision" in lines[0]
    assert "Abstain" in lines[0]


def test_format_table_orders_rows_by_noise_level_regardless_of_input_order() -> None:
    rows = [
        _row(0.30, precision=1.0, abstention_rate=0.20),
        _row(0.0, precision=1.0, abstention_rate=0.01),
    ]
    text = format_table(rows)
    data_lines = text.splitlines()[2:]
    assert data_lines[0].strip().startswith("0.00")
    assert data_lines[1].strip().startswith("0.30")


def test_format_table_renders_missing_throughput_as_placeholder() -> None:
    rows = [_row(0.0, throughput_rps=None)]
    text = format_table(rows)
    assert "?" in text.splitlines()[2]
