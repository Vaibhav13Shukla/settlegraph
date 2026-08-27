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
from .detectors import Finding, FindingKind
from .evidence import Evidence
from .executor import LocalRefundExecutor, RefundNotAuthorized
from .judge import JudgeRequest, adjudicate_hold
from .money import MINOR_UNITS_PER_RUPEE
from .types import AgentMandate, Decision, Disposition, RefundAttempt, RefundSpeed

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
        judge=None,
        merchant_policy: str = "",
    ) -> None:
        self.context = context
        self.mandate = mandate
        self.clock = clock or datetime.now
        self.executor = executor or LocalRefundExecutor()
        self.guard = RefundGuard(context, audit=audit)
        self.judge = judge
        self.merchant_policy = merchant_policy

    def call(self, tool_name: str, arguments: dict, evidence: Evidence | None = None) -> ToolResult:
        """Handle one tool call.

        ``evidence`` is a separate parameter, never a key in ``arguments``, and
        that separation is load-bearing. Provenance is a statement about which
        text the merchant wrote and which text a stranger wrote; an agent that
        could assert its own provenance would simply mark the attacker's
        instructions as trusted. In a deployment the runtime fetches the thread
        from the helpdesk and labels the spans by where it fetched them from.
        Nothing the agent says can change those labels.
        """
        if tool_name != REFUND_TOOL:
            # Refused before the gate sees it: an unknown tool is not a refund
            # decision and must not occupy a slot in the decision record.
            return ToolResult(
                ok=False,
                disposition="BLOCK",
                reason_code="UNKNOWN_TOOL",
                message=f"{tool_name!r} is not exposed through this proxy.",
            )

        attempt = self._to_attempt(arguments, evidence)
        decision = self.guard.evaluate(attempt)
        decision = self._adjudicate(attempt, decision)
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

    def _adjudicate(self, attempt: RefundAttempt, decision: Decision) -> Decision:
        """Give a judge, if one is configured, a look at a held refund.

        The gate's own verdict is already in the audit log by the time this
        runs, and an adjudication that changes it appends a second record
        rather than editing the first. Two decisions genuinely happened -- the
        automated checks held it, and something released or hardened it -- and
        a log that showed only the final state would be hiding the more
        interesting half.
        """
        if self.judge is None or decision.disposition is not Disposition.HOLD:
            return decision

        request = JudgeRequest(
            merchant_policy=self.merchant_policy,
            evidence=attempt.evidence,
            requested_paise=decision.features.get("requested_paise", 0),
            payment_summary=self._payment_summary(attempt),
            hold_reason=decision.reason_code.value,
            findings=tuple(
                Finding(kind=FindingKind(f["kind"]), excerpt=f["excerpt"], weight=f["weight"])
                for f in decision.features.get("evidence", {}).get("findings", [])
            ),
        )
        verdict = self.judge.adjudicate(request)
        adjudicated = adjudicate_hold(decision, verdict, self.context.policy, attempt=attempt)

        if adjudicated.disposition is not decision.disposition:
            self.guard.audit.append(
                action_type="refund.adjudicate",
                agent_id=attempt.agent_id,
                payment_id=attempt.payment_id,
                amount_paise=attempt.amount_paise,
                speed=attempt.audited_speed,
                disposition=adjudicated.disposition.value,
                reason_code=adjudicated.reason_code.value,
                features=adjudicated.features,
                at=attempt.requested_at,
            )
        return adjudicated

    def _payment_summary(self, attempt: RefundAttempt) -> str:
        payment = self.context.payments.get(attempt.payment_id)
        if payment is None:  # pragma: no cover - unreachable via a HOLD
            return attempt.payment_id
        return (
            f"{payment.payment_id}, {payment.amount_paise} paise captured, "
            f"{payment.consumed_paise} paise already refunded, "
            f"{len(payment.refunds)} prior refunds"
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

    def _to_attempt(self, arguments: dict, evidence: Evidence | None) -> RefundAttempt:
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
            # From the runtime, never from `arguments`.
            evidence=evidence if evidence is not None else Evidence(),
        )
