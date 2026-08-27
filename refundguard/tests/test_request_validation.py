"""Hardening found by review: the gate must fail closed on malformed requests.

Three defects shared one root cause. The proxy was quietly normalising values it
did not understand -- a float declaration, an unrecognised speed, a missing
receipt -- and handing the gate a tidied-up request that no longer described
what the agent asked for. Tidying up before an audit boundary is how a log
becomes a faithful record of a request nobody made.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from refundguard import (
    AgentMandate,
    EvaluationContext,
    Payment,
    PaymentStatus,
    rupees_to_paise,
)
from refundguard.proxy import RefundToolProxy

NOW = datetime(2026, 8, 27, 10, 0, 0)
R = rupees_to_paise
MANDATE = AgentMandate(agent_id="agent_support_001", can_use_optimum_refunds=False)


def build() -> tuple[RefundToolProxy, EvaluationContext]:
    payment = Payment(
        payment_id="pay_001",
        customer_id="cust_001",
        amount_paise=R(1000),
        status=PaymentStatus.CAPTURED,
        created_at=NOW - timedelta(days=2),
        captured_at=NOW - timedelta(days=2),
    )
    context = EvaluationContext(payments={payment.payment_id: payment})
    return RefundToolProxy(context=context, mandate=MANDATE, clock=lambda: NOW), context


def call(proxy: RefundToolProxy, **overrides):
    arguments = {
        "payment_id": "pay_001",
        "amount": 40_000,
        "speed": "normal",
        "receipt": "rcpt_1",
        "declared_amount_inr": 400,
    }
    arguments.update(overrides)
    return proxy.call("refunds.create", arguments)


# --- declared amount -------------------------------------------------------


def test_integral_float_declaration_is_accepted():
    """JSON has no integer type. 400.0 is an ordinary way to say 400."""
    proxy, _ = build()
    assert call(proxy, declared_amount_inr=400.0).ok is True


def test_integral_float_declaration_still_catches_a_unit_error():
    proxy, context = build()
    result = call(proxy, amount=400, declared_amount_inr=400.0)
    assert result.ok is False
    assert result.reason_code == "AMOUNT_INTENT_MISMATCH"
    assert context.payments["pay_001"].refundable_paise == R(1000)


def test_unparseable_declaration_blocks_instead_of_skipping_the_check():
    """The old behaviour silently dropped the cross-check. Failing open on the
    headline defence is worse than not having it."""
    proxy, context = build()
    for bad in ("400", 400.5, None, [400]):
        result = call(proxy, declared_amount_inr=bad, receipt=f"rcpt_{bad!r}")
        assert result.ok is False, f"{bad!r} should not pass"
        assert result.reason_code == "DECLARED_AMOUNT_UNPARSEABLE"
    assert context.payments["pay_001"].refundable_paise == R(1000)


def test_omitting_the_declaration_entirely_is_still_allowed():
    """Absent is different from present-and-broken. An agent that never
    declares is running without the cross-check, which is a policy choice."""
    proxy, _ = build()
    arguments = {
        "payment_id": "pay_001",
        "amount": 40_000,
        "speed": "normal",
        "receipt": "rcpt_1",
    }
    assert proxy.call("refunds.create", arguments).ok is True


# --- refund speed ----------------------------------------------------------


def test_unrecognised_speed_is_refused_not_coerced():
    proxy, _ = build()
    result = call(proxy, speed="express")
    assert result.ok is False
    assert result.reason_code == "UNSUPPORTED_REFUND_SPEED"


def test_capitalised_optimum_is_not_downgraded_to_normal():
    """The old code mapped anything != 'optimum' to NORMAL, so 'OPTIMUM' both
    bypassed the mandate check and was logged as a request for 'normal'."""
    proxy, _ = build()
    result = call(proxy, speed="OPTIMUM")
    assert result.ok is False
    assert result.reason_code == "SPEED_UPGRADE_REQUIRES_APPROVAL"


def test_the_audit_log_records_the_speed_the_agent_actually_asked_for():
    proxy, _ = build()
    call(proxy, speed="OPTIMUM")
    assert proxy.guard.audit.records[-1].speed == "optimum"


# --- idempotency key -------------------------------------------------------


def test_missing_receipt_is_named_as_such_not_reported_as_a_replay():
    proxy, _ = build()
    arguments = {"payment_id": "pay_001", "amount": 10_000, "declared_amount_inr": 100}
    result = proxy.call("refunds.create", arguments)
    assert result.ok is False
    assert result.reason_code == "MISSING_IDEMPOTENCY_KEY"


def test_two_receiptless_calls_both_report_the_real_fault():
    """Previously the first burned the empty key and the second was refused as
    a duplicate, telling the merchant the wrong story."""
    proxy, _ = build()
    arguments = {"payment_id": "pay_001", "amount": 10_000, "declared_amount_inr": 100}
    first = proxy.call("refunds.create", arguments)
    second = proxy.call("refunds.create", {**arguments, "payment_id": "pay_001"})
    assert first.reason_code == "MISSING_IDEMPOTENCY_KEY"
    assert second.reason_code == "MISSING_IDEMPOTENCY_KEY"
