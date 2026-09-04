"""Candidate graph: generate potential links between normalized records."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from settlegraph.config import PipelineConfig
from settlegraph.models import NormalizedRecord


def _same_utr(a: NormalizedRecord, b: NormalizedRecord) -> bool:
    return bool(a.utr and b.utr and a.utr == b.utr)


def _same_order_id(a: NormalizedRecord, b: NormalizedRecord) -> bool:
    return bool(a.order_id and b.order_id and a.order_id == b.order_id)


def _same_payment_id(a: NormalizedRecord, b: NormalizedRecord) -> bool:
    return bool(a.payment_id and b.payment_id and a.payment_id == b.payment_id)


def _within_date_tolerance(
    a: NormalizedRecord, b: NormalizedRecord, config: PipelineConfig
) -> bool:
    if not a.settlement_date or not b.transaction_date:
        return True
    delta = abs((a.settlement_date - b.transaction_date).days)
    return delta <= config.date_tolerance_days


def _amount_match(a: NormalizedRecord, b: NormalizedRecord, config: PipelineConfig) -> bool:
    diff = (
        abs(a.net_amount_paise - b.net_amount_paise)
        if a.net_amount_paise and b.net_amount_paise
        else abs(a.amount_paise - b.amount_paise)
    )
    return diff <= config.amount_tolerance_paise


def build_candidate_graph(
    rzp: list[NormalizedRecord],
    bank: list[NormalizedRecord],
    merchant: list[NormalizedRecord],
    config: PipelineConfig,
) -> list[tuple[NormalizedRecord, NormalizedRecord]]:
    """Generate candidate links between sources.

    Strategy:
    1. Razorpay <-> Bank: match on UTR (strongest signal) or settlement_date + amount.
    2. Razorpay <-> Merchant: match on order_id or payment_id.
    3. Bank <-> Merchant: match on amount + date tolerance (weaker, used for cross-check).
    """
    candidates: list[tuple[NormalizedRecord, NormalizedRecord]] = []

    # Razorpay -> Bank: UTR match (primary)
    rzp_by_utr: dict[str, NormalizedRecord] = {}
    for r in rzp:
        if r.utr:
            rzp_by_utr[r.utr] = r

    for b in bank:
        if b.utr and b.utr in rzp_by_utr:
            candidates.append((rzp_by_utr[b.utr], b))

    # Razorpay -> Bank: settlement_date + amount match (secondary, for missing UTR).
    # We only use this fallback when both sides have no UTR to avoid spurious links.
    for r in rzp:
        for b in bank:
            if r.utr or b.utr:
                continue
            if _within_date_tolerance(r, b, config) and _amount_match(r, b, config):
                candidates.append((r, b))

    # Razorpay -> Merchant: order_id or payment_id match
    merch_by_order: dict[str, NormalizedRecord] = {}
    merch_by_payment: dict[str, NormalizedRecord] = {}
    for m in merchant:
        if m.order_id:
            merch_by_order[m.order_id] = m
        if m.payment_id:
            merch_by_payment[m.payment_id] = m

    for r in rzp:
        if r.order_id and r.order_id in merch_by_order:
            candidates.append((r, merch_by_order[r.order_id]))
        if r.payment_id and r.payment_id in merch_by_payment:
            candidates.append((r, merch_by_payment[r.payment_id]))

    # Bank -> Merchant: amount + date tolerance (cross-check only).
    #
    # A naive nested loop here is O(len(bank) * len(merchant)) -- measured
    # at 0.18s / 0.73s / 3.0s for 500 / 1,000 / 2,000 records, a clean
    # quadratic that would make a 10k+ record stress run take minutes. Every
    # comparison this loop actually performs is gated on
    # `_within_date_tolerance`, so bucketing merchant records by date and
    # only scanning the handful of buckets within `date_tolerance_days`
    # produces exactly the same candidate pairs for a near-linear cost
    # instead. `_within_date_tolerance`'s existing permissive fallback (a
    # missing date matches everything) is preserved as its own path so this
    # is a pure performance change, not a semantics change.
    merchant_by_date: dict[object, list[NormalizedRecord]] = defaultdict(list)
    merchant_missing_date: list[NormalizedRecord] = []
    for m in merchant:
        if m.transaction_date:
            merchant_by_date[m.transaction_date].append(m)
        else:
            merchant_missing_date.append(m)

    for b in bank:
        b_date = b.settlement_date
        if not b_date:
            # Same fallback `_within_date_tolerance` itself would take:
            # a missing date passes the date check unconditionally.
            candidates.extend((b, m) for m in merchant if _amount_match(b, m, config))
            continue
        window = range(-config.date_tolerance_days, config.date_tolerance_days + 1)
        seen_ids: set[str] = set()
        for offset in window:
            for m in merchant_by_date.get(b_date + timedelta(days=offset), ()):
                if m.record_id in seen_ids:
                    continue
                seen_ids.add(m.record_id)
                if _amount_match(b, m, config):
                    candidates.append((b, m))
        for m in merchant_missing_date:
            if _amount_match(b, m, config):
                candidates.append((b, m))

    return candidates
