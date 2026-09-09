"""Candidate graph: generate potential links between normalized records."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from settlegraph.config import PipelineConfig
from settlegraph.models import NormalizedRecord

_UNKNOWN_MERCHANT = {"", "merch_unknown"}


def _same_merchant(a: NormalizedRecord, b: NormalizedRecord) -> bool:
    """Two records may only be linked if they belong to the same merchant.

    This is a hard isolation boundary, not a scoring signal: a Razorpay
    settlement for merchant A and a bank credit for merchant B that happen to
    share an amount, a date, or even (through a recycled bank reference
    number) a UTR must never become a candidate, because booking one
    merchant's settlement against another's bank account is a
    cross-tenant data-integrity failure no downstream invariant would catch.

    An unknown/defaulted merchant on either side is treated permissively so
    legacy single-tenant batches -- where `merchant_id` was never populated
    and defaults to ``merch_unknown`` -- reconcile exactly as they did before
    merchant identity existed. Once real merchant_ids are present (every
    generated batch), the boundary is enforced. See ADR 0010.
    """
    if a.merchant_id in _UNKNOWN_MERCHANT or b.merchant_id in _UNKNOWN_MERCHANT:
        return True
    return a.merchant_id == b.merchant_id


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

    # Razorpay -> Bank: UTR match (primary).
    #
    # Keyed to a *list*, not a single record. This used to be
    # `dict[utr] -> record`, which meant that when two payments shared a UTR
    # -- a recycled or corrupted bank reference number, which the generator
    # injects as `duplicate_utr_reuse` and real banks genuinely do -- the
    # second silently overwrote the first before scoring ever ran. The true
    # counterpart never became a candidate, and the bank credit was then
    # confidently AUTO_MATCHed to the wrong payment at 0.95 (UTR + exact
    # amount + same-day date) with nothing in the output signalling the
    # collision. A confident, invariant-passing, factually wrong ledger
    # entry is the worst output this system can produce.
    #
    # Found by `datagen/adversarial.py::high_confidence_wrong_match`. Keeping
    # every colliding record makes the competition visible to
    # `global_assign`, which abstains on it rather than resolving it by dict
    # insertion order.
    rzp_by_utr: dict[str, list[NormalizedRecord]] = defaultdict(list)
    for r in rzp:
        if r.utr:
            rzp_by_utr[r.utr].append(r)

    for b in bank:
        if b.utr:
            for r in rzp_by_utr.get(b.utr, ()):
                candidates.append((r, b))

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

    # Cross-merchant isolation (ADR 0010). Applied once over the fully-built
    # candidate set rather than at every append site above, so no future
    # candidate-generation branch can accidentally bypass it: a pair linking
    # two different known merchants is dropped before scoring ever sees it.
    return [(a, b) for a, b in candidates if _same_merchant(a, b)]
