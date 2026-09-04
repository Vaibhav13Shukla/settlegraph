"""Tax-line matcher: reconcile GST charged on Razorpay's MDR fee against the
merchant's own GST invoice register.

A separate reconciliation surface from the rest of this engine on purpose --
matching a settlement amount to a bank credit and matching a tax invoice to
the fee it was charged on are different questions with different failure
modes (a rate applied wrong, an invoice never raised, a duplicate input-
credit claim), and folding them into the same candidate graph would blur
both. This stays a satellite module: it reads `razorpay_settlements.csv` and
`gst_invoices.csv`, and writes its own report. It does not touch
`engine.match`, `engine.assign`, or `engine.verify` and nothing here can
change a settlement match's label.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from settlegraph.models import GSTInvoiceRecord, RazorpaySettlementRecord

TRUE_GST_RATE_PERCENT = 18.0
ROUNDING_TOLERANCE_PAISE = 5


class TaxLineResult:
    def __init__(
        self,
        settlement_id: str,
        payment_count: int,
        status: str,
        expected_tax_paise: int,
        invoiced_tax_paise: int,
        diff_paise: int,
        detail: str,
    ) -> None:
        self.settlement_id = settlement_id
        self.payment_count = payment_count
        self.status = status
        self.expected_tax_paise = expected_tax_paise
        self.invoiced_tax_paise = invoiced_tax_paise
        self.diff_paise = diff_paise
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "settlement_id": self.settlement_id,
            "payment_count": self.payment_count,
            "status": self.status,
            "expected_tax_paise": self.expected_tax_paise,
            "invoiced_tax_paise": self.invoiced_tax_paise,
            "diff_paise": self.diff_paise,
            "detail": self.detail,
        }


def match_tax_lines(
    razorpay: list[RazorpaySettlementRecord],
    invoices: list[GSTInvoiceRecord],
) -> list[TaxLineResult]:
    """One result per *settlement batch* that incurred fees -- matching the
    generator's own invoicing granularity (one consolidated GST invoice per
    settlement cycle, not per transaction; see generate_gst_invoices).

    Statuses:
    - MATCHED: invoiced tax equals expected tax within rounding tolerance.
    - ROUNDING_DRIFT: within a few paise, not exact -- informational, not a
      finding that needs action.
    - RATE_MISMATCH: the invoiced rate isn't 18%, and the gap is bigger than
      rounding could explain.
    - MISSING_INVOICE: no invoice was raised against this batch at all.
    - DUPLICATE_INVOICE: more than one invoice was raised for the same
      batch -- flagged regardless of amount, because the risk here is
      claiming input credit twice, not a wrong number on one invoice.
    """
    by_settlement_rzp: dict[str, list[RazorpaySettlementRecord]] = {}
    for record in razorpay:
        if record.entity_type == "payment" and record.fee_paise > 0:
            by_settlement_rzp.setdefault(record.settlement_id, []).append(record)

    by_settlement_inv: dict[str, list[GSTInvoiceRecord]] = {}
    for inv in invoices:
        by_settlement_inv.setdefault(inv.settlement_id, []).append(inv)

    results: list[TaxLineResult] = []
    for settlement_id in sorted(by_settlement_rzp):
        batch = by_settlement_rzp[settlement_id]
        expected_tax = round(sum(r.fee_paise for r in batch) * TRUE_GST_RATE_PERCENT / 100)
        matching = by_settlement_inv.get(settlement_id, [])
        n = len(batch)

        if not matching:
            results.append(
                TaxLineResult(
                    settlement_id,
                    n,
                    "MISSING_INVOICE",
                    expected_tax,
                    0,
                    expected_tax,
                    "No GST invoice found for this settlement batch's MDR fees.",
                )
            )
            continue

        if len(matching) > 1:
            total_invoiced = sum(i.total_tax_paise for i in matching)
            results.append(
                TaxLineResult(
                    settlement_id,
                    n,
                    "DUPLICATE_INVOICE",
                    expected_tax,
                    total_invoiced,
                    total_invoiced - expected_tax,
                    f"{len(matching)} invoices raised against one settlement batch -- input-credit overclaim risk.",
                )
            )
            continue

        inv = matching[0]
        diff = inv.total_tax_paise - expected_tax
        if (
            abs(inv.gst_rate_percent - TRUE_GST_RATE_PERCENT) > 0.01
            and abs(diff) > ROUNDING_TOLERANCE_PAISE
        ):
            results.append(
                TaxLineResult(
                    settlement_id,
                    n,
                    "RATE_MISMATCH",
                    expected_tax,
                    inv.total_tax_paise,
                    diff,
                    f"Invoice applied {inv.gst_rate_percent:.0f}% instead of {TRUE_GST_RATE_PERCENT:.0f}%.",
                )
            )
        elif abs(diff) <= ROUNDING_TOLERANCE_PAISE and diff != 0:
            results.append(
                TaxLineResult(
                    settlement_id,
                    n,
                    "ROUNDING_DRIFT",
                    expected_tax,
                    inv.total_tax_paise,
                    diff,
                    "Within rounding tolerance, not a finding.",
                )
            )
        elif diff == 0:
            results.append(
                TaxLineResult(settlement_id, n, "MATCHED", expected_tax, inv.total_tax_paise, 0, "")
            )
        else:
            results.append(
                TaxLineResult(
                    settlement_id,
                    n,
                    "AMOUNT_MISMATCH",
                    expected_tax,
                    inv.total_tax_paise,
                    diff,
                    "Correct rate, but the invoiced amount doesn't follow from it.",
                )
            )
    return results


def summarize(results: list[TaxLineResult]) -> dict[str, Any]:
    by_status: dict[str, dict[str, Any]] = {}
    for r in results:
        bucket = by_status.setdefault(r.status, {"count": 0, "exposure_paise": 0})
        bucket["count"] += 1
        if r.status not in ("MATCHED", "ROUNDING_DRIFT"):
            bucket["exposure_paise"] += abs(r.diff_paise)

    total = len(results)
    clean = (
        by_status.get("MATCHED", {"count": 0})["count"]
        + by_status.get("ROUNDING_DRIFT", {"count": 0})["count"]
    )
    match_rate = round(clean / total, 4) if total else 0.0

    return {
        "total_tax_lines": total,
        "match_rate": match_rate,
        "by_status": by_status,
        "findings_exposure_inr": round(
            sum(
                b["exposure_paise"]
                for s, b in by_status.items()
                if s not in ("MATCHED", "ROUNDING_DRIFT")
            )
            / 100,
            2,
        ),
    }


def run_tax_reconciliation(data_dir: Path, output_dir: Path) -> dict[str, Any]:
    from settlegraph.engine.ingest import load_gst_invoices, load_razorpay

    razorpay = load_razorpay(data_dir / "razorpay_settlements.csv")
    invoices = load_gst_invoices(data_dir / "gst_invoices.csv")
    results = match_tax_lines(razorpay, invoices)
    summary = summarize(results)

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "tax_reconciliation.json").open("w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "results": [r.to_dict() for r in results]}, fh, indent=2)

    return summary
