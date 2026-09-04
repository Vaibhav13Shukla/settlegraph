"""Tests for the plain-language merchant digest."""

from __future__ import annotations

import json

from settlegraph.engine.digest import build_digest


def _write(path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_digest_reports_no_results_honestly(tmp_path) -> None:
    text = build_digest(tmp_path)
    assert "No results found" in text


def test_digest_includes_core_numbers(tmp_path) -> None:
    _write(
        tmp_path / "summary.json",
        {
            "records": {"razorpay": 100, "bank": 100, "merchant": 100},
            "assignments": {"auto_match": 80, "ai_resolved_match": 2, "exception": 3},
            "evaluation": {"precision": 1.0, "false_positives": 0},
            "revenue_assurance": {
                "financial_summary": {
                    "reconciled_settled_inr": 1000.0,
                    "unexplained_exposure_inr": 50.0,
                },
                "forward_cash_position": {"forward_cash_position_inr": 950.0},
            },
            "drift": {"drift_detected": False},
        },
    )
    text = build_digest(tmp_path)
    assert "300" in text  # total records
    assert "80" in text  # auto matched
    assert "100.0%" in text  # precision
    assert "950.00" in text  # forward cash position


def test_digest_omits_sections_with_no_data_rather_than_fabricating(tmp_path) -> None:
    """No tax_reconciliation.json or route_reconciliation.json on disk ->
    those sections must not appear at all, not appear with zeros."""
    _write(
        tmp_path / "summary.json",
        {
            "records": {"razorpay": 10, "bank": 10, "merchant": 10},
            "assignments": {"auto_match": 8, "ai_resolved_match": 0, "exception": 1},
            "evaluation": {},
            "revenue_assurance": {},
            "drift": {},
        },
    )
    text = build_digest(tmp_path)
    assert "GST" not in text
    assert "Route" not in text


def test_digest_flags_detected_drift() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        _write(
            tmp_path / "summary.json",
            {
                "records": {"razorpay": 10},
                "assignments": {"auto_match": 8, "ai_resolved_match": 0, "exception": 1},
                "evaluation": {},
                "revenue_assurance": {},
                "drift": {"drift_detected": True},
            },
        )
        text = build_digest(tmp_path)
        assert "shift in the score distribution" in text
