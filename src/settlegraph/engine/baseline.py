"""Baseline matching engine: naive deterministic rules for comparison benchmarking."""

from __future__ import annotations

from typing import Any

from settlegraph.models import NormalizedRecord


def run_naive_baseline(
    rzp_records: list[NormalizedRecord],
    bank_records: list[NormalizedRecord],
    merchant_records: list[NormalizedRecord],
) -> list[dict[str, Any]]:
    """A naive rule-based matcher that only performs strict, exact equality matches.

    Naive Rules:
    1. Razorpay <-> Bank: Match IF AND ONLY IF both UTRs match exactly AND amounts match exactly.
    2. Razorpay <-> Merchant: Match IF AND ONLY IF order_id matches exactly AND amounts match exactly.

    This baseline lacks:
    - Fuzzy / probabilistic linkage for corrupted references
    - Tolerance for timing differences
    - Handling of MDR fee and refund deductions
    - Global bipartite optimization under conflict
    """
    assignments: list[dict[str, Any]] = []

    bank_by_utr_amt: dict[tuple[str, int], NormalizedRecord] = {}
    for b in bank_records:
        if b.utr:
            bank_by_utr_amt[(b.utr, b.amount_paise)] = b

    used_bank: set[str] = set()
    for r in rzp_records:
        rzp_net = r.net_amount_paise if r.net_amount_paise is not None else r.amount_paise
        key = (r.utr or "", rzp_net)
        if key in bank_by_utr_amt and bank_by_utr_amt[key].record_id not in used_bank:
            b = bank_by_utr_amt[key]
            used_bank.add(b.record_id)
            assignments.append(
                {
                    "source_a": "razorpay",
                    "source_a_id": r.record_id,
                    "source_b": "bank",
                    "source_b_id": b.record_id,
                    "confidence": 1.0,
                    "label": "AUTO_MATCH",
                    "a_amount_paise": r.amount_paise,
                    "b_amount_paise": b.amount_paise,
                    "a_utr": r.utr,
                    "b_utr": b.utr,
                    "a_order_id": r.order_id,
                    "b_order_id": b.order_id,
                }
            )

    merch_by_order_amt: dict[tuple[str, int], NormalizedRecord] = {}
    for m in merchant_records:
        if m.order_id:
            merch_by_order_amt[(m.order_id, m.amount_paise)] = m

    used_merch: set[str] = set()
    for r in rzp_records:
        if r.order_id:
            key = (r.order_id, r.amount_paise)
            if key in merch_by_order_amt and merch_by_order_amt[key].record_id not in used_merch:
                m = merch_by_order_amt[key]
                used_merch.add(m.record_id)
                assignments.append(
                    {
                        "source_a": "razorpay",
                        "source_a_id": r.record_id,
                        "source_b": "merchant",
                        "source_b_id": m.record_id,
                        "confidence": 1.0,
                        "label": "AUTO_MATCH",
                        "a_amount_paise": r.amount_paise,
                        "b_amount_paise": m.amount_paise,
                        "a_utr": r.utr,
                        "b_utr": m.utr,
                        "a_order_id": r.order_id,
                        "b_order_id": m.order_id,
                    }
                )

    return assignments
