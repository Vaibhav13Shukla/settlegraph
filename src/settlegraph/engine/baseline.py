"""Baseline matching engine: naive deterministic rules for comparison benchmarking."""

from __future__ import annotations

from difflib import SequenceMatcher
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


def run_amount_date_baseline(
    rzp_records: list[NormalizedRecord],
    bank_records: list[NormalizedRecord],
    merchant_records: list[NormalizedRecord],
    date_tolerance_days: int = 3,
) -> list[dict[str, Any]]:
    """Baseline B: match on exact net amount plus a settlement date window,
    with no reference to the UTR at all.

    This exists because "amount + rough date" is the first thing anyone
    without domain context reaches for -- it looks reasonable, it's cheap
    to implement, and on a small enough books it even looks correct. The
    UTR is the one field a bank settlement credit and a razorpay payment
    both carry that is (in the absence of corruption) unique per transfer;
    throwing it away and matching on amount + date alone means the only
    thing distinguishing two candidates is a coincidence of timing.

    Rule: a razorpay record matches a bank record IFF the net amount
    (net_amount_paise, falling back to amount_paise) is exactly equal AND
    the razorpay settlement date (falling back to transaction date) is
    within `date_tolerance_days` of the bank transaction date. Matching
    proceeds in input order and enforces 1-to-1 exclusivity -- each bank
    record is consumed by at most one razorpay record -- but there is no
    global optimization: the first razorpay record to reach an eligible
    bank record takes it, whether or not it is the right one.

    This baseline lacks:
    - Any use of UTR, order_id, or other unique reference fields
    - Global bipartite optimization under conflict (see above: first-come,
      first-served among ambiguous candidates)
    - Any concept of abstention -- every match it makes is AUTO_MATCH

    Failure mode this demonstrates: two unrelated payments that happen to
    share an amount and land within the date window are indistinguishable
    to this baseline. It will confidently wire a payment to the wrong bank
    credit rather than raise its hand -- a false positive baked into the
    matching rule itself, not a bug. See
    test_amount_date_baseline_produces_a_false_match_when_ambiguous for a
    reproduction where the wrong pairing is picked in exactly this way.
    """
    assignments: list[dict[str, Any]] = []
    used_bank: set[str] = set()

    for r in rzp_records:
        rzp_net = r.net_amount_paise if r.net_amount_paise is not None else r.amount_paise
        rzp_date = r.settlement_date if r.settlement_date is not None else r.transaction_date

        for b in bank_records:
            if b.record_id in used_bank:
                continue
            bank_net = b.net_amount_paise if b.net_amount_paise is not None else b.amount_paise
            if bank_net != rzp_net:
                continue
            if abs((rzp_date - b.transaction_date).days) > date_tolerance_days:
                continue

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
            break

    return assignments


def run_fuzzy_baseline(
    rzp_records: list[NormalizedRecord],
    bank_records: list[NormalizedRecord],
    merchant_records: list[NormalizedRecord],
    similarity_threshold: float = 0.8,
) -> list[dict[str, Any]]:
    """Baseline C: fuzzy UTR similarity plus a loose amount tolerance.

    This exists because "just fuzzy-match the reference string" is the
    other obvious first move once someone notices UTRs sometimes come
    through corrupted (truncated, a digit flipped by a copy-paste, an OCR
    error from a scanned statement). It recovers some of the matches
    Baseline A's exact-equality rule would miss -- that's the whole
    point of the comparison -- but it does so by trading away exactness on
    BOTH fields at once (fuzzy reference AND tolerant amount), which is
    also the whole point of *why it's dangerous*: it will happily bridge
    two records that merely look similar rather than two records that
    actually correspond.

    Rule: a razorpay record matches a bank record IFF both carry a UTR,
    the amounts are within 2% of each other, and
    `difflib.SequenceMatcher(None, a_utr, b_utr).ratio()` is at least
    `similarity_threshold`. For each razorpay record, all eligible
    (still-unused) bank records are scored and the highest-scoring one is
    taken -- greedy, per razorpay record, not a global optimum. 1-to-1
    exclusivity is enforced: a bank record picked by one razorpay record
    is unavailable to any other.

    This baseline lacks:
    - Any bound on how "corrupted" a UTR is allowed to be beyond the raw
      similarity ratio -- two genuinely different UTRs that happen to
      share most of their characters (common with fixed-format
      references) will pass
    - Exact amount matching -- the 2% tolerance is wide enough to also
      paper over a real MDR fee or a genuinely different payment
    - Any concept of abstention -- every match it makes is AUTO_MATCH

    Failure mode this demonstrates: fuzzy-string plus loose-amount
    stacks two independent sources of false-positive risk. A corrupted
    UTR that *should* be recovered ("RZP000001" vs "RZP00000X") and a
    UTR that merely resembles another payment's reference by chance look
    identical to this rule -- it cannot tell "recovered a real match" from
    "coincidentally similar to the wrong one" apart.
    """
    assignments: list[dict[str, Any]] = []
    used_bank: set[str] = set()

    for r in rzp_records:
        if not r.utr:
            continue
        rzp_net = r.net_amount_paise if r.net_amount_paise is not None else r.amount_paise

        best_bank: NormalizedRecord | None = None
        best_score = 0.0
        for b in bank_records:
            if b.record_id in used_bank or not b.utr:
                continue
            bank_net = b.net_amount_paise if b.net_amount_paise is not None else b.amount_paise
            if bank_net <= 0:
                continue
            if abs(rzp_net - bank_net) / bank_net > 0.02:
                continue

            score = SequenceMatcher(None, r.utr, b.utr).ratio()
            if score >= similarity_threshold and score > best_score:
                best_score = score
                best_bank = b

        if best_bank is not None:
            used_bank.add(best_bank.record_id)
            assignments.append(
                {
                    "source_a": "razorpay",
                    "source_a_id": r.record_id,
                    "source_b": "bank",
                    "source_b_id": best_bank.record_id,
                    "confidence": 1.0,
                    "label": "AUTO_MATCH",
                    "a_amount_paise": r.amount_paise,
                    "b_amount_paise": best_bank.amount_paise,
                    "a_utr": r.utr,
                    "b_utr": best_bank.utr,
                    "a_order_id": r.order_id,
                    "b_order_id": best_bank.order_id,
                }
            )

    return assignments
