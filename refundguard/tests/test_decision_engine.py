"""Day-1 acceptance suite.

Thirty deterministic cases, numbered to match the acceptance matrix. No model,
no network, no Razorpay call. If any of these go red, nothing downstream is
worth building.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from refundguard import (
    AgentMandate,
    Disposition,
    EvaluationContext,
    Payment,
    PaymentStatus,
    Policy,
    ReasonCode,
    Refund,
    RefundAttempt,
    RefundEvent,
    RefundGuard,
    RefundSpeed,
    RefundStatus,
    evaluate_refund_attempt,
    rupees_to_paise,
)

NOW = datetime(2026, 8, 27, 10, 0, 0)
R = rupees_to_paise

OPEN_MANDATE = AgentMandate(agent_id="agent_support_001", can_use_optimum_refunds=True)
LIMITED_MANDATE = AgentMandate(agent_id="agent_support_001", can_use_optimum_refunds=False)


def make_payment(
    *,
    payment_id: str = "pay_001",
    customer_id: str = "cust_001",
    amount_paise: int = R(1000),
    status: PaymentStatus = PaymentStatus.CAPTURED,
    age_days: int = 5,
    has_active_dispute: bool = False,
    refunds: tuple[Refund, ...] = (),
) -> Payment:
    captured_at = NOW - timedelta(days=age_days)
    return Payment(
        payment_id=payment_id,
        customer_id=customer_id,
        amount_paise=amount_paise,
        status=status,
        created_at=captured_at,
        captured_at=captured_at,
        has_active_dispute=has_active_dispute,
        refunds=refunds,
    )


def make_refund(amount_paise: int, status: RefundStatus = RefundStatus.PROCESSED) -> Refund:
    return Refund(
        refund_id=f"rfnd_{amount_paise}_{status.value}",
        amount_paise=amount_paise,
        status=status,
        created_at=NOW - timedelta(days=1),
    )


def make_attempt(
    *,
    amount_paise: object = R(500),
    payment_id: str = "pay_001",
    key: str = "rcpt_001",
    speed: RefundSpeed = RefundSpeed.NORMAL,
    mandate: AgentMandate = LIMITED_MANDATE,
    declared_intent_paise: int | None = None,
    at: datetime = NOW,
) -> RefundAttempt:
    return RefundAttempt(
        idempotency_key=key,
        payment_id=payment_id,
        agent_id=mandate.agent_id,
        mandate=mandate,
        requested_at=at,
        amount_paise=amount_paise,
        speed=speed,
        declared_intent_paise=declared_intent_paise,
    )


def make_context(*payments: Payment, policy: Policy | None = None) -> EvaluationContext:
    return EvaluationContext(
        payments={p.payment_id: p for p in payments}, policy=policy or Policy()
    )


def evaluate(attempt: RefundAttempt, context: EvaluationContext):
    return evaluate_refund_attempt(attempt, context)


# --------------------------------------------------------------------------
# Amount and minor-unit correctness (1-8)
# --------------------------------------------------------------------------


def test_01_allows_rupees_500_as_50000_paise():
    decision = evaluate(make_attempt(amount_paise=50_000), make_context(make_payment()))
    assert decision.disposition is Disposition.ALLOW
    assert decision.reason_code is ReasonCode.ALL_CHECKS_PASSED
    assert decision.features["requested_paise"] == 50_000


def test_02_blocks_decimal_rupee_amount():
    decision = evaluate(make_attempt(amount_paise=500.50), make_context(make_payment()))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.INVALID_AMOUNT
    assert decision.features["submitted_type"] == "float"


def test_03_blocks_string_amount():
    decision = evaluate(make_attempt(amount_paise="50000"), make_context(make_payment()))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.INVALID_AMOUNT
    assert decision.features["submitted_type"] == "str"


def test_04_blocks_zero_but_allows_full_refund_by_omission():
    context = make_context(make_payment())
    zero = evaluate(make_attempt(amount_paise=0), context)
    assert zero.disposition is Disposition.BLOCK
    assert zero.reason_code is ReasonCode.INVALID_AMOUNT

    omitted = evaluate(make_attempt(amount_paise=None, key="rcpt_002"), context)
    assert omitted.disposition is Disposition.ALLOW
    assert omitted.features["requested_paise"] == R(1000)


def test_05_blocks_negative_amount():
    decision = evaluate(make_attempt(amount_paise=-50_000), make_context(make_payment()))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.INVALID_AMOUNT


def test_06_blocks_amount_greater_than_refundable_balance():
    decision = evaluate(make_attempt(amount_paise=R(1500)), make_context(make_payment()))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.AMOUNT_EXCEEDS_REFUNDABLE


def test_07_blocks_minor_unit_under_refund_bug():
    """Agent means Rs 500 but sends 500 paise (Rs 5). Balance check cannot see this."""
    decision = evaluate(
        make_attempt(amount_paise=500, declared_intent_paise=R(500)),
        make_context(make_payment()),
    )
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.AMOUNT_INTENT_MISMATCH
    assert decision.features["declared_intent_paise"] == 50_000
    assert decision.features["submitted_amount_paise"] == 500


def test_08_blocks_minor_unit_over_refund_bug():
    """Agent means Rs 500 but sends Rs 50,000. Payment is large enough that the
    balance check would have let this through."""
    decision = evaluate(
        make_attempt(amount_paise=R(50_000), declared_intent_paise=R(500)),
        make_context(make_payment(amount_paise=R(100_000))),
    )
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.AMOUNT_INTENT_MISMATCH


# --------------------------------------------------------------------------
# Payment state (9-14)
# --------------------------------------------------------------------------


def test_09_blocks_refund_on_authorized_payment():
    decision = evaluate(make_attempt(), make_context(make_payment(status=PaymentStatus.AUTHORIZED)))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.PAYMENT_NOT_CAPTURED


def test_10_blocks_refund_on_failed_payment():
    decision = evaluate(make_attempt(), make_context(make_payment(status=PaymentStatus.FAILED)))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.PAYMENT_NOT_CAPTURED


def test_11_blocks_refund_on_cancelled_payment():
    decision = evaluate(make_attempt(), make_context(make_payment(status=PaymentStatus.CANCELLED)))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.PAYMENT_NOT_CAPTURED


def test_12_allows_refund_on_captured_payment():
    decision = evaluate(make_attempt(), make_context(make_payment()))
    assert decision.disposition is Disposition.ALLOW


def test_13_blocks_already_fully_refunded_payment():
    payment = make_payment(refunds=(make_refund(R(1000)),))
    decision = evaluate(make_attempt(), make_context(payment))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.PAYMENT_FULLY_REFUNDED


def test_14_blocks_payment_under_active_dispute():
    decision = evaluate(make_attempt(), make_context(make_payment(has_active_dispute=True)))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.ACTIVE_DISPUTE


# --------------------------------------------------------------------------
# Existing refunds, cumulative balance, replay (15-20)
# --------------------------------------------------------------------------


def test_15_allows_partial_refund_when_balance_remains():
    payment = make_payment(refunds=(make_refund(R(400)),))
    decision = evaluate(make_attempt(amount_paise=R(300)), make_context(payment))
    assert decision.disposition is Disposition.ALLOW
    assert decision.features["refundable_paise"] == R(600)


def test_16_blocks_when_processed_refunds_consume_full_amount():
    payment = make_payment(refunds=(make_refund(R(500)), make_refund(R(500))))
    decision = evaluate(make_attempt(amount_paise=R(100)), make_context(payment))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.PAYMENT_FULLY_REFUNDED


def test_17_blocks_when_pending_plus_processed_exceed_refundable():
    """A pending refund is not free balance. Treating it as free is how a
    retrying agent double-refunds."""
    payment = make_payment(
        refunds=(
            make_refund(R(600), RefundStatus.PROCESSED),
            make_refund(R(300), RefundStatus.PENDING),
        )
    )
    decision = evaluate(make_attempt(amount_paise=R(200)), make_context(payment))
    assert decision.disposition is Disposition.BLOCK
    assert decision.reason_code is ReasonCode.AMOUNT_EXCEEDS_REFUNDABLE
    assert decision.features["refundable_paise"] == R(100)


def test_18_blocks_duplicate_attempt_reusing_the_same_receipt():
    guard = RefundGuard(make_context(make_payment()))
    first = guard.evaluate(make_attempt(amount_paise=R(100), key="rcpt_dup"))
    second = guard.evaluate(make_attempt(amount_paise=R(100), key="rcpt_dup"))
    assert first.disposition is Disposition.ALLOW
    assert second.disposition is Disposition.BLOCK
    assert second.reason_code is ReasonCode.IDEMPOTENCY_REPLAY


def test_19_blocks_replay_and_reports_matching_fingerprint():
    guard = RefundGuard(make_context(make_payment()))
    guard.evaluate(make_attempt(amount_paise=R(100), key="rcpt_replay"))
    replay = guard.evaluate(make_attempt(amount_paise=R(100), key="rcpt_replay"))
    assert replay.reason_code is ReasonCode.IDEMPOTENCY_REPLAY
    assert replay.features["fingerprint_matches"] is True
    assert replay.features["first_seen_seq"] == 1


def test_20_allows_second_refund_on_same_payment_with_a_new_receipt():
    guard = RefundGuard(make_context(make_payment()))
    first = guard.evaluate(make_attempt(amount_paise=R(300), key="rcpt_a"))
    second = guard.evaluate(make_attempt(amount_paise=R(300), key="rcpt_b"))
    assert first.disposition is Disposition.ALLOW
    assert second.disposition is Disposition.ALLOW


# --------------------------------------------------------------------------
# Refund window (21-24)
# --------------------------------------------------------------------------


def test_21_allows_refund_inside_the_policy_window():
    decision = evaluate(make_attempt(), make_context(make_payment(age_days=5)))
    assert decision.disposition is Disposition.ALLOW


def test_22_holds_refund_outside_the_policy_window():
    decision = evaluate(make_attempt(), make_context(make_payment(age_days=45)))
    assert decision.disposition is Disposition.HOLD
    assert decision.reason_code is ReasonCode.REFUND_WINDOW_EXCEEDED


def test_23_boundary_allows_exactly_on_day_30():
    decision = evaluate(make_attempt(), make_context(make_payment(age_days=30)))
    assert decision.disposition is Disposition.ALLOW
    assert decision.features["payment_age_days"] == 30


def test_24_boundary_holds_on_day_31():
    decision = evaluate(make_attempt(), make_context(make_payment(age_days=31)))
    assert decision.disposition is Disposition.HOLD
    assert decision.reason_code is ReasonCode.REFUND_WINDOW_EXCEEDED


# --------------------------------------------------------------------------
# Refund speed (25-27)
# --------------------------------------------------------------------------


def test_25_allows_normal_speed():
    decision = evaluate(
        make_attempt(speed=RefundSpeed.NORMAL, mandate=LIMITED_MANDATE),
        make_context(make_payment()),
    )
    assert decision.disposition is Disposition.ALLOW


def test_26_holds_silent_speed_upgrade_to_optimum():
    decision = evaluate(
        make_attempt(speed=RefundSpeed.OPTIMUM, mandate=LIMITED_MANDATE),
        make_context(make_payment()),
    )
    assert decision.disposition is Disposition.HOLD
    assert decision.reason_code is ReasonCode.SPEED_UPGRADE_REQUIRES_APPROVAL


def test_27_allows_optimum_when_the_mandate_permits_it():
    decision = evaluate(
        make_attempt(speed=RefundSpeed.OPTIMUM, mandate=OPEN_MANDATE),
        make_context(make_payment()),
    )
    assert decision.disposition is Disposition.ALLOW


# --------------------------------------------------------------------------
# Structuring and velocity (28-30)
# --------------------------------------------------------------------------


def test_28_holds_third_partial_refund_that_structures_past_the_payment_threshold():
    """Three refunds of Rs 9,000 each clear a Rs 10,000 per-call cap. Only the
    running total on the payment catches it."""
    payment = make_payment(
        amount_paise=R(30_000),
        refunds=(make_refund(R(9_000)), make_refund(R(9_000))),
    )
    decision = evaluate(make_attempt(amount_paise=R(9_000)), make_context(payment))
    assert decision.disposition is Disposition.HOLD
    assert decision.reason_code is ReasonCode.PAYMENT_CUMULATIVE_THRESHOLD_EXCEEDED
    assert decision.features["cumulative_paise"] == R(27_000)


def test_29_holds_customer_level_structuring_across_separate_payments():
    payment = make_payment(payment_id="pay_002", amount_paise=R(15_000))
    context = make_context(payment)
    for index in range(2):
        context.history.record(
            RefundEvent(
                customer_id="cust_001",
                payment_id=f"pay_prior_{index}",
                amount_paise=R(22_000),
                at=NOW - timedelta(hours=1),
            )
        )
    decision = evaluate(make_attempt(amount_paise=R(9_000), payment_id="pay_002"), context)
    assert decision.disposition is Disposition.HOLD
    assert decision.reason_code is ReasonCode.CUSTOMER_CUMULATIVE_THRESHOLD_EXCEEDED
    assert decision.features["cumulative_paise"] == R(53_000)


def test_30_holds_agent_velocity_spike():
    context = make_context(make_payment())
    for minute in range(5):
        context.velocity.record("agent_support_001", NOW - timedelta(minutes=minute + 1))
    decision = evaluate(make_attempt(amount_paise=R(100)), context)
    assert decision.disposition is Disposition.HOLD
    assert decision.reason_code is ReasonCode.AGENT_VELOCITY_EXCEEDED
    assert decision.features["agent_attempts_in_window"] == 5
