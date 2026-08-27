"""Tool-boundary proxy.

Shaped like an MCP tool server so a merchant can repoint an existing agent at
RefundGuard without touching the agent. The agent keeps calling
``refunds.create`` with Razorpay-shaped arguments; the call now passes through
the gate on its way to the money.

The boundary carries a security property worth stating plainly: **anything the
agent can influence arrives in ``arguments``; anything it must not influence
lives on the proxy.** Identity and mandate are constructor arguments, set by
the merchant when the agent session is created. An agent that puts
``can_use_optimum_refunds`` or a different ``agent_id`` in its payload is
writing into a dictionary nobody reads.

The proxy also asks each agent to state the amount twice: ``amount`` in paise
on the wire, and ``declared_amount_inr`` in the units a human reasons in. The
proxy multiplies the second by 100 and requires agreement. An agent confused
about units will disagree with itself, and the disagreement is checkable
without any judgement about whether the refund was deserved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .audit_log import AuditLog
from .decision_engine import EvaluationContext, RefundGuard
from .executor import LocalRefundExecutor, RefundNotAuthorized
from .money import MINOR_UNITS_PER_RUPEE
from .types import AgentMandate, Disposition, RefundAttempt, RefundSpeed

REFUND_TOOL = "refunds.create"


@dataclass(frozen=True)
class ToolResult:
    """What the agent gets back. Refusals are results, not exceptions.

    An agent that receives an exception tends to retry. An agent that receives
    a structured refusal with a reason can explain itself to the customer and
    stop, which is the behaviour we want.
    """

    ok: bool
    disposition: str
    reason_code: str
    message: str
    refund_id: str | None = None
    audit_seq: int | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "disposition": self.disposition,
            "reason_code": self.reason_code,
            "message": self.message,
            "refund_id": self.refund_id,
            "audit_seq": self.audit_seq,
        }


MESSAGES = {
    "ALLOW": "Refund issued.",
    "HOLD": "Held for merchant review. Tell the customer a human is looking at it; do not retry.",
    "BLOCK": "Refused. Do not retry this action.",
}


class RefundToolProxy:
    def __init__(
        self,
        context: EvaluationContext,
        mandate: AgentMandate,
        clock: Callable[[], datetime] | None = None,
        executor: LocalRefundExecutor | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.context = context
        self.mandate = mandate
        self.clock = clock or datetime.now
        self.executor = executor or LocalRefundExecutor()
        self.guard = RefundGuard(context, audit=audit)

    def call(self, tool_name: str, arguments: dict) -> ToolResult:
        if tool_name != REFUND_TOOL:
            # Refused before the gate sees it: an unknown tool is not a refund
            # decision and must not occupy a slot in the decision record.
            return ToolResult(
                ok=False,
                disposition="BLOCK",
                reason_code="UNKNOWN_TOOL",
                message=f"{tool_name!r} is not exposed through this proxy.",
            )

        attempt = self._to_attempt(arguments)
        decision = self.guard.evaluate(attempt)
        audit_seq = len(self.guard.audit)

        if decision.disposition is not Disposition.ALLOW:
            return ToolResult(
                ok=False,
                disposition=decision.disposition.value,
                reason_code=decision.reason_code.value,
                message=MESSAGES[decision.disposition.value],
                audit_seq=audit_seq,
            )

        try:
            receipt = self.executor.execute(attempt, decision, self.context)
        except RefundNotAuthorized:  # pragma: no cover - defence in depth
            return ToolResult(
                ok=False,
                disposition="BLOCK",
                reason_code="EXECUTOR_REFUSED",
                message="The executor refused an action the gate allowed.",
                audit_seq=audit_seq,
            )

        return ToolResult(
            ok=True,
            disposition="ALLOW",
            reason_code=decision.reason_code.value,
            message=MESSAGES["ALLOW"],
            refund_id=receipt.refund_id,
            audit_seq=audit_seq,
        )

    @staticmethod
    def _parse_declared_amount(arguments: dict) -> tuple[int | None, bool]:
        """Return ``(paise, unparseable)`` for the agent's declared rupee figure.

        JSON has no integer type, so a model emitting ``400.0`` means 400 and
        is accepted. ``400.5`` is not a whole rupee, ``"400"`` is a string that
        slipped a type check somewhere upstream, and neither is something to
        guess at. Present-but-unreadable is reported so the gate can fail
        closed rather than skipping the cross-check.
        """
        if "declared_amount_inr" not in arguments:
            return None, False

        value = arguments["declared_amount_inr"]
        if isinstance(value, bool):
            return None, True
        if isinstance(value, int):
            return value * MINOR_UNITS_PER_RUPEE, False
        if isinstance(value, float) and value.is_integer():
            return int(value) * MINOR_UNITS_PER_RUPEE, False
        return None, True

    @staticmethod
    def _parse_speed(arguments: dict) -> tuple[RefundSpeed, str | None]:
        """Return ``(enum, raw)``. Unknown values are passed through, not coerced."""
        raw = arguments.get("speed")
        if raw is None:
            return RefundSpeed.NORMAL, None
        normalised = str(raw).strip().lower()
        if normalised == "optimum":
            return RefundSpeed.OPTIMUM, normalised
        return RefundSpeed.NORMAL, normalised

    def _to_attempt(self, arguments: dict) -> RefundAttempt:
        declared_paise, declared_unparseable = self._parse_declared_amount(arguments)
        speed, raw_speed = self._parse_speed(arguments)

        return RefundAttempt(
            # No default. A missing receipt is a missing idempotency key, and
            # the gate says so rather than inventing one.
            idempotency_key=str(arguments.get("receipt") or ""),
            payment_id=str(arguments.get("payment_id", "")),
            # Identity and mandate come from the session, never the payload.
            agent_id=self.mandate.agent_id,
            mandate=self.mandate,
            requested_at=self.clock(),
            amount_paise=arguments.get("amount"),
            speed=speed,
            declared_intent_paise=declared_paise,
            raw_speed=raw_speed,
            declared_amount_unparseable=declared_unparseable,
        )
