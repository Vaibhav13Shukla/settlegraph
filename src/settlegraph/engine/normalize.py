"""Normalization layer: unify Razorpay, bank, and merchant into NormalizedRecord."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from settlegraph.models import (
    BankStatementRecord,
    MerchantLedgerRecord,
    NormalizedRecord,
    RazorpaySettlementRecord,
)


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {value}")


def normalize_razorpay(record: RazorpaySettlementRecord) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=f"rzp_norm_{record.entity_id}",
        source="razorpay",
        source_record_id=record.entity_id,
        record_type="payment",
        payment_id=record.entity_id,
        order_id=record.order_id,
        settlement_id=record.settlement_id,
        utr=record.settlement_utr,
        invoice_number=None,
        reference_text=record.description,
        amount_paise=record.amount_paise,
        fee_paise=record.fee_paise,
        tax_paise=record.tax_paise,
        net_amount_paise=record.net_amount_paise,
        currency=record.currency,
        transaction_date=record.captured_at.date(),
        settlement_date=record.settled_at.date(),
        description=record.description,
        raw_record=record.model_dump(mode="json"),
        provenance={"source": "razorpay", "original_id": record.entity_id},
    )


def normalize_bank(record: BankStatementRecord) -> NormalizedRecord:
    amount = (
        record.credit_amount_paise
        if record.credit_amount_paise is not None
        else (record.debit_amount_paise or 0)
    )
    direction = "credit" if record.credit_amount_paise is not None else "debit"
    return NormalizedRecord(
        record_id=f"bank_norm_{record.record_id}",
        source="bank",
        source_record_id=record.record_id,
        record_type="settlement_credit" if direction == "credit" else "adjustment",
        payment_id=None,
        order_id=None,
        settlement_id=None,
        utr=record.reference_number,
        invoice_number=None,
        reference_text=record.description,
        amount_paise=abs(amount),
        fee_paise=None,
        tax_paise=None,
        net_amount_paise=abs(amount),
        currency="INR",
        transaction_date=record.transaction_date,
        settlement_date=record.value_date or record.transaction_date,
        description=record.description,
        raw_record=record.model_dump(mode="json"),
        provenance={"source": "bank", "original_id": record.record_id, "direction": direction},
    )


def normalize_merchant(record: MerchantLedgerRecord) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=f"merch_norm_{record.ledger_id}",
        source="merchant",
        source_record_id=record.ledger_id,
        record_type="sale" if record.transaction_type in ("sale", "credit_note") else "adjustment",
        payment_id=record.payment_gateway_id,
        order_id=record.order_id,
        settlement_id=None,
        utr=None,
        invoice_number=record.invoice_number,
        reference_text=record.notes,
        amount_paise=record.amount_paise,
        fee_paise=None,
        tax_paise=None,
        net_amount_paise=record.amount_paise,
        currency=record.currency,
        transaction_date=record.created_at.date(),
        settlement_date=None,
        description=record.notes,
        raw_record=record.model_dump(mode="json"),
        provenance={"source": "merchant", "original_id": record.ledger_id},
    )


def normalize_all(
    razorpay: list[RazorpaySettlementRecord],
    bank: list[BankStatementRecord],
    merchant: list[MerchantLedgerRecord],
) -> tuple[list[NormalizedRecord], list[NormalizedRecord], list[NormalizedRecord]]:
    rzp_norm = [normalize_razorpay(r) for r in razorpay]
    bank_norm = [normalize_bank(r) for r in bank]
    merch_norm = [normalize_merchant(r) for r in merchant]
    return rzp_norm, bank_norm, merch_norm
