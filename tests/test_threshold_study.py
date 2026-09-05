"""Unit tests for scripts/threshold_study.py's pure helper functions.

Deliberately does NOT run a real threshold sweep (that means generating a
dataset with splits and running the full pipeline once per threshold) -- see
scripts/threshold_study.py itself, run manually, for that. These tests
exercise `build_threshold_row`, `format_threshold_table`,
`recommend_threshold`, and `format_recommendation_section` directly against
synthetic metric dicts, the same discipline tests/test_noise_sweep.py and
tests/test_eval_holdout.py use for their sibling scripts.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.threshold_study import (  # noqa: E402
    build_threshold_row,
    format_recommendation_section,
    format_threshold_table,
    parse_thresholds,
    recommend_threshold,
)


def _evaluation(
    precision: float = 1.0,
    recall: float = 0.85,
    true_positives: int = 100,
    false_positives: int = 0,
    **overrides,
) -> dict:
    base = {
        "precision": precision,
        "recall": recall,
        "f1": 0.9,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_auto_book_rate": 0.0,
        "dangerous_miss_rate": 0.0,
        "safe_auto_resolution_rate": 0.9,
    }
    base.update(overrides)
    return base


def _assignment_counts(auto: int = 100, likely: int = 10, exception: int = 5) -> dict:
    return {
        "auto_match": auto,
        "likely_match": likely,
        "exception": exception,
        "total": auto + likely + exception,
    }


def _abstention(precision: float = 0.02, rate: float = 0.12) -> dict:
    return {
        "abstentions": 15,
        "justified_abstentions": 1,
        "unjustified_abstentions": 14,
        "abstention_precision": precision,
        "abstention_rate": rate,
    }


def _row(
    threshold: float,
    precision: float = 1.0,
    recall: float = 0.85,
    false_positives: int = 0,
    abstention_precision: float = 0.02,
) -> dict:
    return build_threshold_row(
        threshold,
        _evaluation(precision=precision, recall=recall, false_positives=false_positives),
        _assignment_counts(),
        _abstention(precision=abstention_precision),
    )


# --- parse_thresholds ---------------------------------------------------------


def test_safe_but_gainless_lower_threshold_does_not_justify_a_change() -> None:
    """The case the first real sweep actually produced, and the reason this
    guard exists.

    Recall was identical (80.46%) from 0.80 through 0.95, so "the lowest
    threshold that keeps precision at 100%" recommended dropping the default
    from 0.95 to 0.80 for **+0.00pp recall and +0.0000 abstention precision**
    -- spending safety margin for zero measured return. The docstring on
    `recommend_threshold` always said a gainless-but-safe threshold is not a
    reason to touch a working default; the code did not enforce it.
    """
    rows = [
        _row(0.80, recall=0.8046, abstention_precision=0.0),
        _row(0.95, recall=0.8046, abstention_precision=0.0),
    ]

    rec = recommend_threshold(rows, current_threshold=0.95)

    assert rec["lowest_safe_threshold"] == 0.80
    assert rec["change_supported_by_evidence"] is False
    assert "buys nothing measurable" in rec["reason"]


def test_material_recall_gain_does_justify_a_change() -> None:
    """The complement: when a lower threshold genuinely buys throughput at
    zero false positives, the study must say so -- otherwise the guard would
    just be a way of never recommending anything."""
    rows = [
        _row(0.85, recall=0.90, abstention_precision=0.02),
        _row(0.95, recall=0.83, abstention_precision=0.02),
    ]

    rec = recommend_threshold(rows, current_threshold=0.95)

    assert rec["change_supported_by_evidence"] is True
    assert rec["recall_gain"] == 0.07


def test_parse_thresholds_splits_sorts_and_dedupes() -> None:
    assert parse_thresholds("0.95,0.80,0.90,0.80") == [0.80, 0.90, 0.95]


def test_parse_thresholds_rejects_empty_string() -> None:
    try:
        parse_thresholds("")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty --thresholds")


# --- build_threshold_row -------------------------------------------------------


def test_build_threshold_row_flattens_evaluation_assignments_and_abstention() -> None:
    row = build_threshold_row(
        0.90, _evaluation(precision=1.0, recall=0.83), _assignment_counts(90, 8, 2), _abstention()
    )
    assert row["threshold"] == 0.90
    assert row["precision"] == 1.0
    assert row["recall"] == 0.83
    assert row["auto_count"] == 90
    assert row["likely_count"] == 8
    assert row["exception_count"] == 2
    assert row["abstention_precision"] == 0.02


def test_build_threshold_row_defaults_missing_evaluation_fields_to_zero() -> None:
    row = build_threshold_row(0.90, {}, _assignment_counts(0, 0, 0), {})
    assert row["precision"] == 0.0
    assert row["recall"] == 0.0
    assert row["true_positives"] == 0
    assert row["false_positives"] == 0
    assert row["abstention_precision"] == 0.0


# --- format_threshold_table -----------------------------------------------------


def test_format_threshold_table_has_header_and_one_row_per_threshold() -> None:
    rows = [_row(0.95), _row(0.90)]
    text = format_threshold_table(rows)
    lines = text.splitlines()
    assert len(lines) == 4  # header + separator + 2 rows
    assert "Threshold" in lines[0]
    assert "Precision" in lines[0]


def test_format_threshold_table_sorts_ascending_by_threshold() -> None:
    rows = [_row(0.97), _row(0.80), _row(0.90)]
    text = format_threshold_table(rows)
    data_lines = text.splitlines()[2:]
    assert data_lines[0].strip().startswith("0.80")
    assert data_lines[1].strip().startswith("0.90")
    assert data_lines[2].strip().startswith("0.97")


# --- recommend_threshold: a lower threshold keeps zero FP -----------------------


def test_recommends_lower_threshold_when_it_keeps_zero_fp() -> None:
    rows = [
        _row(0.80, precision=1.0, false_positives=0, recall=0.90, abstention_precision=0.20),
        _row(0.90, precision=1.0, false_positives=0, recall=0.87, abstention_precision=0.10),
        _row(0.95, precision=1.0, false_positives=0, recall=0.84, abstention_precision=0.02),
    ]
    rec = recommend_threshold(rows, current_threshold=0.95)
    assert rec["lowest_safe_threshold"] == 0.80
    assert rec["change_supported_by_evidence"] is True
    assert rec["recall_gain"] == round(0.90 - 0.84, 4)
    assert rec["abstention_precision_gain"] == round(0.20 - 0.02, 4)


def test_recommendation_text_names_the_lower_threshold_and_gains() -> None:
    rows = [
        _row(0.85, precision=1.0, false_positives=0, recall=0.90, abstention_precision=0.15),
        _row(0.95, precision=1.0, false_positives=0, recall=0.84, abstention_precision=0.02),
    ]
    rec = recommend_threshold(rows, current_threshold=0.95)
    text = format_recommendation_section(rec)
    assert "RECOMMENDATION" in text
    assert "0.85" in text
    assert "YES" in text
    assert "confirmed on 'test'" in text.lower() or "confirm" in text.lower()


# --- recommend_threshold: every lower threshold introduces FP -------------------


def test_recommends_keeping_current_when_every_lower_threshold_has_fp() -> None:
    rows = [
        _row(0.80, precision=0.90, false_positives=5, recall=0.95),
        _row(0.90, precision=0.98, false_positives=1, recall=0.90),
        _row(0.95, precision=1.0, false_positives=0, recall=0.84, abstention_precision=0.02),
    ]
    rec = recommend_threshold(rows, current_threshold=0.95)
    assert rec["lowest_safe_threshold"] == 0.95
    assert rec["change_supported_by_evidence"] is False
    assert rec["recall_gain"] == 0.0
    assert rec["abstention_precision_gain"] == 0.0


def test_recommendation_text_says_no_when_keeping_current() -> None:
    rows = [
        _row(0.80, precision=0.90, false_positives=5, recall=0.95),
        _row(0.95, precision=1.0, false_positives=0, recall=0.84, abstention_precision=0.02),
    ]
    rec = recommend_threshold(rows, current_threshold=0.95)
    text = format_recommendation_section(rec)
    assert "NO:" in text
    assert "already the lowest" in text


# --- recommend_threshold: edge cases ---------------------------------------------


def test_recommend_threshold_when_no_row_clears_zero_fp_bar() -> None:
    rows = [
        _row(0.80, precision=0.90, false_positives=5, recall=0.95),
        _row(0.95, precision=0.99, false_positives=2, recall=0.84),
    ]
    rec = recommend_threshold(rows, current_threshold=0.95)
    assert rec["lowest_safe_threshold"] is None
    assert rec["change_supported_by_evidence"] is False
    assert rec["recall_gain"] is None
    assert rec["abstention_precision_gain"] is None


def test_recommend_threshold_when_current_default_not_in_swept_rows() -> None:
    rows = [
        _row(0.80, precision=1.0, false_positives=0, recall=0.90),
        _row(0.90, precision=1.0, false_positives=0, recall=0.87),
    ]
    rec = recommend_threshold(rows, current_threshold=0.95)
    assert rec["lowest_safe_threshold"] == 0.80
    assert rec["current_row"] is None
    assert rec["recall_gain"] is None
    assert rec["abstention_precision_gain"] is None
    assert rec["change_supported_by_evidence"] is False


def test_recommend_threshold_raises_on_empty_rows() -> None:
    try:
        recommend_threshold([], current_threshold=0.95)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty rows")


def test_format_recommendation_section_handles_no_safe_threshold() -> None:
    rows = [_row(0.80, precision=0.90, false_positives=5, recall=0.95)]
    rec = recommend_threshold(rows, current_threshold=0.95)
    text = format_recommendation_section(rec)
    assert "NONE" in text
    assert "Not computable" in text
