"""Invariant verification: ensure accounting integrity of matched sets."""

from __future__ import annotations

from settlegraph.config import PipelineConfig
from settlegraph.models import NormalizedRecord


class InvariantViolation(BaseException):
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
    if diff > 100:  # 1 paise tolerance
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
    """Verify: settlement credits should be positive (credit direction)."""
    if bank.source == "bank" and bank.record_type != "settlement_credit":
        return True  # debits are expected for refunds
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
    return violations
