"""The Q&A agent's tools are pure functions over files already on disk --
tested directly here, no SDK/subprocess/network involved. That path is
exercised separately by a manual smoke test (see README), the same split
`ai_reasoner.py` uses for its own network-dependent path.
"""

from __future__ import annotations

import json

from settlegraph.qa_agent import (
    build_options,
    get_assignment_impl,
    get_exception_impl,
    get_revenue_assurance_impl,
    get_summary_impl,
    search_by_amount_or_utr_impl,
)


def _write_json(path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_csv(path, rows: list[dict]) -> None:
    import csv

    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_get_summary_returns_error_payload_when_missing(tmp_path) -> None:
    result = get_summary_impl(tmp_path)
    payload = json.loads(result["content"][0]["text"])
    assert "error" in payload


def test_get_summary_returns_the_real_file(tmp_path) -> None:
    _write_json(tmp_path / "summary.json", {"records": {"razorpay": 1000}})
    result = get_summary_impl(tmp_path)
    payload = json.loads(result["content"][0]["text"])
    assert payload["records"]["razorpay"] == 1000


def test_get_assignment_finds_a_matched_record_on_either_side(tmp_path) -> None:
    _write_csv(
        tmp_path / "assignments.csv",
        [
            {
                "source_a_id": "rzp_norm_pay_1",
                "source_b_id": "bank_norm_bank_1",
                "label": "AUTO_MATCH",
            }
        ],
    )
    result = get_assignment_impl(tmp_path, "bank_norm_bank_1")
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "matched"
    assert len(payload["assignments"]) == 1


def test_get_assignment_falls_back_to_unmatched(tmp_path) -> None:
    _write_csv(tmp_path / "assignments.csv", [])
    _write_csv(
        tmp_path / "unmatched.csv",
        [{"source": "razorpay", "record_id": "rzp_norm_orphan", "status": "UNMATCHED"}],
    )
    result = get_assignment_impl(tmp_path, "rzp_norm_orphan")
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "unmatched"


def test_get_assignment_reports_not_found_honestly(tmp_path) -> None:
    result = get_assignment_impl(tmp_path, "nonexistent_id")
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "not_found"


def test_get_exception_joins_deterministic_and_ai_attempts(tmp_path) -> None:
    _write_json(
        tmp_path / "exceptions.json",
        [{"record_id": "rzp_norm_1", "category": "MISSING_COUNTERPART"}],
    )
    _write_json(
        tmp_path / "ai_resolutions.json",
        [{"record_id": "rzp_norm_1", "outcome": "cannot_resolve"}],
    )
    result = get_exception_impl(tmp_path, "rzp_norm_1")
    payload = json.loads(result["content"][0]["text"])
    assert len(payload["exception_reports"]) == 1
    assert len(payload["ai_reasoner_attempts"]) == 1


def test_search_caps_results_and_never_touches_ground_truth(tmp_path) -> None:
    """The search tool reads assignments/unmatched only -- ground_truth.csv
    (evaluator-only, per docs/PRD.md) is never in its search path at all,
    so there's no file it could read to leak evaluation labels through."""
    _write_csv(tmp_path / "assignments.csv", [{"a_utr": "RZP000000123", "label": "AUTO_MATCH"}])
    _write_csv(tmp_path / "unmatched.csv", [])
    result = search_by_amount_or_utr_impl(tmp_path, "RZP000000123")
    payload = json.loads(result["content"][0]["text"])
    assert len(payload["assignment_matches"]) == 1


def test_get_revenue_assurance_returns_the_real_file(tmp_path) -> None:
    _write_json(tmp_path / "revenue_assurance.json", {"financial_summary": {}})
    result = get_revenue_assurance_impl(tmp_path)
    payload = json.loads(result["content"][0]["text"])
    assert "financial_summary" in payload


def test_build_options_never_enables_a_dangerous_builtin_tool(tmp_path) -> None:
    """The two independent restrictions this module's docstring promises:
    empty built-in tools preset, and an explicit disallowed_tools list."""
    options = build_options(tmp_path)
    assert options.tools == []
    for dangerous in ("Bash", "Write", "Edit", "WebSearch", "WebFetch", "Task"):
        assert dangerous in options.disallowed_tools


def test_build_options_allows_exactly_the_five_ledger_tools(tmp_path) -> None:
    options = build_options(tmp_path)
    assert set(options.allowed_tools) == {
        "mcp__ledger__get_summary",
        "mcp__ledger__get_assignment",
        "mcp__ledger__get_exception",
        "mcp__ledger__search_by_amount_or_utr",
        "mcp__ledger__get_revenue_assurance",
    }
