"""Tests for the ingestion layer."""

from __future__ import annotations

from pathlib import Path

from settlegraph.engine.ingest import load_all


def test_load_all_returns_typed_records(tmp_path: Path) -> None:
    """load_all produces typed Pydantic records from CSV files."""
    # Write minimal valid CSVs
    rzp_csv = tmp_path / "razorpay_settlements.csv"
    rzp_csv.write_text(
        "entity_id,entity_type,settlement_id,settlement_utr,order_id,amount_paise,currency,fee_paise,tax_paise,net_amount_paise,payment_method,captured_at,settled_at,description,notes,status\n"
        "pay_1,payment,setl_1,RZP001,order_1,10000,INR,200,36,9764,,2026-01-15T10:00:00,2026-01-17T10:00:00,Order order_1,'{}',captured\n"
    )

    bank_csv = tmp_path / "bank_statements.csv"
    bank_csv.write_text(
        "record_id,transaction_date,value_date,description,reference_number,debit_amount_paise,credit_amount_paise,balance_paise,bank_name,account_number\n"
        "bank_1,2026-01-17,2026-01-17,NEFT/RAZORPAY/RZP001/setl_1,RZP001,,,9764,ICICI,XXXX001234\n"
    )

    merch_csv = tmp_path / "merchant_ledger.csv"
    merch_csv.write_text(
        "ledger_id,order_id,invoice_number,customer_id,amount_paise,currency,transaction_type,created_at,payment_status,payment_gateway_id,notes\n"
        "led_1,order_1,INV-2026-000001,cust_1,10000,INR,sale,2026-01-15T10:00:00,paid,pay_1,Razorpay pay_1\n"
    )

    rzp, bank, merchant = load_all(tmp_path)

    assert len(rzp) == 1
    assert len(bank) == 1
    assert len(merchant) == 1
    assert rzp[0].entity_id == "pay_1"
    assert rzp[0].net_amount_paise == 9764
    assert bank[0].credit_amount_paise == 9764
    assert merchant[0].order_id == "order_1"


def test_load_razorpay_validates_net_balance(tmp_path: Path) -> None:
    """Razorpay records with incorrect net_amount are rejected."""
    rzp_csv = tmp_path / "razorpay_settlements.csv"
    rzp_csv.write_text(
        "entity_id,entity_type,settlement_id,settlement_utr,order_id,amount_paise,currency,fee_paise,tax_paise,net_amount_paise,payment_method,captured_at,settled_at,description,notes,status\n"
        "pay_bad,payment,setl_1,RZP001,order_1,10000,INR,200,36,9999,,2026-01-15T10:00:00,2026-01-17T10:00:00,Order order_1,'{}',captured\n"
    )

    bank_csv = tmp_path / "bank_statements.csv"
    bank_csv.write_text(
        "record_id,transaction_date,value_date,description,reference_number,debit_amount_paise,credit_amount_paise,balance_paise,bank_name,account_number\n"
        "bank_1,2026-01-17,2026-01-17,NEFT/RAZORPAY/RZP001/setl_1,RZP001,,,9764,ICICI,XXXX001234\n"
    )

    merch_csv = tmp_path / "merchant_ledger.csv"
    merch_csv.write_text(
        "ledger_id,order_id,invoice_number,customer_id,amount_paise,currency,transaction_type,created_at,payment_status,payment_gateway_id,notes\n"
        "led_1,order_1,INV-2026-000001,cust_1,10000,INR,sale,2026-01-15T10:00:00,paid,pay_1,Razorpay pay_1\n"
    )

    import pytest

    with pytest.raises(Exception):  # Pydantic ValidationError
        load_all(tmp_path)
