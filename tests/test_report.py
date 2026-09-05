"""Tests for the exception triage table (PDF page 13 style)."""

from __future__ import annotations

import json

from settlegraph.engine.report import build_exception_triage_table, generate_markdown_audit_report


def _write(path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_triage_table_ranks_by_severity_then_exposure(tmp_path) -> None:
    _write(
        tmp_path / "exceptions.json",
        [
            {
                "record_id": "low_1",
                "category": "TIMING_DIFFERENCE",
                "severity": "LOW",
                "root_cause": "x",
                "unexplained_amount_paise": 999999,
            },
            {
                "record_id": "high_1",
                "category": "MISSING_COUNTERPART",
                "severity": "HIGH",
                "root_cause": "y",
                "unexplained_amount_paise": 100,
            },
        ],
    )
    rows = build_exception_triage_table(tmp_path)
    assert rows[0]["transaction"] == "high_1"
    assert rows[1]["transaction"] == "low_1"


def test_triage_table_marks_promoted_ai_resolutions(tmp_path) -> None:
    _write(
        tmp_path / "exceptions.json",
        [
            {
                "record_id": "rzp_1",
                "category": "MISSING_COUNTERPART",
                "severity": "HIGH",
                "root_cause": "no candidate",
                "unexplained_amount_paise": 500,
            }
        ],
    )
    _write(
        tmp_path / "ai_resolutions.json",
        [
            {
                "record_id": "rzp_1",
                "candidate_record_id": "bank_1",
                "confidence": 0.9,
                "outcome": "promoted",
            }
        ],
    )
    rows = build_exception_triage_table(tmp_path)
    assert rows[0]["status"] == "AI-RESOLVED"
    assert "bank_1" in rows[0]["agent_attempt"]


def test_triage_table_marks_invariant_failures_as_escalated_not_hidden(tmp_path) -> None:
    """A rejected hypothesis must still show up as ESCALATED with the
    rejection visible -- burying a failed AI attempt would look like the
    exception was never investigated at all."""
    _write(
        tmp_path / "exceptions.json",
        [
            {
                "record_id": "rzp_2",
                "category": "MISSING_COUNTERPART",
                "severity": "HIGH",
                "root_cause": "no candidate",
                "unexplained_amount_paise": 500,
            }
        ],
    )
    _write(
        tmp_path / "ai_resolutions.json",
        [
            {
                "record_id": "rzp_2",
                "candidate_record_id": "bank_2",
                "confidence": 1.0,
                "outcome": "rejected_invariant_failure",
            }
        ],
    )
    rows = build_exception_triage_table(tmp_path)
    assert rows[0]["status"] == "ESCALATED"
    assert "FAILED invariant" in rows[0]["agent_attempt"]


def test_triage_table_handles_no_exceptions_file(tmp_path) -> None:
    assert build_exception_triage_table(tmp_path) == []


def test_audit_report_shows_safe_auto_resolution_and_false_auto_book_rate(tmp_path) -> None:
    """The Track 04 trust framing's headline pair must be visible on the
    audit report a finance controller actually reads, not just returned
    from evaluate() for a dashboard tile to pick up. Together they're the
    "how do I know this agent won't quietly mess up my books" answer."""
    summary = {"invariant_violations": 0}
    revenue_assurance = {
        "financial_summary": {
            "reconciliation_rate_percent": 90.0,
            "merchant_sales_inr": 100000.0,
            "razorpay_net_expected_inr": 97640.0,
            "bank_credits_observed_inr": 97640.0,
            "reconciled_settled_inr": 90000.0,
            "unexplained_exposure_inr": 10000.0,
        },
        "exception_categories": {},
    }
    evaluation = {
        "precision": 0.99,
        "recall": 0.9,
        "f1": 0.94,
        "true_positives": 90,
        "false_positives": 1,
        "false_match_rate": 0.011,
        "ai_assisted_matches": 0,
        "total_records": 100,
        "correct_auto_matches": 90,
        "false_auto_matches": 1,
        "safe_auto_resolution_rate": 0.9,
        "false_auto_book_rate": 0.01,
    }
    report = generate_markdown_audit_report(summary, revenue_assurance, evaluation, tmp_path)
    assert "Safe Auto-Resolution Rate" in report
    assert "90.0%" in report
    assert "False Auto-Book Rate" in report
    assert "1.00%" in report


def test_audit_report_shows_dangerous_miss_rate_and_exception_recall(tmp_path) -> None:
    """Day 7: `no_counterpart` (a settlement that genuinely never reached
    the bank) is now actually generated, and `evaluate()` measures whether
    the system correctly abstained from it or dangerously auto-matched it
    anyway. This is exactly the failure mode the whole project is framed
    around -- it has to be visible on the report a finance controller
    reads, not just sitting in evaluation.json."""
    summary = {"invariant_violations": 0}
    revenue_assurance = {
        "financial_summary": {
            "reconciliation_rate_percent": 90.0,
            "merchant_sales_inr": 100000.0,
            "razorpay_net_expected_inr": 97640.0,
            "bank_credits_observed_inr": 97640.0,
            "reconciled_settled_inr": 90000.0,
            "unexplained_exposure_inr": 10000.0,
        },
        "exception_categories": {},
    }
    evaluation = {
        "precision": 0.99,
        "recall": 0.9,
        "f1": 0.94,
        "true_positives": 90,
        "false_positives": 1,
        "false_match_rate": 0.011,
        "ai_assisted_matches": 0,
        "total_records": 100,
        "correct_auto_matches": 90,
        "false_auto_matches": 1,
        "safe_auto_resolution_rate": 0.9,
        "false_auto_book_rate": 0.01,
        "no_counterpart_total": 5,
        "dangerous_misses": 1,
        "dangerous_miss_rate": 0.2,
        "exception_recall": 0.8,
    }
    report = generate_markdown_audit_report(summary, revenue_assurance, evaluation, tmp_path)
    assert "Dangerous Miss Rate" in report
    assert "20.0%" in report
    assert "Exception Recall" in report
    assert "80.0%" in report
    assert "5" in report  # the n=5 denominator must be visible, not hidden behind the rate


def test_triage_table_respects_limit(tmp_path) -> None:
    exceptions = [
        {
            "record_id": f"r_{i}",
            "category": "AMBIGUOUS_MATCH",
            "severity": "MEDIUM",
            "root_cause": "x",
            "unexplained_amount_paise": i,
        }
        for i in range(30)
    ]
    _write(tmp_path / "exceptions.json", exceptions)
    rows = build_exception_triage_table(tmp_path, limit=5)
    assert len(rows) == 5
    assert rows[0]["total_exceptions"] == "30"
