"""Tests for the invariant-enforcement gate `run_pipeline` applies after
global assignment.

Found by code review (see DEVLOG): `verify_settlegraph_invariants` was being
called on every AUTO_MATCH and its violations counted into
`summary["invariant_violations"]`, but nothing ever acted on that count --
the assignment kept its AUTO_MATCH label regardless. `report.py`'s own audit
report claims "any violation demotes to exception queue"; this is the
function that has to make that claim true.
"""

from __future__ import annotations

from datetime import date

from settlegraph.config import PipelineConfig
from settlegraph.engine.pipeline import deduplicate_normalized_records, enforce_invariant_gate
from settlegraph.models import NormalizedRecord


def _rzp(
    record_id: str = "rzp_norm_1", net: int = 9764, source_record_id: str = "pay_1"
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        source="razorpay",
        source_record_id=source_record_id,
        record_type="payment",
        utr="RZP001",
        amount_paise=10000,
        net_amount_paise=net,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        settlement_date=date(2026, 1, 17),
        raw_record={},
        provenance={"source": "razorpay"},
    )


def _bank(
    record_id: str = "bank_norm_1",
    amount: int = 9764,
    direction: str = "credit",
    transaction_date: date = date(2026, 1, 17),
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        source="bank",
        source_record_id="bank_1",
        record_type="settlement_credit" if direction == "credit" else "adjustment",
        utr="RZP001",
        amount_paise=amount,
        net_amount_paise=amount,
        currency="INR",
        transaction_date=transaction_date,
        settlement_date=transaction_date,
        raw_record={},
        provenance={"source": "bank", "direction": direction},
    )


def _assignment(
    a: NormalizedRecord, b: NormalizedRecord, label: str, confidence: float = 0.98
) -> dict:
    return {
        "source_a": a.source,
        "source_a_id": a.record_id,
        "source_b": b.source,
        "source_b_id": b.record_id,
        "confidence": confidence,
        "label": label,
        "a_amount_paise": a.amount_paise,
        "b_amount_paise": b.amount_paise,
        "a_utr": a.utr,
        "b_utr": b.utr,
        "a_order_id": a.order_id,
        "b_order_id": b.order_id,
    }


def test_auto_match_failing_direction_invariant_is_demoted_to_exception() -> None:
    """A bank debit line (e.g. a refund payout) that happens to share a UTR,
    amount, and date with a settlement can still score >= auto_match_threshold
    -- scoring never looks at direction. The invariant gate is the only thing
    standing between that and a corrupted ledger entry."""
    config = PipelineConfig()
    rzp = _rzp()
    bank = _bank(direction="debit")
    assignments = [_assignment(rzp, bank, "AUTO_MATCH")]
    norm_map = {rzp.record_id: rzp, bank.record_id: bank}

    result_assignments, exception_reports, violation_count = enforce_invariant_gate(
        assignments, norm_map, config
    )

    assert result_assignments[0]["label"] == "EXCEPTION"
    assert violation_count >= 1
    assert len(exception_reports) == 1
    report = exception_reports[0]
    assert report.record_id == rzp.record_id
    assert report.category == "INVARIANT_VIOLATION"
    assert "Direction violation" in report.root_cause


def test_auto_match_passing_invariants_is_left_untouched() -> None:
    config = PipelineConfig()
    rzp = _rzp()
    bank = _bank(direction="credit")
    assignments = [_assignment(rzp, bank, "AUTO_MATCH")]
    norm_map = {rzp.record_id: rzp, bank.record_id: bank}

    result_assignments, exception_reports, violation_count = enforce_invariant_gate(
        assignments, norm_map, config
    )

    assert result_assignments[0]["label"] == "AUTO_MATCH"
    assert violation_count == 0
    assert exception_reports == []


def test_non_auto_match_assignments_are_not_checked() -> None:
    """LIKELY_MATCH and EXCEPTION rows already went through the deterministic
    scorer's own judgment -- this gate only guards the label that means
    'booked automatically, no human ever looks at it'."""
    config = PipelineConfig()
    rzp = _rzp()
    bank = _bank(direction="debit")  # would fail the invariant if checked
    assignments = [_assignment(rzp, bank, "LIKELY_MATCH", confidence=0.80)]
    norm_map = {rzp.record_id: rzp, bank.record_id: bank}

    result_assignments, exception_reports, violation_count = enforce_invariant_gate(
        assignments, norm_map, config
    )

    assert result_assignments[0]["label"] == "LIKELY_MATCH"
    assert violation_count == 0
    assert exception_reports == []


def test_duplicate_webhook_replay_is_filtered_before_candidate_generation() -> None:
    """`IdempotencyShield` (engine/idempotency.py, ADR 0004) was fully built
    and unit-tested but never called from `run_pipeline` -- ADR 0004's
    "guarantees zero duplicate ledger entries under network retry storms"
    was true of the class in isolation, not of the pipeline that ships it.
    A retried settlement webhook landing twice in the real ingest data used
    to flow straight through scoring/matching/revenue-assurance ungated."""
    rzp = [_rzp(record_id="rzp_norm_1"), _rzp(record_id="rzp_norm_1_replay")]
    bank = [_bank(record_id="bank_norm_1"), _bank(record_id="bank_norm_1_replay")]
    merch: list[NormalizedRecord] = []

    rzp_unique, bank_unique, merch_unique, duplicate_events = deduplicate_normalized_records(
        rzp, bank, merch
    )

    assert [r.record_id for r in rzp_unique] == ["rzp_norm_1"]
    assert [r.record_id for r in bank_unique] == ["bank_norm_1"]
    assert merch_unique == []
    assert len(duplicate_events) == 2


def test_no_duplicates_leaves_every_record_untouched() -> None:
    rzp = [
        _rzp(record_id="rzp_norm_1", source_record_id="pay_1"),
        _rzp(record_id="rzp_norm_2", source_record_id="pay_2"),
    ]
    bank = [_bank(record_id="bank_norm_1")]
    merch: list[NormalizedRecord] = []

    rzp_unique, bank_unique, merch_unique, duplicate_events = deduplicate_normalized_records(
        rzp, bank, merch
    )

    assert len(rzp_unique) == 2
    assert len(bank_unique) == 1
    assert duplicate_events == []


def test_razorpay_merchant_auto_matches_are_skipped() -> None:
    """verify_settlegraph_invariants only knows how to check a razorpay<->bank
    leg (see its own docstring) -- a razorpay<->merchant AUTO_MATCH must pass
    through unexamined, not crash or get spuriously demoted."""
    config = PipelineConfig()
    rzp = _rzp()
    merch = NormalizedRecord(
        record_id="merch_norm_1",
        source="merchant",
        source_record_id="led_1",
        record_type="sale",
        order_id="order_1",
        amount_paise=10000,
        net_amount_paise=10000,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        raw_record={},
        provenance={"source": "merchant"},
    )
    assignments = [_assignment(rzp, merch, "AUTO_MATCH")]
    norm_map = {rzp.record_id: rzp, merch.record_id: merch}

    result_assignments, exception_reports, violation_count = enforce_invariant_gate(
        assignments, norm_map, config
    )

    assert result_assignments[0]["label"] == "AUTO_MATCH"
    assert violation_count == 0
    assert exception_reports == []
