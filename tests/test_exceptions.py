"""Tests for the exception investigation engine."""

from __future__ import annotations

from datetime import date

from settlegraph.engine.exceptions import investigate_exception
from settlegraph.models import NormalizedRecord


def _make_record(
    record_id: str,
    source: str = "razorpay",
    utr: str | None = None,
    amount: int = 10000,
    net: int = 9764,
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        source=source,
        source_record_id=record_id,
        record_type="payment" if source == "razorpay" else "settlement_credit",
        payment_id=None,
        order_id=None,
        settlement_id=None,
        utr=utr,
        invoice_number=None,
        reference_text=None,
        amount_paise=amount,
        fee_paise=None,
        tax_paise=None,
        net_amount_paise=net,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        settlement_date=date(2026, 1, 17),
        description=None,
        raw_record={},
        provenance={"source": source},
    )


def test_investigate_utr_corruption() -> None:
    rzp = _make_record("rzp_1", source="razorpay", utr="RZP000000001")
    bank = _make_record("bank_1", source="bank", utr="RZP00000000X")
    norm_map = {rzp.record_id: rzp, bank.record_id: bank}

    report = investigate_exception(rzp, [(bank, 0.40)], norm_map)

    assert report.category == "UTR_CORRUPTION"
    assert report.severity == "MEDIUM"
    assert "character corruption" in report.root_cause


def test_investigate_amount_mismatch() -> None:
    rzp = _make_record("rzp_1", source="razorpay", utr="RZP001", amount=10000, net=9764)
    bank = _make_record("bank_1", source="bank", utr="RZP001", amount=5000, net=5000)
    norm_map = {rzp.record_id: rzp, bank.record_id: bank}

    report = investigate_exception(rzp, [(bank, 0.70)], norm_map)

    assert report.category in ("REFUND_OR_FEE_DEDUCTION", "AMOUNT_MISMATCH")
    assert report.unexplained_amount_paise == 4764


def test_investigate_utr_corruption_multi_character() -> None:
    """A single trailing character used to be the only shape this caught.

    Two transposed/corrupted characters mid-string is just as plausible a
    bank-side data-entry failure and must still be diagnosed as
    UTR_CORRUPTION, not fall through to a vaguer AMBIGUOUS_MATCH.
    """
    rzp = _make_record("rzp_1", source="razorpay", utr="RZP000000001")
    bank = _make_record("bank_1", source="bank", utr="RZP0000000XY")  # last two chars corrupted
    norm_map = {rzp.record_id: rzp, bank.record_id: bank}

    report = investigate_exception(rzp, [(bank, 0.40)], norm_map)

    assert report.category == "UTR_CORRUPTION"
    assert "edit distance" in report.root_cause


def test_investigate_genuinely_different_utr_is_not_corruption() -> None:
    """Two UTRs that are simply unrelated must not be labeled as corrupted

    of each other -- that would misdirect a reviewer toward "confirm manual
    linkage" for a pair that never should have been linked at all.
    """
    rzp = _make_record("rzp_1", source="razorpay", utr="RZP000000001")
    bank = _make_record("bank_1", source="bank", utr="HDFC999888777")
    norm_map = {rzp.record_id: rzp, bank.record_id: bank}

    report = investigate_exception(rzp, [(bank, 0.30)], norm_map)

    assert report.category != "UTR_CORRUPTION"


def test_investigate_missing_counterpart() -> None:
    rzp = _make_record("rzp_orphan", source="razorpay")
    norm_map = {rzp.record_id: rzp}

    report = investigate_exception(rzp, [], norm_map)

    assert report.category == "MISSING_COUNTERPART"
    assert report.severity == "HIGH"
