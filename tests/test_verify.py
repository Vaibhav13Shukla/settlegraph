"""Tests for invariant verification."""

from __future__ import annotations

from datetime import date

import pytest

from settlegraph.config import PipelineConfig
from settlegraph.engine.verify import (
    InvariantViolation,
    verify_amount_invariant,
    verify_date_invariant,
    verify_settlegraph_invariants,
)
from settlegraph.models import NormalizedRecord


def _make_rzp(
    net: int = 9764,
    settlement_date: date | None = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id="rzp_1",
        source="razorpay",
        source_record_id="rzp_1",
        record_type="payment",
        payment_id=None,
        order_id=None,
        settlement_id="setl_1",
        utr="RZP001",
        invoice_number=None,
        reference_text=None,
        amount_paise=10000,
        fee_paise=200,
        tax_paise=36,
        net_amount_paise=net,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        settlement_date=settlement_date or date(2026, 1, 17),
        description=None,
        raw_record={},
        provenance={"source": "razorpay"},
    )


def _make_bank(
    amount: int = 9764,
    transaction_date: date | None = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id="bank_1",
        source="bank",
        source_record_id="bank_1",
        record_type="settlement_credit",
        payment_id=None,
        order_id=None,
        settlement_id=None,
        utr="RZP001",
        invoice_number=None,
        reference_text=None,
        amount_paise=amount,
        fee_paise=None,
        tax_paise=None,
        net_amount_paise=amount,
        currency="INR",
        transaction_date=transaction_date or date(2026, 1, 17),
        settlement_date=transaction_date or date(2026, 1, 17),
        description=None,
        raw_record={},
        provenance={"source": "bank"},
    )


def test_amount_invariant_passes_on_exact_match() -> None:
    rzp = _make_rzp(net=9764)
    bank = _make_bank(amount=9764)

    result = verify_amount_invariant(rzp, bank)
    assert result is True


def test_amount_invariant_raises_on_large_mismatch() -> None:
    rzp = _make_rzp(net=9764)
    bank = _make_bank(amount=9000)

    with pytest.raises(InvariantViolation, match="Amount mismatch"):
        verify_amount_invariant(rzp, bank)


def test_date_invariant_passes_on_same_day() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(settlement_date=date(2026, 1, 17))
    bank = _make_bank(transaction_date=date(2026, 1, 17))

    result = verify_date_invariant(rzp, bank, config)
    assert result is True


def test_date_invariant_raises_on_excessive_gap() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(settlement_date=date(2026, 1, 17))
    bank = _make_bank(transaction_date=date(2026, 1, 25))

    with pytest.raises(InvariantViolation, match="Date violation"):
        verify_date_invariant(rzp, bank, config)


def test_verify_settlegraph_invariants_returns_violations() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(net=9764)
    bank = _make_bank(amount=9000)  # Amount mismatch

    violations = verify_settlegraph_invariants(rzp, bank, config)

    assert len(violations) >= 1
    assert isinstance(violations[0], InvariantViolation)


def test_verify_settlegraph_invariants_empty_on_valid() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(net=9764, settlement_date=date(2026, 1, 17))
    bank = _make_bank(amount=9764, transaction_date=date(2026, 1, 17))

    violations = verify_settlegraph_invariants(rzp, bank, config)

    assert len(violations) == 0
