"""Tests for edge scoring."""

from __future__ import annotations

from datetime import date

from settlegraph.engine.score import score_all, score_edge
from settlegraph.models import NormalizedRecord


def _make_rzp(
    utr: str | None = None,
    order_id: str | None = None,
    payment_id: str | None = None,
    amount: int = 10000,
    net: int = 9764,
    settlement_date: date | None = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id="rzp_1",
        source="razorpay",
        source_record_id="rzp_1",
        record_type="payment",
        payment_id=payment_id,
        order_id=order_id,
        settlement_id="setl_1",
        utr=utr,
        invoice_number=None,
        reference_text=None,
        amount_paise=amount,
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
    utr: str | None = None,
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
        utr=utr,
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


def test_utr_match_scores_higher() -> None:
    with_utr = score_edge(_make_rzp(utr="RZP001"), _make_bank(utr="RZP001"))
    without_utr = score_edge(_make_rzp(order_id="order_1"), _make_bank())

    assert with_utr > without_utr


def test_exact_amount_scores_higher() -> None:
    exact = score_edge(_make_rzp(utr="RZP001", net=9764), _make_bank(utr="RZP001", amount=9764))
    mismatched = score_edge(
        _make_rzp(utr="RZP001", net=9764), _make_bank(utr="RZP001", amount=9000)
    )

    assert exact > mismatched


def test_score_all_returns_correct_count() -> None:
    a = _make_rzp(utr="RZP001")
    b = _make_bank(utr="RZP001")
    candidates = [(a, b), (a, _make_bank(utr="DIFFERENT"))]

    scored = score_all(candidates)

    assert len(scored) == 2
    assert all(0 <= s <= 1.0 for _, _, s in scored)


def test_score_is_deterministic() -> None:
    a = _make_rzp(utr="RZP001", order_id="order_1")
    b = _make_bank(utr="RZP001")

    s1 = score_edge(a, b)
    s2 = score_edge(a, b)

    assert s1 == s2
