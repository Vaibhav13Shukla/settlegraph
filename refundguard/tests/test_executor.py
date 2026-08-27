"""Executor: only an ALLOW may move money."""

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
    RefundStatus,
    rupees_to_paise,
)
from refundguard.executor import LocalRefundExecutor, RefundNotAuthorized

NOW = datetime(2026, 8, 27, 10, 0, 0)
R = rupees_to_paise
MANDATE = AgentMandate(agent_id="agent_support_001", can_use_optimum_refunds=False)


def build_context() -> EvaluationContext:
    payment = Payment(
        payment_id="pay_001",
        customer_id="cust_001",
        amount_paise=R(1000),
        status=PaymentStatus.CAPTURED,
        created_at=NOW - timedelta(days=2),
        captured_at=NOW - timedelta(days=2),
    )
    return EvaluationContext(payments={payment.payment_id: payment})


def attempt(key: str, amount_paise: object) -> RefundAttempt:
    return RefundAttempt(
        idempotency_key=key,
        payment_id="pay_001",
        agent_id=MANDATE.agent_id,
        mandate=MANDATE,
        requested_at=NOW,
        amount_paise=amount_paise,
        speed=RefundSpeed.NORMAL,
    )


def test_allowed_refund_issues_a_receipt_and_consumes_balance():
    context = build_context()
    guard = RefundGuard(context)
    executor = LocalRefundExecutor()

    request = attempt("rcpt_1", R(400))
    decision = guard.evaluate(request)
    receipt = executor.execute(request, decision, context)

    assert receipt.amount_paise == R(400)
    assert receipt.status is RefundStatus.PROCESSED
    assert receipt.refund_id.startswith("rfnd_")
    assert context.payments["pay_001"].refundable_paise == R(600)


def test_executor_refuses_a_blocked_decision():
    """Fail closed. The executor is the last door and it does not take
    instructions from anyone but the gate."""
    context = build_context()
    guard = RefundGuard(context)
    executor = LocalRefundExecutor()

    request = attempt("rcpt_1", 500.50)
    decision = guard.evaluate(request)

    with pytest.raises(RefundNotAuthorized):
        executor.execute(request, decision, context)

    assert context.payments["pay_001"].refundable_paise == R(1000)


def test_executor_refuses_a_held_decision():
    context = build_context()
    guard = RefundGuard(context)
    executor = LocalRefundExecutor()

    request = RefundAttempt(
        idempotency_key="rcpt_1",
        payment_id="pay_001",
        agent_id=MANDATE.agent_id,
        mandate=MANDATE,
        requested_at=NOW,
        amount_paise=R(100),
        speed=RefundSpeed.OPTIMUM,
    )
    decision = guard.evaluate(request)

    with pytest.raises(RefundNotAuthorized):
        executor.execute(request, decision, context)
