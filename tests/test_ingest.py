"""Tests for the ingestion layer.

Row-level fault tolerance: ``engine/ingest.py`` used to build records with an
all-or-nothing list comprehension, so a single unparseable row anywhere in a
CSV threw the entire file's ingest away. ``scripts/chaos_batch.py`` measured
this directly -- a sweep of escalating structural damage completed cleanly at
0% and 10% damage but hard-crashed from 20% onward. The tests below pin the
replacement behavior: a row that fails validation is quarantined (captured
with its raw content, source file, 1-indexed row number, and exact error
string) while every valid row in the same file still comes through. A wholly
missing source file is a different failure -- there is nothing to reconcile
at all -- and must still raise ``FileNotFoundError`` rather than being
treated as "zero rows, zero quarantine".
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from settlegraph.engine.ingest import (
    load_all,
    load_all_with_quarantine,
    load_bank_with_quarantine,
    load_razorpay,
    load_razorpay_with_quarantine,
)

RZP_HEADER = [
    "entity_id",
    "entity_type",
    "settlement_id",
    "settlement_utr",
    "order_id",
    "amount_paise",
    "currency",
    "fee_paise",
    "tax_paise",
    "net_amount_paise",
    "payment_method",
    "captured_at",
    "settled_at",
    "description",
    "notes",
    "status",
]

BANK_HEADER = [
    "record_id",
    "transaction_date",
    "value_date",
    "description",
    "reference_number",
    "debit_amount_paise",
    "credit_amount_paise",
    "balance_paise",
    "bank_name",
    "account_number",
]

MERCHANT_HEADER = [
    "ledger_id",
    "order_id",
    "invoice_number",
    "customer_id",
    "amount_paise",
    "currency",
    "transaction_type",
    "created_at",
    "payment_status",
    "payment_gateway_id",
    "notes",
]


def _write_csv(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def _valid_rzp_row(entity_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "entity_id": entity_id,
        "entity_type": "payment",
        "settlement_id": f"setl_{entity_id}",
        "settlement_utr": "RZP001",
        "order_id": f"order_{entity_id}",
        "amount_paise": "10000",
        "currency": "INR",
        "fee_paise": "200",
        "tax_paise": "36",
        "net_amount_paise": "9764",
        "payment_method": "",
        "captured_at": "2026-01-15T10:00:00",
        "settled_at": "2026-01-17T10:00:00",
        "description": f"Order order_{entity_id}",
        "notes": "{}",
        "status": "captured",
    }
    row.update(overrides)
    return row


def _valid_bank_row(record_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "record_id": record_id,
        "transaction_date": "2026-01-17",
        "value_date": "2026-01-17",
        "description": "NEFT/RAZORPAY/RZP001/setl_1",
        "reference_number": "RZP001",
        "debit_amount_paise": "",
        "credit_amount_paise": "9764",
        "balance_paise": "",
        "bank_name": "ICICI",
        "account_number": "XXXX001234",
    }
    row.update(overrides)
    return row


def _valid_merchant_row(ledger_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "ledger_id": ledger_id,
        "order_id": "order_1",
        "invoice_number": "INV-2026-000001",
        "customer_id": "cust_1",
        "amount_paise": "10000",
        "currency": "INR",
        "transaction_type": "sale",
        "created_at": "2026-01-15T10:00:00",
        "payment_status": "paid",
        "payment_gateway_id": "pay_1",
        "notes": "Razorpay pay_1",
    }
    row.update(overrides)
    return row


def test_load_all_returns_typed_records(tmp_path: Path) -> None:
    """load_all produces typed Pydantic records from CSV files."""
    _write_csv(tmp_path / "razorpay_settlements.csv", RZP_HEADER, [_valid_rzp_row("pay_1")])
    _write_csv(tmp_path / "bank_statements.csv", BANK_HEADER, [_valid_bank_row("bank_1")])
    _write_csv(tmp_path / "merchant_ledger.csv", MERCHANT_HEADER, [_valid_merchant_row("led_1")])

    rzp, bank, merchant = load_all(tmp_path)

    assert len(rzp) == 1
    assert len(bank) == 1
    assert len(merchant) == 1
    assert rzp[0].entity_id == "pay_1"
    assert rzp[0].net_amount_paise == 9764
    assert bank[0].credit_amount_paise == 9764
    assert merchant[0].order_id == "order_1"


def test_clean_file_quarantines_nothing(tmp_path: Path) -> None:
    """A well-formed file with no bad rows must not produce a false quarantine."""
    rows = [_valid_rzp_row(f"pay_{i}") for i in range(5)]
    _write_csv(tmp_path / "razorpay_settlements.csv", RZP_HEADER, rows)

    records, quarantined = load_razorpay_with_quarantine(tmp_path / "razorpay_settlements.csv")

    assert len(records) == 5
    assert quarantined == []


def test_one_malformed_row_is_quarantined_valid_rows_survive(tmp_path: Path) -> None:
    """A single unparseable timestamp must no longer abort the whole batch.

    Rows 0, 1, 3 (0-indexed) are valid; row 2 has a garbage `captured_at`
    that Pydantic cannot coerce into a datetime. Excel-style row numbering
    starts data at row 2 (row 1 is the header), so the bad row is line 4.
    """
    rows = [
        _valid_rzp_row("pay_0"),
        _valid_rzp_row("pay_1"),
        _valid_rzp_row("pay_bad", captured_at="not-a-date"),
        _valid_rzp_row("pay_3"),
    ]
    path = tmp_path / "razorpay_settlements.csv"
    _write_csv(path, RZP_HEADER, rows)

    records, quarantined = load_razorpay_with_quarantine(path)

    assert [r.entity_id for r in records] == ["pay_0", "pay_1", "pay_3"]
    assert len(quarantined) == 1

    bad = quarantined[0]
    assert bad.row_number == 4  # header=1, pay_0=2, pay_1=3, pay_bad=4
    assert bad.source_file.endswith("razorpay_settlements.csv")
    assert bad.source == "razorpay"
    assert bad.raw_row["entity_id"] == "pay_bad"
    assert bad.error  # non-empty exact validation error string
    assert "captured_at" in bad.error


def test_net_must_balance_cross_field_failure_is_quarantined(tmp_path: Path) -> None:
    """A row can fail on a cross-field invariant, not just a type coercion --
    RazorpaySettlementRecord.net_must_balance rejects a row where
    net_amount_paise != amount - fee - tax. That must quarantine the same
    way a type error does, not crash the batch."""
    rows = [
        _valid_rzp_row("pay_ok"),
        _valid_rzp_row("pay_bad_balance", net_amount_paise="9999"),  # should be 9764
    ]
    path = tmp_path / "razorpay_settlements.csv"
    _write_csv(path, RZP_HEADER, rows)

    records, quarantined = load_razorpay_with_quarantine(path)

    assert [r.entity_id for r in records] == ["pay_ok"]
    assert len(quarantined) == 1
    bad = quarantined[0]
    assert bad.row_number == 3
    assert bad.raw_row["entity_id"] == "pay_bad_balance"
    assert bad.error
    assert "net_amount_paise" in bad.error


def test_missing_file_still_raises_file_not_found(tmp_path: Path) -> None:
    """A wholly missing source file is a different failure than a malformed
    row -- there is nothing to reconcile at all -- and must still fail
    closed rather than being swallowed into an empty quarantine result."""
    with pytest.raises(FileNotFoundError):
        load_razorpay_with_quarantine(tmp_path / "does_not_exist.csv")

    with pytest.raises(FileNotFoundError):
        load_razorpay(tmp_path / "does_not_exist.csv")

    with pytest.raises(FileNotFoundError):
        load_all_with_quarantine(tmp_path)


def test_every_row_malformed_quarantines_all_without_crashing(tmp_path: Path) -> None:
    """The worst case: every row in the file is bad. The batch must come
    back empty-but-explained, not crash and not silently look like a clean
    empty file -- the caller (and quarantine.json) must be able to tell this
    batch was empty *for a reason*."""
    rows = [_valid_rzp_row(f"pay_{i}", captured_at="not-a-date") for i in range(4)]
    path = tmp_path / "razorpay_settlements.csv"
    _write_csv(path, RZP_HEADER, rows)

    records, quarantined = load_razorpay_with_quarantine(path)

    assert records == []
    assert len(quarantined) == 4
    assert all(q.error for q in quarantined)
    assert [q.row_number for q in quarantined] == [2, 3, 4, 5]


def test_load_bank_with_quarantine_generalizes_to_other_loaders(tmp_path: Path) -> None:
    """The shared quarantine machinery isn't razorpay-specific -- a bad bank
    row (violating BankStatementRecord's exactly-one-of debit/credit
    invariant) quarantines the same way."""
    rows = [
        _valid_bank_row("bank_ok"),
        _valid_bank_row("bank_bad", debit_amount_paise="500", credit_amount_paise="500"),
    ]
    path = tmp_path / "bank_statements.csv"
    _write_csv(path, BANK_HEADER, rows)

    records, quarantined = load_bank_with_quarantine(path)

    assert [r.record_id for r in records] == ["bank_ok"]
    assert len(quarantined) == 1
    assert quarantined[0].source == "bank"
    assert quarantined[0].row_number == 3


def test_load_all_with_quarantine_aggregates_across_sources(tmp_path: Path) -> None:
    """load_all_with_quarantine merges quarantine lists from every source so
    the pipeline can report one combined count/file."""
    _write_csv(
        tmp_path / "razorpay_settlements.csv",
        RZP_HEADER,
        [_valid_rzp_row("pay_ok"), _valid_rzp_row("pay_bad", captured_at="garbage")],
    )
    _write_csv(tmp_path / "bank_statements.csv", BANK_HEADER, [_valid_bank_row("bank_1")])
    _write_csv(tmp_path / "merchant_ledger.csv", MERCHANT_HEADER, [_valid_merchant_row("led_1")])

    rzp, bank, merchant, quarantined = load_all_with_quarantine(tmp_path)

    assert len(rzp) == 1
    assert len(bank) == 1
    assert len(merchant) == 1
    assert len(quarantined) == 1
    assert quarantined[0].source == "razorpay"

    # to_dict() must be JSON-serializable with the documented keys, since
    # the pipeline writes these straight to results/quarantine.json.
    d = quarantined[0].to_dict()
    assert set(d.keys()) == {"source_file", "source", "row_number", "raw_row", "error"}


def test_load_all_still_returns_three_tuple_for_existing_callers(tmp_path: Path) -> None:
    """load_all's signature must stay exactly what cli.py/server.py/tests
    already depend on, even when a source has quarantined rows."""
    _write_csv(
        tmp_path / "razorpay_settlements.csv",
        RZP_HEADER,
        [_valid_rzp_row("pay_ok"), _valid_rzp_row("pay_bad", captured_at="garbage")],
    )
    _write_csv(tmp_path / "bank_statements.csv", BANK_HEADER, [_valid_bank_row("bank_1")])
    _write_csv(tmp_path / "merchant_ledger.csv", MERCHANT_HEADER, [_valid_merchant_row("led_1")])

    result = load_all(tmp_path)

    assert len(result) == 3
    rzp, bank, merchant = result
    assert len(rzp) == 1
    assert len(bank) == 1
    assert len(merchant) == 1
