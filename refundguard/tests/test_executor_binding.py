"""An approval is an approval of one specific action.

The executor used to take `attempt` and `decision` as unrelated arguments and
trust that they belonged together. Nothing enforced it. That is fine while a
single call path constructs both in the same breath, and stops being fine the
moment a decision is stored and replayed -- which is exactly what the human
review queue will do.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from refundguard import (
    AgentMandate,
    EvaluationContext,
    Payment,
    PaymentStatus,
    RefundAttempt,
    RefundGuard,
    RefundSpeed,
    rupees_to_paise,
)
from refundguard.executor import LocalRefundExecutor, RefundNotAuthorized

NOW = datetime(2026, 8, 27, 10, 0, 0)
R = rupees_to_paise
MANDATE = AgentMandate(agent_id="agent_support_001", can_use_optimum_refunds=False)


def payment(payment_id: str, rupees: int) -> Payment:
    at = NOW - timedelta(days=2)
    return Payment(
        payment_id=payment_id,
        customer_id=f"cust_{payment_id}",
        amount_paise=R(rupees),
        status=PaymentStatus.CAPTURED,
        created_at=at,
        captured_at=at,
    )


def build() -> EvaluationContext:
    payments = {p.payment_id: p for p in (payment("pay_small", 200), payment("pay_big", 50_000))}
    return EvaluationContext(payments=payments)


def attempt(payment_id: str, key: str, rupees: int) -> RefundAttempt:
    return RefundAttempt(
        idempotency_key=key,
        payment_id=payment_id,
        agent_id=MANDATE.agent_id,
        mandate=MANDATE,
        requested_at=NOW,
        amount_paise=R(rupees),
        speed=RefundSpeed.NORMAL,
    )


def test_executor_refuses_a_decision_issued_for_a_different_payment():
    """The confused-deputy case: a cheap approval reused against a rich payment."""
    context = build()
    guard = RefundGuard(context)
    executor = LocalRefundExecutor()

    cheap = attempt("pay_small", "rcpt_1", 100)
    approval = guard.evaluate(cheap)
    assert approval.disposition.value == "ALLOW"

    elsewhere = attempt("pay_big", "rcpt_2", 100)
    with pytest.raises(RefundNotAuthorized):
        executor.execute(elsewhere, approval, context)

    assert context.payments["pay_big"].refundable_paise == R(50_000)


def test_executor_refuses_a_decision_issued_for_a_different_amount():
    context = build()
    guard = RefundGuard(context)
    executor = LocalRefundExecutor()

    approval = guard.evaluate(attempt("pay_big", "rcpt_1", 100))
    inflated = attempt("pay_big", "rcpt_1", 9_000)

    with pytest.raises(RefundNotAuthorized):
        executor.execute(inflated, approval, context)

    assert context.payments["pay_big"].refundable_paise == R(50_000)


def test_executor_refuses_a_decision_replayed_under_a_new_receipt():
    context = build()
    guard = RefundGuard(context)
    executor = LocalRefundExecutor()

    approval = guard.evaluate(attempt("pay_big", "rcpt_1", 100))
    rekeyed = attempt("pay_big", "rcpt_2", 100)

    with pytest.raises(RefundNotAuthorized):
        executor.execute(rekeyed, approval, context)


def test_the_matching_attempt_still_executes():
    context = build()
    guard = RefundGuard(context)
    executor = LocalRefundExecutor()

    request = attempt("pay_big", "rcpt_1", 100)
    approval = guard.evaluate(request)
    receipt = executor.execute(request, approval, context)

    assert receipt.amount_paise == R(100)
    assert context.payments["pay_big"].refundable_paise == R(49_900)
