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
    def __init__(self, proxy: RefundToolProxy) -> None:
        self.proxy = proxy

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
        )
        return proposal, result

    def run(self, inbox: list[Ticket]) -> list[tuple[Proposal, ToolResult]]:
        outcomes = []
        for ticket in inbox:
            proposal, result = self.handle(ticket)
            if proposal is not None and result is not None:
                outcomes.append((proposal, result))
        return outcomes
