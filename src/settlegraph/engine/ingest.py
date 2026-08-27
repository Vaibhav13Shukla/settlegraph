"""Ingestion layer: load CSV sources idempotently into typed records."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from settlegraph.models import BankStatementRecord, MerchantLedgerRecord, RazorpaySettlementRecord


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _clean_row(row: dict[str, Any], model_fields: set[str]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in row.items():
        if key not in model_fields:
            continue
        if value == "":
            cleaned[key] = None
            continue
        if key == "notes" and isinstance(value, str):
            v = value.strip()
            if (v.startswith("{") and v.endswith("}")) or (v.startswith("'") and v.endswith("'")):
                v2 = v[1:-1] if v.startswith("'") and v.endswith("'") else v
                try:
                    cleaned[key] = json.loads(v2)
                    continue
                except json.JSONDecodeError:
                    cleaned[key] = {}
                    continue
        cleaned[key] = value

    # Some bank exports place the transaction amount in balance column while
    # leaving debit/credit empty. For ingestion safety we treat that as credit.
    if {"debit_amount_paise", "credit_amount_paise", "balance_paise"}.issubset(model_fields):
        if (
            cleaned.get("debit_amount_paise") is None
            and cleaned.get("credit_amount_paise") is None
            and cleaned.get("balance_paise") is not None
        ):
            cleaned["credit_amount_paise"] = cleaned["balance_paise"]
    return cleaned


def load_razorpay(path: Path) -> list[RazorpaySettlementRecord]:
    rows = _read_csv(path)
    fields = set(RazorpaySettlementRecord.model_fields)
    return [RazorpaySettlementRecord(**_clean_row(row, fields)) for row in rows]


def load_bank(path: Path) -> list[BankStatementRecord]:
    rows = _read_csv(path)
    fields = set(BankStatementRecord.model_fields)
    return [BankStatementRecord(**_clean_row(row, fields)) for row in rows]


def load_merchant(path: Path) -> list[MerchantLedgerRecord]:
    rows = _read_csv(path)
    fields = set(MerchantLedgerRecord.model_fields)
    return [MerchantLedgerRecord(**_clean_row(row, fields)) for row in rows]


def load_all(
    data_dir: Path,
) -> tuple[list[RazorpaySettlementRecord], list[BankStatementRecord], list[MerchantLedgerRecord]]:
    rzp = load_razorpay(data_dir / "razorpay_settlements.csv")
    bank = load_bank(data_dir / "bank_statements.csv")
    merch = load_merchant(data_dir / "merchant_ledger.csv")
    return rzp, bank, merch
