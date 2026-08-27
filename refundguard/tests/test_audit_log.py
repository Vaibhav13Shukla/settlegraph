"""Audit log: append-only, hash-chained, and logs every disposition."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from refundguard import (
    AgentMandate,
    AuditLog,
    Disposition,
    EvaluationContext,
    Payment,
    PaymentStatus,
    RefundAttempt,
    RefundGuard,
    RefundSpeed,
    rupees_to_paise,
)
from refundguard.audit_log import GENESIS_HASH

NOW = datetime(2026, 8, 27, 10, 0, 0)
R = rupees_to_paise
MANDATE = AgentMandate(agent_id="agent_support_001", can_use_optimum_refunds=False)


def build_guard() -> RefundGuard:
    payment = Payment(
        payment_id="pay_001",
        customer_id="cust_001",
        amount_paise=R(1000),
        status=PaymentStatus.CAPTURED,
        created_at=NOW - timedelta(days=2),
        captured_at=NOW - timedelta(days=2),
    )
    return RefundGuard(EvaluationContext(payments={payment.payment_id: payment}))


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


def test_first_record_chains_from_genesis():
    guard = build_guard()
    guard.evaluate(attempt("rcpt_1", R(100)))
    record = guard.audit.records[0]
    assert record.seq == 1
    assert record.prev_hash == GENESIS_HASH
    assert len(record.hash) == 64


def test_sequence_increments_and_chain_links():
    guard = build_guard()
    guard.evaluate(attempt("rcpt_1", R(100)))
    guard.evaluate(attempt("rcpt_2", R(100)))
    first, second = guard.audit.records
    assert [first.seq, second.seq] == [1, 2]
    assert second.prev_hash == first.hash
    assert guard.audit.verify_chain() == (True, None)


def test_blocked_actions_are_logged_too():
    """A refusal that leaves no trace is not a control, it is a silence."""
    guard = build_guard()
    guard.evaluate(attempt("rcpt_1", R(100)))
    blocked = guard.evaluate(attempt("rcpt_2", 500.50))
    assert blocked.disposition is Disposition.BLOCK
    assert len(guard.audit) == 2
    assert guard.audit.records[1].disposition == "BLOCK"


def test_rewriting_a_refusal_into_an_approval_breaks_the_chain():
    guard = build_guard()
    blocked = guard.evaluate(attempt("rcpt_1", 500.50))
    guard.evaluate(attempt("rcpt_2", R(100)))
    assert blocked.disposition is Disposition.BLOCK
    assert guard.audit.verify_chain() == (True, None)

    # The attack the chain exists for: turn a recorded refusal into an approval.
    guard.audit._records[0] = replace(guard.audit._records[0], disposition="ALLOW")
    intact, first_bad = guard.audit.verify_chain()
    assert intact is False
    assert first_bad == 1


def test_rewriting_the_evidence_breaks_the_chain():
    """Forging a justification after the fact must be as detectable as forging
    the verdict."""
    guard = build_guard()
    guard.evaluate(attempt("rcpt_1", R(100)))
    original = guard.audit.records[0]
    forged = dict(original.features)
    forged["refundable_paise"] = 99_999_999

    guard.audit._records[0] = replace(original, features=forged)
    intact, first_bad = guard.audit.verify_chain()
    assert intact is False
    assert first_bad == 1


def test_empty_log_verifies_and_head_is_genesis():
    log = AuditLog()
    assert len(log) == 0
    assert log.head_hash == GENESIS_HASH
    assert log.verify_chain() == (True, None)
