"""Tests for the normalization layer."""

from __future__ import annotations

from datetime import UTC, date, datetime

from settlegraph.engine.normalize import (
    normalize_all,
    normalize_bank,
    normalize_merchant,
    normalize_razorpay,
)
from settlegraph.models import (
    BankStatementRecord,
    MerchantLedgerRecord,
    NormalizedRecord,
    RazorpaySettlementRecord,
)


def test_normalize_razorpay_preserves_key_fields() -> None:
    rzp = RazorpaySettlementRecord(
        entity_id="pay_123",
        entity_type="payment",
        settlement_id="setl_456",
        settlement_utr="RZP000000123",
        order_id="order_789",
        amount_paise=10000,
        fee_paise=200,
        tax_paise=36,
        net_amount_paise=9764,
        payment_method="upi",
        captured_at=datetime.now(UTC),
        settled_at=datetime.now(UTC),
        status="captured",
    )
    norm = normalize_razorpay(rzp)

    assert isinstance(norm, NormalizedRecord)
    assert norm.source == "razorpay"
    assert norm.source_record_id == "pay_123"
    assert norm.utr == "RZP000000123"
    assert norm.order_id == "order_789"
    assert norm.amount_paise == 10000
    assert norm.net_amount_paise == 9764
    assert norm.provenance["source"] == "razorpay"


def test_normalize_bank_direction_detected() -> None:
    bank = BankStatementRecord(
        record_id="bank_001",
        transaction_date=date(2026, 1, 17),
        value_date=date(2026, 1, 17),
        description="NEFT/RAZORPAY/RZP001/setl_1",
        reference_number="RZP001",
        credit_amount_paise=9764,
        bank_name="ICICI",
        account_number="XXXX001234",
    )
    norm = normalize_bank(bank)

    assert norm.source == "bank"
    assert norm.record_type == "settlement_credit"
    assert norm.amount_paise == 9764
    assert norm.provenance["direction"] == "credit"


def test_normalize_merchant_preserves_order_id() -> None:
    merch = MerchantLedgerRecord(
        ledger_id="led_001",
        order_id="order_789",
        invoice_number="INV-2026-000001",
        customer_id="cust_1",
        amount_paise=10000,
        transaction_type="sale",
        created_at=datetime.now(UTC),
        payment_status="paid",
        payment_gateway_id="pay_123",
        notes="Razorpay pay_123",
    )
    norm = normalize_merchant(merch)

    assert norm.source == "merchant"
    assert norm.order_id == "order_789"
    assert norm.payment_id == "pay_123"
    assert norm.invoice_number == "INV-2026-000001"


def test_normalize_all_returns_three_lists() -> None:
    rzp = [
        RazorpaySettlementRecord(
            entity_id="pay_1",
            entity_type="payment",
            settlement_id="setl_1",
            settlement_utr="RZP001",
            order_id="order_1",
            amount_paise=10000,
            fee_paise=200,
            tax_paise=36,
            net_amount_paise=9764,
            captured_at=datetime.now(UTC),
            settled_at=datetime.now(UTC),
            status="captured",
        )
    ]
    bank = [
        BankStatementRecord(
            record_id="bank_1",
            transaction_date=date(2026, 1, 17),
            value_date=date(2026, 1, 17),
            description="NEFT/RAZORPAY/RZP001/setl_1",
            reference_number="RZP001",
            credit_amount_paise=9764,
            bank_name="ICICI",
            account_number="XXXX001234",
        )
    ]
    merch = [
        MerchantLedgerRecord(
            ledger_id="led_1",
            order_id="order_1",
            invoice_number="INV-2026-000001",
            customer_id="cust_1",
            amount_paise=10000,
            transaction_type="sale",
            created_at=datetime.now(UTC),
            payment_status="paid",
            payment_gateway_id="pay_1",
            notes="Razorpay pay_1",
        )
    ]

    rzp_n, bank_n, merch_n = normalize_all(rzp, bank, merch)

    assert len(rzp_n) == 1
    assert len(bank_n) == 1
    assert len(merch_n) == 1
    assert all(isinstance(r, NormalizedRecord) for r in rzp_n + bank_n + merch_n)
