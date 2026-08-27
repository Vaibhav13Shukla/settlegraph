"""Global assignment: constraint-based optimization of matched edges."""

from __future__ import annotations

from settlegraph.config import PipelineConfig
from settlegraph.models import NormalizedRecord


def _classify_assignment(confidence: float, config: PipelineConfig) -> str:
    if confidence >= config.auto_match_threshold:
        return "AUTO_MATCH"
    if confidence >= config.exception_threshold:
        return "LIKELY_MATCH"
    return "EXCEPTION"


def global_assign(
    scored_edges: list[tuple[NormalizedRecord, NormalizedRecord, float]],
    config: PipelineConfig,
) -> list[dict]:
    """Assign matches under hard constraints:

    1. Each leg pair (e.g. Razorpay <-> Bank, Razorpay <-> Merchant) consumes each record at most once.
    2. Greedy selection: highest-confidence edges first.
    3. Classification is threshold-driven.
    """
    # Track which records are assigned per leg
    rzp_bank_assigned: set[str] = set()
    bank_assigned: set[str] = set()
    rzp_merch_assigned: set[str] = set()
    merchant_assigned: set[str] = set()
    bank_merch_assigned: set[str] = set()
    merch_bank_assigned: set[str] = set()

    # Sort edges by confidence descending
    sorted_edges = sorted(scored_edges, key=lambda e: e[2], reverse=True)

    assignments: list[dict] = []

    for a, b, confidence in sorted_edges:
        a_id = a.record_id
        b_id = b.record_id
        source_a = a.source
        source_b = b.source

        # Skip if same source
        if source_a == source_b:
            continue

        sources = {source_a, source_b}

        # Check leg-specific exclusivity
        if sources == {"razorpay", "bank"}:
            rzp_id = a_id if source_a == "razorpay" else b_id
            bank_id = a_id if source_a == "bank" else b_id
            if rzp_id in rzp_bank_assigned or bank_id in bank_assigned:
                continue
            rzp_bank_assigned.add(rzp_id)
            bank_assigned.add(bank_id)

        elif sources == {"razorpay", "merchant"}:
            rzp_id = a_id if source_a == "razorpay" else b_id
            merch_id = a_id if source_a == "merchant" else b_id
            if rzp_id in rzp_merch_assigned or merch_id in merchant_assigned:
                continue
            rzp_merch_assigned.add(rzp_id)
            merchant_assigned.add(merch_id)

        elif sources == {"bank", "merchant"}:
            bank_id = a_id if source_a == "bank" else b_id
            merch_id = a_id if source_a == "merchant" else b_id
            if bank_id in bank_merch_assigned or merch_id in merch_bank_assigned:
                continue
            bank_merch_assigned.add(bank_id)
            merch_bank_assigned.add(merch_id)

        label = _classify_assignment(confidence, config)

        assignment = {
            "source_a": source_a,
            "source_a_id": a_id,
            "source_b": source_b,
            "source_b_id": b_id,
            "confidence": round(confidence, 4),
            "label": label,
            "a_amount_paise": a.amount_paise,
            "b_amount_paise": b.amount_paise,
            "a_utr": a.utr,
            "b_utr": b.utr,
            "a_order_id": a.order_id,
            "b_order_id": b.order_id,
        }

        assignments.append(assignment)

    return assignments


def classify_unmatched(
    all_rzp_ids: set[str],
    all_bank_ids: set[str],
    all_merchant_ids: set[str],
    assignments: list[dict],
) -> list[dict]:
    """Identify records that were not matched by global assignment."""
    matched_rzp: set[str] = set()
    matched_bank: set[str] = set()
    matched_merchant: set[str] = set()

    for a in assignments:
        if a["source_a"] == "razorpay":
            matched_rzp.add(a["source_a_id"])
        if a["source_b"] == "razorpay":
            matched_rzp.add(a["source_b_id"])
        if a["source_a"] == "bank":
            matched_bank.add(a["source_a_id"])
        if a["source_b"] == "bank":
            matched_bank.add(a["source_b_id"])
        if a["source_a"] == "merchant":
            matched_merchant.add(a["source_a_id"])
        if a["source_b"] == "merchant":
            matched_merchant.add(a["source_b_id"])

    unmatched: list[dict] = []
    for rid in all_rzp_ids - matched_rzp:
        unmatched.append({"source": "razorpay", "record_id": rid, "status": "UNMATCHED"})
    for rid in all_bank_ids - matched_bank:
        unmatched.append({"source": "bank", "record_id": rid, "status": "UNMATCHED"})
    for rid in all_merchant_ids - matched_merchant:
        unmatched.append({"source": "merchant", "record_id": rid, "status": "UNMATCHED"})

    return unmatched
