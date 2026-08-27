"""The deterministic refund gate.

Design rule for this module: no model, no network, no probability. Everything
here is arithmetic and policy. A language model may later be asked to raise
suspicion about a refund that passes every check below, but it is never
permitted to lower one: an ALLOW that a deterministic invariant refused stays
refused.

Check order is load-bearing and is asserted by the test suite:

    replay -> payment state -> amount validity -> policy holds -> allow

Blocks come before holds because a block means the action is invalid no matter
who signed off on it; a hold means a human still might. Velocity is evaluated
last so that a structured-refund scenario reports the structuring rather than
the side effect of having made several calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .audit_log import AuditLog
from .idempotency import IdempotencyLedger
from .money import is_valid_paise_amount
from .types import (
    Decision,
    Disposition,
    Payment,
    PaymentStatus,
    Policy,
    ReasonCode,
    RefundAttempt,
    RefundEvent,
)
from .velocity import RefundHistoryLedger, VelocityLedger

ACTION_TYPE = "refund.create"


@dataclass
class EvaluationContext:
    """Read-only view the pure evaluator is allowed to see."""

    payments: dict[str, Payment]
    policy: Policy = field(default_factory=Policy)
    idempotency: IdempotencyLedger = field(default_factory=IdempotencyLedger)
    velocity: VelocityLedger = field(default_factory=VelocityLedger)
    history: RefundHistoryLedger = field(default_factory=RefundHistoryLedger)


def _age_days(anchor: datetime, now: datetime) -> int:
    return (now - anchor).days


def evaluate_refund_attempt(attempt: RefundAttempt, context: EvaluationContext) -> Decision:
    """Pure function. Reads ledgers, mutates nothing."""
    policy = context.policy
    now = attempt.requested_at

    # 1. Replay. Checked first: a re-presented key must never be re-evaluated,
    #    because re-evaluating it is how a timeout becomes a double refund.
    replay = context.idempotency.check(
        attempt.idempotency_key, attempt.payment_id, attempt.amount_paise, attempt.speed.value
    )
    if replay.is_replay:
        return Decision(
            disposition=Disposition.BLOCK,
            reason_code=ReasonCode.IDEMPOTENCY_REPLAY,
            features={
                "idempotency_key": attempt.idempotency_key,
                "fingerprint_matches": replay.fingerprint_matches,
                "first_seen_seq": replay.first_seen_seq,
            },
        )

    # 2. Payment must exist before anything about it can be asserted.
    payment = context.payments.get(attempt.payment_id)
    if payment is None:
        return Decision(
            disposition=Disposition.BLOCK,
            reason_code=ReasonCode.PAYMENT_NOT_FOUND,
            features={"payment_id": attempt.payment_id},
        )

    consumed = payment.consumed_paise
    refundable = payment.refundable_paise
    age_days = _age_days(payment.refund_window_anchor, now)
    attempts_in_window = context.velocity.count_in_window(
        attempt.agent_id, now, policy.velocity_window_minutes
    )
    customer_window_total = context.history.customer_total_in_window(
        payment.customer_id, now, policy.customer_cumulative_window_hours
    )

    base = {
        "payment_amount_paise": payment.amount_paise,
        "existing_refunded_paise": consumed,
        "refundable_paise": refundable,
        "payment_age_days": age_days,
        "agent_attempts_in_window": attempts_in_window,
        "customer_refunds_in_window_paise": customer_window_total,
    }

    def verdict(disposition: Disposition, reason: ReasonCode, **extra) -> Decision:
        return Decision(disposition=disposition, reason_code=reason, features={**base, **extra})

    # 3. Payment state. Only a captured payment holds money that can come back.
    if payment.status is not PaymentStatus.CAPTURED:
        return verdict(
            Disposition.BLOCK,
            ReasonCode.PAYMENT_NOT_CAPTURED,
            payment_status=payment.status.value,
        )

    # 4. Nothing left to refund.
    if refundable <= 0:
        return verdict(Disposition.BLOCK, ReasonCode.PAYMENT_FULLY_REFUNDED)

    # 5. An active dispute means the money is already contested; refunding now
    #    risks paying the same claim twice.
    if payment.has_active_dispute:
        return verdict(Disposition.BLOCK, ReasonCode.ACTIVE_DISPUTE)

    # 6. Resolve the amount. Omitting it means the remaining balance, matching
    #    the behaviour of the Razorpay refund endpoint.
    is_full_refund = attempt.amount_paise is None
    resolved = refundable if is_full_refund else attempt.amount_paise

    # 7. Amount must be well-formed integer paise.
    if not is_valid_paise_amount(resolved):
        return verdict(
            Disposition.BLOCK,
            ReasonCode.INVALID_AMOUNT,
            submitted_amount=repr(attempt.amount_paise),
            submitted_type=type(attempt.amount_paise).__name__,
        )

    # 8. The figure the agent declared must equal the figure on the wire. This
    #    is the deterministic catch for rupee/paise confusion.
    if attempt.declared_intent_paise is not None and attempt.declared_intent_paise != resolved:
        return verdict(
            Disposition.BLOCK,
            ReasonCode.AMOUNT_INTENT_MISMATCH,
            declared_intent_paise=attempt.declared_intent_paise,
            submitted_amount_paise=resolved,
        )

    # 9. Cannot refund more than remains.
    if resolved > refundable:
        return verdict(
            Disposition.BLOCK, ReasonCode.AMOUNT_EXCEEDS_REFUNDABLE, requested_paise=resolved
        )

    # Holds below this line: the action is well-formed, a human decides.

    # 10. Outside the refund window the merchant published.
    if age_days > policy.refund_window_days:
        return verdict(
            Disposition.HOLD,
            ReasonCode.REFUND_WINDOW_EXCEEDED,
            requested_paise=resolved,
            window_days=policy.refund_window_days,
        )

    # 11. Instant refunds settle faster and cost the merchant more. An agent
    #     silently upgrading speed is spending money it was not granted.
    if attempt.speed.value == "optimum" and not attempt.mandate.can_use_optimum_refunds:
        return verdict(
            Disposition.HOLD,
            ReasonCode.SPEED_UPGRADE_REQUIRES_APPROVAL,
            requested_paise=resolved,
            requested_speed=attempt.speed.value,
        )

    # 12. Single-call ceiling for unattended refunds.
    if resolved > policy.per_call_approval_threshold_paise:
        return verdict(
            Disposition.HOLD,
            ReasonCode.PER_CALL_APPROVAL_THRESHOLD_EXCEEDED,
            requested_paise=resolved,
            threshold_paise=policy.per_call_approval_threshold_paise,
        )

    # 13. Structuring across calls on one payment.
    if consumed + resolved > policy.payment_cumulative_threshold_paise:
        return verdict(
            Disposition.HOLD,
            ReasonCode.PAYMENT_CUMULATIVE_THRESHOLD_EXCEEDED,
            requested_paise=resolved,
            cumulative_paise=consumed + resolved,
            threshold_paise=policy.payment_cumulative_threshold_paise,
        )

    # 14. Structuring across payments for one customer.
    if customer_window_total + resolved > policy.customer_cumulative_threshold_paise:
        return verdict(
            Disposition.HOLD,
            ReasonCode.CUSTOMER_CUMULATIVE_THRESHOLD_EXCEEDED,
            requested_paise=resolved,
            cumulative_paise=customer_window_total + resolved,
            threshold_paise=policy.customer_cumulative_threshold_paise,
        )

    # 15. Burst behaviour from one agent.
    if context.velocity.exceeds(
        attempt.agent_id, now, policy.velocity_max_attempts, policy.velocity_window_minutes
    ):
        return verdict(
            Disposition.HOLD,
            ReasonCode.AGENT_VELOCITY_EXCEEDED,
            requested_paise=resolved,
            max_attempts=policy.velocity_max_attempts,
        )

    return verdict(Disposition.ALLOW, ReasonCode.ALL_CHECKS_PASSED, requested_paise=resolved)


class RefundGuard:
    """Stateful gate around the pure evaluator.

    Owns the side effects the evaluator is not allowed to have: recording the
    attempt, burning the idempotency key, and writing the audit record. Every
    disposition is logged, including the ones that never reach Razorpay.
    """

    def __init__(self, context: EvaluationContext, audit: AuditLog | None = None) -> None:
        self.context = context
        self.audit = audit or AuditLog()

    def evaluate(self, attempt: RefundAttempt) -> Decision:
        decision = evaluate_refund_attempt(attempt, self.context)

        # A replay must not consume a second slot in the velocity window; every
        # other attempt counts, allowed or not.
        if decision.reason_code is not ReasonCode.IDEMPOTENCY_REPLAY:
            self.context.velocity.record(attempt.agent_id, attempt.requested_at)
            self.context.idempotency.record(
                attempt.idempotency_key,
                attempt.payment_id,
                attempt.amount_paise,
                attempt.speed.value,
            )

        if decision.disposition is Disposition.ALLOW:
            payment = self.context.payments[attempt.payment_id]
            self.context.history.record(
                RefundEvent(
                    customer_id=payment.customer_id,
                    payment_id=payment.payment_id,
                    amount_paise=decision.features["requested_paise"],
                    at=attempt.requested_at,
                )
            )

        self.audit.append(
            action_type=ACTION_TYPE,
            agent_id=attempt.agent_id,
            payment_id=attempt.payment_id,
            amount_paise=attempt.amount_paise,
            speed=attempt.speed.value,
            disposition=decision.disposition.value,
            reason_code=decision.reason_code.value,
            features=decision.features,
            at=attempt.requested_at,
        )
        return decision
