"""Ingestion layer: load CSV sources idempotently into typed records.

Row-level fault tolerance. Every ``load_*`` function used to build its
records with an all-or-nothing list comprehension --
``[Model(**_clean_row(row, fields)) for row in rows]`` -- so a single
unparseable timestamp or a single row failing a cross-field invariant
anywhere in a source file raised out of the comprehension and discarded
every other row in that file along with it. ``scripts/chaos_batch.py``
measured the practical cost of that: a sweep of escalating structural
damage completed cleanly at 0% and 10% damage and then hard-crashed from
20% onward, meaning a merchant with exactly one bad row in a 20,000-row
settlement file got *nothing* -- not the 19,999 good records sitting right
next to it.

The fix is quarantine, not silent skipping. For a financial reconciliation
system, silently dropping a row that fails validation would be strictly
worse than the crash it replaces: the batch would report as clean while a
real settlement amount vanished from the reconciliation with no trace.
Every ``load_*`` function is now backed by a shared per-row try/except
(``_load_with_quarantine``) that keeps every row which validates and
diverts every row which doesn't into a ``QuarantinedRow`` -- source file,
1-indexed row number (matching what a human sees if they open the CSV in
Excel), the row's raw un-coerced field values, and the exact Pydantic
error string. Nothing about the money path is weakened to make this work:
quarantine only ever *removes* an untrustworthy row from the batch, it
never coerces a bad value into something the schema would accept.

The public ``load_razorpay``/``load_bank``/``load_merchant``/
``load_gst_invoices``/``load_route_payouts``/``load_all`` signatures are
left exactly as every existing caller (``pipeline.py``, ``cli.py``,
``server.py``, ``tax_matcher.py``, ``route_reconciliation.py``) depends on
them -- a plain list of typed records, nothing more. Each one is now a
thin wrapper that calls its ``*_with_quarantine`` twin and discards the
quarantine list. Callers that need to see (and report) what got
quarantined -- currently only ``pipeline.py``'s Phase 1 -- call the
``*_with_quarantine`` variant directly instead.

A wholly missing source file is a different failure from a malformed row:
there is nothing at all to reconcile, not "reconcile everything except one
row we couldn't parse". That still raises ``FileNotFoundError`` straight
out of `Path.open`, unmodified from before this change -- fail-closed is
the correct behavior there, and quarantine must never be read as license
to swallow it.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from settlegraph.models import (
    BankStatementRecord,
    GSTInvoiceRecord,
    MerchantLedgerRecord,
    RazorpaySettlementRecord,
    RoutePayoutRecord,
)


class QuarantinedRow:
    """One CSV row that failed Pydantic validation and was pulled out of the
    batch rather than either aborting the whole file's ingest or -- worse,
    for a system whose job is to account for money -- being dropped without
    a trace.

    Carries everything needed to go find the row and understand why it
    didn't make it: which file, which line (1-indexed the way a human sees
    it in Excel -- the header occupies row 1, so the first data row is row
    2), the row's raw field values exactly as ``csv.DictReader`` produced
    them (evidence, not the cleaned/coerced version ``_clean_row`` would
    have built), and the exact validation error string. ``source`` names
    which loader produced it (``"razorpay"``, ``"bank"``, ...) so a merged
    quarantine list (see ``load_all_with_quarantine``) still tells the
    reader which schema rejected the row.
    """

    def __init__(
        self,
        source_file: str,
        source: str,
        row_number: int,
        raw_row: dict[str, Any],
        error: str,
    ) -> None:
        self.source_file = source_file
        self.source = source
        self.row_number = row_number
        self.raw_row = raw_row
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source": self.source,
            "row_number": self.row_number,
            "raw_row": self.raw_row,
            "error": self.error,
        }


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


def _load_with_quarantine(
    path: Path, model: type[BaseModel], source: str
) -> tuple[list[Any], list[QuarantinedRow]]:
    """Shared quarantine machinery behind every ``load_*`` function.

    Deliberately a per-row try/except rather than the previous
    ``[model(**_clean_row(row, fields)) for row in rows]`` comprehension:
    that single line meant one bad row anywhere aborted the entire file.
    ``rows`` is read in full first (``_read_csv``/``Path.open`` still raise
    ``FileNotFoundError`` unmodified if ``path`` doesn't exist -- that
    happens before any row-level try/except and is intentionally left able
    to propagate), then each row is validated independently: a row that
    raises ``ValidationError`` -- whether from a plain type/coercion
    failure or a cross-field ``model_validator`` like
    ``RazorpaySettlementRecord.net_must_balance`` -- is captured as a
    ``QuarantinedRow`` and excluded from the returned record list. Every
    other row is entirely unaffected by its neighbor's failure.

    Only ``ValidationError`` is caught. Anything else escaping ``model(...)``
    is not a "this row is untrustworthy" situation this function is
    designed to handle -- it is left to propagate and fail the batch
    closed, the same as a missing file does.
    """
    rows = _read_csv(path)
    fields = set(model.model_fields)
    records: list[Any] = []
    quarantined: list[QuarantinedRow] = []
    for row_number, row in enumerate(rows, start=2):
        try:
            records.append(model(**_clean_row(row, fields)))
        except ValidationError as exc:
            quarantined.append(
                QuarantinedRow(
                    source_file=str(path),
                    source=source,
                    row_number=row_number,
                    raw_row=dict(row),
                    error=str(exc),
                )
            )
    return records, quarantined


def load_razorpay_with_quarantine(
    path: Path,
) -> tuple[list[RazorpaySettlementRecord], list[QuarantinedRow]]:
    return _load_with_quarantine(path, RazorpaySettlementRecord, "razorpay")


def load_razorpay(path: Path) -> list[RazorpaySettlementRecord]:
    records, _ = load_razorpay_with_quarantine(path)
    return records


def load_bank_with_quarantine(
    path: Path,
) -> tuple[list[BankStatementRecord], list[QuarantinedRow]]:
    return _load_with_quarantine(path, BankStatementRecord, "bank")


def load_bank(path: Path) -> list[BankStatementRecord]:
    records, _ = load_bank_with_quarantine(path)
    return records


def load_merchant_with_quarantine(
    path: Path,
) -> tuple[list[MerchantLedgerRecord], list[QuarantinedRow]]:
    return _load_with_quarantine(path, MerchantLedgerRecord, "merchant")


def load_merchant(path: Path) -> list[MerchantLedgerRecord]:
    records, _ = load_merchant_with_quarantine(path)
    return records


def load_gst_invoices_with_quarantine(
    path: Path,
) -> tuple[list[GSTInvoiceRecord], list[QuarantinedRow]]:
    if not path.exists():
        return [], []
    return _load_with_quarantine(path, GSTInvoiceRecord, "gst_invoice")


def load_gst_invoices(path: Path) -> list[GSTInvoiceRecord]:
    records, _ = load_gst_invoices_with_quarantine(path)
    return records


def load_route_payouts_with_quarantine(
    path: Path,
) -> tuple[list[RoutePayoutRecord], list[QuarantinedRow]]:
    if not path.exists():
        return [], []
    return _load_with_quarantine(path, RoutePayoutRecord, "route_payout")


def load_route_payouts(path: Path) -> list[RoutePayoutRecord]:
    records, _ = load_route_payouts_with_quarantine(path)
    return records


def load_all_with_quarantine(
    data_dir: Path,
) -> tuple[
    list[RazorpaySettlementRecord],
    list[BankStatementRecord],
    list[MerchantLedgerRecord],
    list[QuarantinedRow],
]:
    """Load the three core sources and return one merged quarantine list
    alongside them, so a single caller (``pipeline.py``'s Phase 1) can
    report one combined count and write one ``quarantine.json`` covering
    every source instead of three separate files."""
    rzp, rzp_q = load_razorpay_with_quarantine(data_dir / "razorpay_settlements.csv")
    bank, bank_q = load_bank_with_quarantine(data_dir / "bank_statements.csv")
    merch, merch_q = load_merchant_with_quarantine(data_dir / "merchant_ledger.csv")
    return rzp, bank, merch, rzp_q + bank_q + merch_q


def load_all(
    data_dir: Path,
) -> tuple[list[RazorpaySettlementRecord], list[BankStatementRecord], list[MerchantLedgerRecord]]:
    rzp, bank, merch, _ = load_all_with_quarantine(data_dir)
    return rzp, bank, merch
