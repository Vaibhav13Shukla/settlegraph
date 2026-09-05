"""Unit tests for scripts/eval_holdout.py's pure helper functions.

Deliberately does NOT run a real held-out evaluation (that means generating
a dataset with splits and running the full pipeline plus three baselines) --
see scripts/eval_holdout.py itself, run manually, for that. These tests
exercise `build_row`, `format_comparison_table`, `format_calibration_section`,
`analyze_holdout_verdict`, and `format_verdict_section` directly against
synthetic metric dicts, the same discipline tests/test_noise_sweep.py uses
for scripts/noise_sweep.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.eval_holdout import (  # noqa: E402
    DEV_REFERENCE,
    analyze_holdout_verdict,
    build_row,
    format_calibration_section,
    format_comparison_table,
    format_verdict_section,
)


def _evaluation(
    precision: float = 1.0,
    recall: float = 0.85,
    **overrides,
) -> dict:
    base = {
        "precision": precision,
        "recall": recall,
        "f1": 0.9,
        "true_positives": 100,
        "false_positives": 0,
        "false_auto_book_rate": 0.0,
        "dangerous_miss_rate": 0.0,
        "exception_recall": 1.0,
        "safe_auto_resolution_rate": 0.9,
    }
    base.update(overrides)
    return base


def _holdout_row(
    precision: float = 1.0,
    recall: float = 0.85,
    false_auto_book_rate: float = 0.0,
    abstention_precision: float | None = 0.02,
    **overrides,
) -> dict:
    row = build_row(
        "SettleGraph",
        _evaluation(precision=precision, recall=recall, false_auto_book_rate=false_auto_book_rate),
        auto_count=100,
        likely_count=10,
        exception_count=5,
        throughput_rps=500.0,
    )
    if abstention_precision is not None:
        row["abstention_precision"] = abstention_precision
    row.update(overrides)
    return row


# --- build_row ---------------------------------------------------------------


def test_build_row_flattens_evaluation_and_counts() -> None:
    row = build_row(
        "SettleGraph",
        _evaluation(precision=1.0, recall=0.83),
        auto_count=10,
        likely_count=2,
        exception_count=1,
        throughput_rps=1234.5,
    )
    assert row["approach"] == "SettleGraph"
    assert row["precision"] == 1.0
    assert row["recall"] == 0.83
    assert row["auto_count"] == 10
    assert row["likely_count"] == 2
    assert row["exception_count"] == 1
    assert row["throughput_rps"] == 1234.5


def test_build_row_defaults_missing_evaluation_fields_to_zero() -> None:
    row = build_row(
        "Baseline A", {}, auto_count=0, likely_count=0, exception_count=0, throughput_rps=None
    )
    assert row["precision"] == 0.0
    assert row["recall"] == 0.0
    assert row["throughput_rps"] is None


# --- format_comparison_table ---------------------------------------------------


def test_format_comparison_table_has_header_and_one_row_per_approach() -> None:
    rows = [
        _holdout_row(),
        build_row("Baseline A", _evaluation(precision=1.0, recall=0.8), 80, 0, 0, 400.0),
    ]
    text = format_comparison_table(rows)
    lines = text.splitlines()
    assert len(lines) == 4  # header + separator + 2 rows
    assert "Approach" in lines[0]
    assert "Precision" in lines[0]
    assert "SettleGraph" in lines[2]
    assert "Baseline A" in lines[3]


def test_format_comparison_table_renders_missing_throughput_as_placeholder() -> None:
    row = build_row("Baseline C", _evaluation(), 0, 0, 0, None)
    text = format_comparison_table([row])
    assert "?" in text.splitlines()[2]


def test_format_comparison_table_preserves_row_order() -> None:
    rows = [
        build_row("First", _evaluation(), 1, 0, 0, 1.0),
        build_row("Second", _evaluation(), 2, 0, 0, 2.0),
    ]
    text = format_comparison_table(rows)
    data_lines = text.splitlines()[2:]
    assert data_lines[0].strip().startswith("First")
    assert data_lines[1].strip().startswith("Second")


# --- format_calibration_section ------------------------------------------------


def test_format_calibration_section_reports_key_numbers() -> None:
    calibration = {"expected_calibration_error": 0.03, "brier_score": 0.008, "total_scored": 958}
    abstention = {
        "abstentions": 122,
        "justified_abstentions": 2,
        "unjustified_abstentions": 120,
        "abstention_precision": 0.0164,
        "abstention_rate": 0.1291,
    }
    text = format_calibration_section(calibration, abstention)
    assert "0.0300" in text
    assert "0.0080" in text
    assert "122" in text
    assert "0.0164" in text
    assert "12.91%" in text


def test_format_calibration_section_handles_empty_dicts() -> None:
    # Must never raise on an all-zero/empty result (e.g. a split with no
    # razorpay<->bank assignments at all).
    text = format_calibration_section({}, {})
    assert "CALIBRATION" in text


# --- analyze_holdout_verdict: healthy -----------------------------------------


def test_healthy_when_holdout_matches_development() -> None:
    holdout = _holdout_row(precision=1.0, recall=DEV_REFERENCE["recall"] + 0.01)
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    assert verdict["verdict"] == "HEALTHY"
    assert verdict["reasons"] == []
    assert verdict["precision_held"] is True
    assert verdict["recall_degraded"] is False


def test_healthy_when_holdout_recall_slightly_better_than_development() -> None:
    holdout = _holdout_row(precision=1.0, recall=0.95)
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    assert verdict["verdict"] == "HEALTHY"


def test_healthy_survives_a_small_recall_dip_within_tolerance() -> None:
    # Development recall is 0.8364; a drop of ~2pp is within the 5pp
    # material-degradation threshold and should not trigger OVERFITTING.
    holdout = _holdout_row(precision=1.0, recall=0.82)
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    assert verdict["verdict"] == "HEALTHY"


# --- analyze_holdout_verdict: overfitting --------------------------------------


def test_overfitting_when_precision_drops_below_100_percent() -> None:
    holdout = _holdout_row(precision=0.95, recall=DEV_REFERENCE["recall"])
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    assert verdict["verdict"] == "OVERFITTING"
    assert verdict["precision_held"] is False
    assert any("precision" in reason for reason in verdict["reasons"])


def test_overfitting_when_recall_drops_materially() -> None:
    # More than RECALL_DEGRADATION_DELTA (0.05) below the development number.
    holdout = _holdout_row(precision=1.0, recall=DEV_REFERENCE["recall"] - 0.20)
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    assert verdict["verdict"] == "OVERFITTING"
    assert verdict["recall_degraded"] is True
    assert any("recall dropped" in reason for reason in verdict["reasons"])


def test_overfitting_when_false_auto_book_rate_regresses() -> None:
    holdout = _holdout_row(precision=1.0, recall=DEV_REFERENCE["recall"], false_auto_book_rate=0.02)
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    assert verdict["verdict"] == "OVERFITTING"
    assert verdict["false_book_regressed"] is True
    assert any("false_auto_book_rate" in reason for reason in verdict["reasons"])


def test_held_out_much_worse_triggers_overfitting_with_multiple_reasons() -> None:
    holdout = _holdout_row(precision=0.80, recall=0.40, false_auto_book_rate=0.10)
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    assert verdict["verdict"] == "OVERFITTING"
    assert len(verdict["reasons"]) >= 2


def test_analyze_holdout_verdict_tolerates_missing_abstention_precision() -> None:
    holdout = build_row("SettleGraph", _evaluation(precision=1.0, recall=0.9), 10, 1, 0, 500.0)
    assert "abstention_precision" not in holdout
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    assert verdict["abstention_precision_delta"] is None
    assert verdict["verdict"] == "HEALTHY"


# --- format_verdict_section -----------------------------------------------------


def test_format_verdict_section_healthy_text() -> None:
    holdout = _holdout_row(precision=1.0, recall=DEV_REFERENCE["recall"])
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    text = format_verdict_section(verdict, holdout, DEV_REFERENCE)
    assert "HELD-OUT VERDICT" in text
    assert "VERDICT: HEALTHY" in text
    assert "Precision held at 100.00%" in text


def test_format_verdict_section_overfitting_text_names_reasons() -> None:
    holdout = _holdout_row(precision=0.90, recall=0.30, false_auto_book_rate=0.05)
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    text = format_verdict_section(verdict, holdout, DEV_REFERENCE)
    assert "VERDICT: OVERFITTING" in text
    assert "did NOT hold" in text
    for reason in verdict["reasons"]:
        assert reason in text


def test_format_verdict_section_includes_abstention_precision_when_present() -> None:
    holdout = _holdout_row(precision=1.0, recall=DEV_REFERENCE["recall"], abstention_precision=0.05)
    verdict = analyze_holdout_verdict(holdout, DEV_REFERENCE)
    text = format_verdict_section(verdict, holdout, DEV_REFERENCE)
    assert "Abstention precision" in text
