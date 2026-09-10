"""Invariant verification: ensure accounting integrity of matched sets."""

from __future__ import annotations

from settlegraph.config import PipelineConfig
from settlegraph.models import NormalizedRecord


class InvariantViolation(Exception):
    """Raised when a hard accounting invariant fails."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


def verify_amount_invariant(rzp: NormalizedRecord, bank: NormalizedRecord) -> bool:
    """Verify: bank credit should approximately equal Razorpay net settlement.

    Allow small tolerance for fees/taxes adjustments.
    """
    rzp_net = rzp.net_amount_paise or rzp.amount_paise
    bank_credit = bank.net_amount_paise or bank.amount_paise
    diff = abs(rzp_net - bank_credit)
    if diff > 100:  # 100 paise == Rs 1.00 tolerance (boundary pinned in test_verify.py)
        raise InvariantViolation(
            f"Amount mismatch: Razorpay net={rzp_net}, bank credit={bank_credit}, diff={diff}",
            {"rzp_net": rzp_net, "bank_credit": bank_credit, "diff": diff},
        )
    return True


def verify_date_invariant(
    rzp: NormalizedRecord, bank: NormalizedRecord, config: PipelineConfig
) -> bool:
    """Verify: bank value date should be within tolerance of Razorpay settlement date."""
    if not rzp.settlement_date or not bank.transaction_date:
        return True
    delta = abs((rzp.settlement_date - bank.transaction_date).days)
    if delta > config.date_tolerance_days:
        raise InvariantViolation(
            f"Date violation: Razorpay settled {rzp.settlement_date}, bank credited {bank.transaction_date} ({delta} days diff)",
            {"delta_days": delta},
        )
    return True


def verify_direction_invariant(bank: NormalizedRecord) -> bool:
    """Verify: a bank leg matched to a Razorpay settlement must be a credit.

    ``build_candidate_graph`` (engine/match.py) does not discriminate on
    ledger direction when proposing links -- a debit/adjustment row (e.g. a
    refund payout that also lands in the bank feed) can share a UTR or an
    amount+date window with a Razorpay settlement and get proposed as a
    candidate. Accepting a debit as if it were the settlement credit would
    corrupt both the audit trail and the revenue-assurance totals derived
    from it, so this is a hard invariant rather than a soft check.
    """
    if bank.source != "bank":
        return True
    direction = bank.provenance.get("direction")
    if direction == "debit":
        raise InvariantViolation(
            f"Direction violation: bank record {bank.record_id} is a debit "
            "line, not a settlement credit, and cannot satisfy a Razorpay "
            "settlement match.",
            {"record_id": bank.record_id, "direction": direction},
        )
    return True


def verify_record_type_invariant(rzp: NormalizedRecord) -> bool:
    """Verify: only an actual payment may be booked against a settlement credit.

    Razorpay's settlement feed carries more than payments -- `entity_type` is
    one of payment / refund / transfer / adjustment. A refund or an adjustment
    is not a payment, and matching one to a bank settlement credit books a
    settlement against a record that is not a settlement.

    `score_edge` never inspects `record_type` at all, so an adjustment with a
    matching UTR, exact amount and same-day date scores 0.95 -- byte-identical
    to the ordinary payment happy path -- and cleared the auto-match
    threshold. Found by `datagen/adversarial.py::new_transaction_category`.

    Two things kept this hidden. `normalize_razorpay` hardcoded
    `record_type="payment"` for every Razorpay row, discarding `entity_type`
    before anything downstream could see it; and `datagen/generator.py` only
    ever emits `entity_type="payment"`, so no generated batch could reach the
    case. The gap was therefore reachable on real merchant data and not on
    ours -- the worst combination, and the reason a green suite is not
    evidence of safety.

    Distinct from `verify_direction_invariant`, which checks the *bank* side's
    credit/debit provenance. This checks the *Razorpay* side's transaction
    category. A refund payout leaving the bank and a refund record arriving
    from the gateway are different failures and both must be caught.
    """
    if rzp.source != "razorpay":
        return True
    if rzp.record_type != "payment":
        raise InvariantViolation(
            f"Record type violation: Razorpay record {rzp.record_id} is a "
            f"'{rzp.record_type}', not a payment, and cannot satisfy a bank "
            "settlement-credit match.",
            {"record_id": rzp.record_id, "record_type": rzp.record_type},
        )
    return True


_UNKNOWN_MERCHANT = frozenset({"", "merch_unknown"})


def verify_merchant_invariant(rzp: NormalizedRecord, bank: NormalizedRecord) -> bool:
    """Verify: a settlement may only be booked against its own merchant's bank leg.

    ``build_candidate_graph`` (engine/match.py) already refuses to *propose* a
    cross-merchant edge (ADR 0010), so the deterministic pipeline never reaches
    this check with a mismatched pair -- here it is a no-op. It exists for the
    one path that does not go through the candidate graph:
    ``ai_reasoner._widen_candidates`` scans the whole bank pool on date+amount
    alone when a Razorpay exception had zero deterministic candidates, and the
    model's chosen candidate is booked as ``AI_RESOLVED_MATCH`` if it clears
    this gate. Without a merchant check, a same-amount, same-day bank credit
    belonging to a *different* merchant could be accepted -- money booked
    across a tenant boundary on the AI resolver's say-so. This makes ADR 0010's
    guarantee true for every path, not just the deterministic one: scoring (and
    the model) propose, verification decides.

    Fail-safe: only fires when both sides carry a real, differing merchant id.
    An unknown/empty id on either side cannot prove a cross-merchant violation
    (older fixtures and unpopulated rows default to ``merch_unknown``), so it is
    allowed through here and left to the other invariants -- mirroring
    ``match._same_merchant``.
    """
    a = rzp.merchant_id
    b = bank.merchant_id
    if a in _UNKNOWN_MERCHANT or b in _UNKNOWN_MERCHANT:
        return True
    if a != b:
        raise InvariantViolation(
            f"Merchant violation: Razorpay record {rzp.record_id} belongs to "
            f"'{a}' but bank record {bank.record_id} belongs to '{b}'; a "
            "settlement cannot be booked across a merchant boundary.",
            {"rzp_merchant": a, "bank_merchant": b},
        )
    return True


def verify_settlegraph_invariants(
    rzp: NormalizedRecord,
    bank: NormalizedRecord,
    config: PipelineConfig,
) -> list[InvariantViolation]:
    """Run all invariants on a matched Razorpay-bank pair.

    Returns list of violations (empty means all passed).
    """
    violations: list[InvariantViolation] = []
    try:
        verify_amount_invariant(rzp, bank)
    except InvariantViolation as e:
        violations.append(e)
    try:
        verify_date_invariant(rzp, bank, config)
    except InvariantViolation as e:
        violations.append(e)
    try:
        verify_direction_invariant(bank)
    except InvariantViolation as e:
        violations.append(e)
    try:
        verify_record_type_invariant(rzp)
    except InvariantViolation as e:
        violations.append(e)
    try:
        verify_merchant_invariant(rzp, bank)
    except InvariantViolation as e:
        violations.append(e)
    return violations
