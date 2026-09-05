"""Global assignment: constraint-based optimization of matched edges."""

from __future__ import annotations

from collections import defaultdict

from settlegraph.config import PipelineConfig
from settlegraph.models import NormalizedRecord


def _classify_assignment(confidence: float, config: PipelineConfig) -> str:
    if confidence >= config.auto_match_threshold:
        return "AUTO_MATCH"
    if confidence >= config.exception_threshold:
        return "LIKELY_MATCH"
    return "EXCEPTION"


def _leg(source_a: str, source_b: str) -> tuple[str, str]:
    """Canonical key for a reconciliation leg, order-independent."""
    first, second = sorted((source_a, source_b))
    return (first, second)


def _count_near_ties(
    scored_edges: list[tuple[NormalizedRecord, NormalizedRecord, float]],
    config: PipelineConfig,
) -> dict[tuple[str, tuple[str, str]], int]:
    """For each (record, leg), how many *other* candidates score within
    `ambiguity_margin` of that record's best candidate on that leg.

    Scoped per leg on purpose: a payment legitimately has both a bank
    counterpart and a merchant counterpart, and those are answers to
    different questions rather than competing explanations for the same one.
    Counting them as competition would suppress every well-matched payment
    in the batch.
    """
    # Keyed by *counterpart record id*, not by edge. `build_candidate_graph`
    # can legitimately propose the same pair more than once through
    # different routes -- a razorpay<->merchant pair is proposed once on
    # order_id and again on payment_id -- and those duplicates are one
    # candidate, not two competing ones. Counting edges instead of distinct
    # counterparts made every such pair look contested and suppressed 990
    # correct auto-matches on a real 1,000-record batch. Caught by measuring
    # the batch after the change rather than by any test.
    best_per_counterpart: dict[tuple[str, tuple[str, str]], dict[str, float]] = defaultdict(dict)
    for a, b, score in scored_edges:
        if a.source == b.source:
            continue
        leg = _leg(a.source, b.source)
        for record, counterpart in ((a, b), (b, a)):
            seen = best_per_counterpart[(record.record_id, leg)]
            prior = seen.get(counterpart.record_id)
            if prior is None or score > prior:
                seen[counterpart.record_id] = score

    near_ties: dict[tuple[str, tuple[str, str]], int] = {}
    for key, per_counterpart in best_per_counterpart.items():
        scores = list(per_counterpart.values())
        best = max(scores)
        # -1 excludes the winner itself from its own competitor count.
        near_ties[key] = sum(1 for s in scores if best - s <= config.ambiguity_margin) - 1
    return near_ties


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

    # Sort edges by confidence descending. Tie-break on record ids, not
    # insertion order: `sorted` is stable, so two edges with an identical
    # score (routine for `_score_bank_merchant`, which only ever produces
    # a handful of discrete values) used to be ordered however
    # `build_candidate_graph` happened to generate them. That made a
    # supposedly pure performance refactor of the candidate-generation loop
    # (see match.py, DEVLOG Day 4) capable of silently changing *which*
    # tied edge wins an exclusivity slot, even though the candidate set
    # itself was verified identical -- caught by code review, not by any
    # difference in aggregate precision/recall (bank-merchant is a
    # lower-priority cross-check leg, so it didn't move those numbers, but
    # it could still change which specific record ends up in the audit
    # trail). Ordering on ids makes the result independent of whatever
    # order candidates were generated in, not just "usually the same."
    sorted_edges = sorted(scored_edges, key=lambda e: (-e[2], e[0].record_id, e[1].record_id))

    # Computed over the full edge set before any exclusivity consumption, so
    # a competitor still counts even though it will later lose the slot --
    # that it existed at all is exactly the signal being preserved.
    near_ties = _count_near_ties(scored_edges, config)

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

        # "No competing explanation." A win by a hair over an equally
        # plausible alternative is a tie-break, not evidence, and this
        # system's whole claim is that it does not book on tie-breaks. Only
        # AUTO_MATCH is suppressed -- demoting a LIKELY_MATCH or an
        # EXCEPTION further would change nothing about what gets booked and
        # would only muddy the exception queue's reasons.
        leg = _leg(source_a, source_b)
        competitors = max(
            near_ties.get((a_id, leg), 0),
            near_ties.get((b_id, leg), 0),
        )
        abstention_reason = ""
        if label == "AUTO_MATCH" and competitors > 0:
            label = "LIKELY_MATCH"
            abstention_reason = (
                f"{competitors} competing candidate(s) within "
                f"{config.ambiguity_margin} of this score on the "
                f"{leg[0]}<->{leg[1]} leg; held for review rather than "
                "auto-booked on a tie-break"
            )

        assignment = {
            "source_a": source_a,
            "source_a_id": a_id,
            "source_b": source_b,
            "source_b_id": b_id,
            "confidence": round(confidence, 4),
            "label": label,
            "competing_candidates": competitors,
            "abstention_reason": abstention_reason,
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
