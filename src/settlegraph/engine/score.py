"""Edge scoring: assign Fellegi-Sunter weighted confidence to candidate links."""

from __future__ import annotations

from settlegraph.models import NormalizedRecord


def _score_razorpay_bank(rzp: NormalizedRecord, bank: NormalizedRecord) -> float:
    """Score link between Razorpay and Bank records.

    Discriminating fields:
    - UTR (0.60): Primary golden key linking settlement batch to bank credit
    - Net Amount (0.25): Exact match between net settled amount and bank credit
    - Date Proximity (0.10): Settlement date vs Bank value/transaction date
    - Description Tokens (0.05): Fuzzy containment of UTR, settlement_id, or payment_id in bank description
    """
    score = 0.0

    # 1. UTR Match
    if rzp.utr and bank.utr and rzp.utr == bank.utr:
        score += 0.60
    elif rzp.utr and bank.description and rzp.utr in bank.description:
        score += 0.50

    # 2. Amount Match (compare Razorpay net settled amount to Bank credit amount)
    rzp_net = rzp.net_amount_paise if rzp.net_amount_paise is not None else rzp.amount_paise
    bank_amt = bank.net_amount_paise if bank.net_amount_paise is not None else bank.amount_paise
    diff = abs(rzp_net - bank_amt)

    if diff == 0:
        score += 0.25
    elif diff <= 100:  # within 1 rupee
        score += 0.20
    elif rzp_net > 0 and (diff / rzp_net) < 0.01:  # within 1%
        score += 0.10

    # 3. Date Proximity
    date_a = rzp.settlement_date or rzp.transaction_date
    date_b = bank.settlement_date or bank.transaction_date
    if date_a and date_b:
        delta = abs((date_a - date_b).days)
        if delta == 0:
            score += 0.10
        elif delta <= 2:
            score += 0.07
        elif delta <= 5:
            score += 0.03

    # 4. Description Reference
    if bank.description:
        desc = bank.description
        if (
            rzp.settlement_id
            and rzp.settlement_id in desc
            or rzp.payment_id
            and rzp.payment_id in desc
        ):
            score += 0.05

    return min(score, 1.0)


def _score_razorpay_merchant(rzp: NormalizedRecord, merch: NormalizedRecord) -> float:
    """Score link between Razorpay and Merchant Ledger records.

    Discriminating fields:
    - Payment ID / Gateway ID (0.45): Direct gateway transaction identifier
    - Order ID (0.35): Merchant order identifier
    - Gross Amount (0.15): Merchant sale amount vs Razorpay captured gross amount
    - Date Proximity (0.05): Transaction creation dates
    """
    score = 0.0

    # 1. Payment ID / Gateway ID Match
    if (
        rzp.payment_id
        and merch.payment_id
        and rzp.payment_id == merch.payment_id
        or rzp.source_record_id
        and merch.payment_id
        and rzp.source_record_id == merch.payment_id
    ):
        score += 0.45

    # 2. Order ID Match
    if rzp.order_id and merch.order_id and rzp.order_id == merch.order_id:
        score += 0.35

    # 3. Gross Amount Match (compare gross transaction amounts)
    diff = abs(rzp.amount_paise - merch.amount_paise)
    if diff == 0:
        score += 0.15
    elif diff <= 100:
        score += 0.10

    # 4. Date Proximity
    if rzp.transaction_date and merch.transaction_date:
        delta = abs((rzp.transaction_date - merch.transaction_date).days)
        if delta == 0:
            score += 0.05
        elif delta <= 2:
            score += 0.03

    return min(score, 1.0)


def _score_bank_merchant(bank: NormalizedRecord, merch: NormalizedRecord) -> float:
    """Score link between Bank and Merchant records (cross-check)."""
    score = 0.0
    diff = abs(bank.amount_paise - merch.amount_paise)
    if diff == 0:
        score += 0.60
    elif diff <= 500:
        score += 0.40

    if bank.transaction_date and merch.transaction_date:
        delta = abs((bank.transaction_date - merch.transaction_date).days)
        if delta <= 2:
            score += 0.40
        elif delta <= 5:
            score += 0.20

    return min(score, 1.0)


def score_edge(a: NormalizedRecord, b: NormalizedRecord) -> float:
    """Score a candidate link between two normalized records."""
    sources = {a.source, b.source}
    if sources == {"razorpay", "bank"}:
        rzp = a if a.source == "razorpay" else b
        bank = b if b.source == "bank" else a
        return _score_razorpay_bank(rzp, bank)
    elif sources == {"razorpay", "merchant"}:
        rzp = a if a.source == "razorpay" else b
        merch = b if b.source == "merchant" else a
        return _score_razorpay_merchant(rzp, merch)
    elif sources == {"bank", "merchant"}:
        bank = a if a.source == "bank" else b
        merch = b if b.source == "merchant" else a
        return _score_bank_merchant(bank, merch)
    return 0.0


def score_all(
    candidates: list[tuple[NormalizedRecord, NormalizedRecord]],
) -> list[tuple[NormalizedRecord, NormalizedRecord, float]]:
    """Score every candidate link and return (a, b, confidence)."""
    return [(a, b, score_edge(a, b)) for a, b in candidates]
