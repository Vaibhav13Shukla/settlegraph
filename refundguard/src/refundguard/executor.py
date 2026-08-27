"""Refund execution, and the refusal to execute.

This module stands in for the Razorpay refund endpoint. It exists as a separate
seam so that the thing which actually moves money has one entry point, and that
entry point independently re-checks the disposition it was handed.

That re-check is not redundancy for its own sake. The gate and the executor are
the two places a mistake could let money out, and an executor that trusts its
caller is an executor that will eventually be called by something other than
the gate.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from datetime import datetime

from .decision_engine import decision_binding
from .types import Decision, Disposition, Refund, RefundAttempt, RefundStatus


class RefundNotAuthorized(RuntimeError):
    """Raised when execution is attempted on anything but an ALLOW."""


@dataclass(frozen=True)
class RefundReceipt:
    refund_id: str
    payment_id: str
    amount_paise: int
    speed: str
    status: RefundStatus
    created_at: datetime


class LocalRefundExecutor:
    """In-process stand-in for the Razorpay refund endpoint.

    Day 2 keeps this local on purpose: the decision path has to be provably
    correct before a real API key is anywhere near it.
    """

    def __init__(self) -> None:
        self._counter = itertools.count(1)

    def execute(self, attempt: RefundAttempt, decision: Decision, context) -> RefundReceipt:
        if decision.disposition is not Disposition.ALLOW:
            raise RefundNotAuthorized(
                f"refusing {decision.disposition.value} ({decision.reason_code.value}); "
                "only ALLOW may move money"
            )

        amount_paise = decision.features["requested_paise"]

        # The approval must be an approval of *this* action. Without this the
        # executor is a confused deputy: hand it any ALLOW alongside any
        # attempt and it refunds the approved amount against the attempted
        # payment. It matters as soon as a decision outlives the call that
        # produced it, which is what the review queue does by design.
        expected = decision_binding(attempt, amount_paise)
        if decision.bound_to != expected:
            raise RefundNotAuthorized(
                "decision was not issued for this attempt; "
                f"bound_to={decision.bound_to!r} expected={expected!r}"
            )

        payment = context.payments[attempt.payment_id]
        refund = Refund(
            refund_id=f"rfnd_{next(self._counter):06d}",
            amount_paise=amount_paise,
            status=RefundStatus.PROCESSED,
            created_at=attempt.requested_at,
        )
        context.payments[attempt.payment_id] = replace(payment, refunds=payment.refunds + (refund,))
        return RefundReceipt(
            refund_id=refund.refund_id,
            payment_id=attempt.payment_id,
            amount_paise=amount_paise,
            speed=attempt.speed.value,
            status=refund.status,
            created_at=refund.created_at,
        )
