"""A scripted support agent and the inbox it reads.

The agent is naive on purpose. It reads a ticket, finds the most authoritative
instruction it can see, and acts on it. That is a fair deterministic model of
what a language model does with text containing directives, and it lets Day 2
demonstrate the failure without a model in the loop.

The one thing this agent does that a naive agent would not is record *where*
the number came from. That provenance is not used for any decision yet. It is
recorded because the agent genuinely knows it, and throwing it away at the
point of origin is how a system loses the only signal that could have saved it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .evidence import AmountOrigin, Evidence, Span, Trust
from .proxy import RefundToolProxy, ToolResult

# "refund of Rs 8500", "refund Rs 8,500", "refund of 8500"
DIRECTIVE = re.compile(
    r"refund\s+(?:of\s+)?(?:rs\.?|inr|₹)?\s*([\d,]+)",
    re.IGNORECASE,
)


class AmountSource(str, Enum):
    """Where the agent read the number it is about to spend."""

    MERCHANT_RECORD = "merchant_record"
    UNTRUSTED_TICKET_TEXT = "untrusted_ticket_text"


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    customer_id: str
    payment_id: str
    subject: str
    body: str
    approved_refund_inr: int | None = None


@dataclass(frozen=True)
class Proposal:
    ticket_id: str
    payment_id: str
    amount_inr: int
    amount_source: AmountSource
    quoted_span: str | None = None


class NaiveRefundAgent:
    def __init__(self, proxy: RefundToolProxy, declare_evidence: bool = True) -> None:
        """``declare_evidence=False`` is the ablation control.

        With provenance withheld the gate falls back to arithmetic and policy
        alone, which is precisely the rules-only baseline any claim about the
        evidence layer has to beat. Keeping the switch here rather than in the
        gate means the baseline runs the same decision code, so a difference in
        outcome is a difference in evidence and nothing else.
        """
        self.proxy = proxy
        self.declare_evidence = declare_evidence

    def propose(self, ticket: Ticket) -> Proposal | None:
        """Decide what to refund, if anything.

        A directive in the body wins over the merchant record, which is exactly
        the mistake: the most persuasive text in the room is the text an
        attacker controls.
        """
        match = DIRECTIVE.search(ticket.body)
        if match:
            amount = int(match.group(1).replace(",", ""))
            return Proposal(
                ticket_id=ticket.ticket_id,
                payment_id=ticket.payment_id,
                amount_inr=amount,
                amount_source=AmountSource.UNTRUSTED_TICKET_TEXT,
                quoted_span=match.group(0),
            )
        if ticket.approved_refund_inr is not None:
            return Proposal(
                ticket_id=ticket.ticket_id,
                payment_id=ticket.payment_id,
                amount_inr=ticket.approved_refund_inr,
                amount_source=AmountSource.MERCHANT_RECORD,
            )
        return None

    @staticmethod
    def evidence_for(ticket: Ticket, proposal: Proposal) -> Evidence:
        """Label the source material by where it came from.

        Note what this does *not* do: consult the agent. The ticket body is
        untrusted because it arrived from a helpdesk inbox, not because the
        agent judged it so, and an approved return amount is trusted because
        it came out of the merchant's own records. Both facts are properties
        of the fetch, which is why the runtime can state them and the agent
        cannot argue with them.
        """
        spans = [Span(label="customer_message", text=ticket.body, trust=Trust.UNTRUSTED)]
        if ticket.approved_refund_inr is not None:
            spans.append(
                Span(
                    label="return_record",
                    text=f"Approved return: Rs {ticket.approved_refund_inr}",
                    trust=Trust.TRUSTED,
                )
            )

        origin = (
            AmountOrigin.MERCHANT_RECORD
            if proposal.amount_source is AmountSource.MERCHANT_RECORD
            else AmountOrigin.UNTRUSTED_TEXT
        )
        approved_paise = (
            ticket.approved_refund_inr * 100 if ticket.approved_refund_inr is not None else None
        )
        return Evidence(
            spans=tuple(spans),
            amount_origin=origin,
            merchant_approved_paise=approved_paise,
        )

    def handle(self, ticket: Ticket) -> tuple[Proposal | None, ToolResult | None]:
        proposal = self.propose(ticket)
        if proposal is None:
            return None, None
        result = self.proxy.call(
            "refunds.create",
            {
                "payment_id": proposal.payment_id,
                "amount": proposal.amount_inr * 100,
                "speed": "normal",
                "receipt": f"rcpt_{ticket.ticket_id}",
                "declared_amount_inr": proposal.amount_inr,
                "reason": ticket.subject,
            },
            evidence=self.evidence_for(ticket, proposal) if self.declare_evidence else None,
        )
        return proposal, result

    def run(self, inbox: list[Ticket]) -> list[tuple[Proposal, ToolResult]]:
        outcomes = []
        for ticket in inbox:
            proposal, result = self.handle(ticket)
            if proposal is not None and result is not None:
                outcomes.append((proposal, result))
        return outcomes
