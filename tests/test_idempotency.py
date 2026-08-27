"""Tests for the idempotency and deduplication shield."""

from __future__ import annotations

from datetime import date

from settlegraph.engine.idempotency import IdempotencyShield
from settlegraph.models import NormalizedRecord


def _make_record(record_id: str, source: str = "razorpay", amount: int = 10000) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        source=source,
        source_record_id="orig_123",
        record_type="payment",
        amount_paise=amount,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        raw_record={},
        provenance={"source": source},
    )


def test_idempotency_shield_accepts_unique_records() -> None:
    shield = IdempotencyShield()
    r1 = _make_record("rzp_1", amount=10000)
    r2 = _make_record("rzp_2", amount=20000)
    # Different source_record_id
    r2.source_record_id = "orig_456"

    unique, dups = shield.filter_duplicates([r1, r2])

    assert len(unique) == 2
    assert len(dups) == 0
    assert shield.duplicate_count == 0


def test_idempotency_shield_intercepts_exact_duplicates() -> None:
    shield = IdempotencyShield()
    r1 = _make_record("rzp_1", amount=10000)
    r2 = _make_record("rzp_1_duplicate", amount=10000)  # Same source_record_id, amount, date

    unique, dups = shield.filter_duplicates([r1, r2])

    assert len(unique) == 1
    assert len(dups) == 1
    assert shield.duplicate_count == 1
    assert dups[0].record_id == "rzp_1_duplicate"
    assert shield.intercepted_duplicates[0]["original_record_id"] == "rzp_1"
