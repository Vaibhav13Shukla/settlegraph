"""Tests for the tax-line (GST) matcher."""

from __future__ import annotations

from datetime import datetime

from settlegraph.engine.tax_matcher import match_tax_lines, summarize
from settlegraph.models import GSTInvoiceRecord, RazorpaySettlementRecord


def _rzp(
    entity_id: str = "pay_1", fee: int = 1000, settlement_id: str = "setl_1"
) -> RazorpaySettlementRecord:
    amount = max(fee * 20, 1000)  # stays a valid (> 0) amount even when fee=0
    tax = round(fee * 0.18)
    return RazorpaySettlementRecord(
        entity_id=entity_id,
        entity_type="payment",
        settlement_id=settlement_id,
        amount_paise=amount,
        fee_paise=fee,
        tax_paise=tax,
        net_amount_paise=amount - fee - tax,
        captured_at=datetime(2026, 1, 1),
        settled_at=datetime(2026, 1, 3),
        status="captured",
    )


def _invoice(settlement_id: str = "setl_1", tax: int = 180, rate: float = 18.0) -> GSTInvoiceRecord:
    return GSTInvoiceRecord(
        invoice_id=f"gst_{settlement_id}",
        settlement_id=settlement_id,
        taxable_value_paise=1000,
        cgst_paise=tax // 2,
        sgst_paise=tax - tax // 2,
        igst_paise=0,
        gst_rate_percent=rate,
        invoice_date=datetime(2026, 1, 3).date(),
        gstin="29AAAAA1111A1Z1",
    )


def test_exact_match_is_clean() -> None:
    results = match_tax_lines([_rzp(fee=1000)], [_invoice(tax=180)])
    assert results[0].status == "MATCHED"


def test_missing_invoice_is_flagged_with_full_exposure() -> None:
    results = match_tax_lines([_rzp(fee=1000)], [])
    assert results[0].status == "MISSING_INVOICE"
    assert results[0].expected_tax_paise == 180
    assert results[0].diff_paise == 180


def test_rate_mismatch_detected_even_when_amount_looks_close() -> None:
    # 12% instead of 18% on a small fee still produces a real gap
    results = match_tax_lines([_rzp(fee=1000)], [_invoice(tax=120, rate=12.0)])
    assert results[0].status == "RATE_MISMATCH"


def test_duplicate_invoice_flagged_regardless_of_amount_correctness() -> None:
    inv = _invoice(tax=180)
    results = match_tax_lines([_rzp(fee=1000)], [inv, inv.model_copy(update={"invoice_id": "dup"})])
    assert results[0].status == "DUPLICATE_INVOICE"
    assert results[0].invoiced_tax_paise == 360  # both counted -- that's the whole point


def test_rounding_drift_within_tolerance_is_not_a_finding() -> None:
    results = match_tax_lines([_rzp(fee=1000)], [_invoice(tax=182)])  # 2 paise off
    assert results[0].status == "ROUNDING_DRIFT"


def test_summarize_computes_honest_match_rate() -> None:
    results = match_tax_lines(
        [
            _rzp(entity_id="p1", settlement_id="s1", fee=1000),
            _rzp(entity_id="p2", settlement_id="s2", fee=1000),
        ],
        [_invoice(settlement_id="s1", tax=180)],  # s2 has no invoice
    )
    summary = summarize(results)
    assert summary["total_tax_lines"] == 2
    assert summary["match_rate"] == 0.5


def test_multiple_payments_sharing_a_settlement_batch_match_one_invoice() -> None:
    """Regression test for the bug the first real run found: settlement_id
    groups many payments into one batch (see datagen/generator.py), and one
    invoice is raised per *batch*, not per payment. Matching per-payment
    against a batch-level invoice made nearly every multi-payment batch
    falsely look like a duplicate invoice -- this pins the fix."""
    batch = [
        _rzp(entity_id="p1", fee=1000, settlement_id="s1"),
        _rzp(entity_id="p2", fee=500, settlement_id="s1"),
    ]
    # Combined fee = 1500, expected tax = round(1500 * 0.18) = 270
    results = match_tax_lines(batch, [_invoice(settlement_id="s1", tax=270)])
    assert len(results) == 1  # one result per batch, not one per payment
    assert results[0].status == "MATCHED"
    assert results[0].payment_count == 2


def test_zero_fee_transactions_are_excluded_not_flagged() -> None:
    """A payment with no fee has no GST question to ask -- it shouldn't
    show up as a false MISSING_INVOICE finding."""
    zero_fee = _rzp(fee=0)
    results = match_tax_lines([zero_fee], [])
    assert results == []
