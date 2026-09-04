"""Tests for the exception triage table (PDF page 13 style)."""

from __future__ import annotations

import json

from settlegraph.engine.report import build_exception_triage_table


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
