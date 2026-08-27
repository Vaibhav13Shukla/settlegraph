"""Field-level provenance for a money action.

The deterministic invariants in `decision_engine` answer "is this action
arithmetically and contractually valid?". They cannot answer "is there any
trustworthy reason to do it?", because by the time a refund reaches them it is
just an integer. An integer read out of a warehouse return record and an
integer read out of a customer's email are indistinguishable at that point,
and they are not remotely the same thing.

So provenance is declared by whoever assembled the request -- the proxy, or the
agent harness -- and assessed here. That placement is the whole design: only
the caller knows where it looked.

The interface is deliberately narrow. Callers build an `Evidence`, and ask one
question of it via `assess`. Everything else -- which spans get scanned, how
findings are weighted, what corroboration means -- stays inside.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .detectors import Finding, scan, suspicion_score

# A single strong signal, or two weaker ones, is enough to want a human.
SUSPICION_ESCALATION_THRESHOLD = 30


class Trust(str, Enum):
    """Who wrote this text.

    TRUSTED means the merchant or their systems: policy documents, return
    records, internal notes. UNTRUSTED means anyone else, which in practice
    means the customer and anything they can put in front of the agent.
    """

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class AmountOrigin(str, Enum):
    """Where the number the agent is about to spend actually came from."""

    MERCHANT_RECORD = "merchant_record"
    UNTRUSTED_TEXT = "untrusted_text"
    AGENT_UNSOURCED = "agent_unsourced"


@dataclass(frozen=True)
class Span:
    label: str
    text: str
    trust: Trust


@dataclass(frozen=True)
class Evidence:
    """What the agent read, and where it got the figure.

    An empty Evidence means the caller declared nothing. That is a legitimate
    state for an integration that has not wired provenance yet, and it is
    reported honestly rather than assumed benign.
    """

    spans: tuple[Span, ...] = ()
    amount_origin: AmountOrigin = AmountOrigin.AGENT_UNSOURCED
    merchant_approved_paise: int | None = None

    @property
    def declared(self) -> bool:
        return bool(self.spans) or self.amount_origin is not AmountOrigin.AGENT_UNSOURCED


@dataclass(frozen=True)
class EvidenceAssessment:
    amount_origin: AmountOrigin
    corroborated: bool
    suspicion: int
    findings: tuple[Finding, ...] = ()
    evidence_declared: bool = False

    @property
    def needs_human(self) -> bool:
        """Whether a person should look before this money moves.

        Two independent reasons, and only two:

        1. Somebody in the thread is addressing the agent rather than a
           person. An approved return does not make the rest of the message
           harmless.

        2. Provenance was declared and the merchant's own records do not
           support the figure. That is not proof of an attack; a customer
           asking for a refund they are owed lands here too. It is a reason to
           look, which is why this produces a hold and never a block.

        Note that (2) applies to a claimed MERCHANT_RECORD origin as well as
        an untrusted one. An agent asserting that the warehouse supports a
        figure the warehouse has never heard of is describing an inconsistency,
        and an origin label that exempts itself from checking is not a control.

        An undeclared provenance is deliberately *not* a reason. Absent is not
        suspicious; it means the check is not running, and saying so is more
        useful than a hold nobody can action.
        """
        if self.suspicion >= SUSPICION_ESCALATION_THRESHOLD:
            return True
        if self.amount_origin is AmountOrigin.AGENT_UNSOURCED:
            return False
        return not self.corroborated

    @property
    def reason(self) -> str:
        if self.suspicion >= SUSPICION_ESCALATION_THRESHOLD:
            kinds = sorted({f.kind.value for f in self.findings})
            return f"instruction-shaped text in the thread ({', '.join(kinds)})"
        if not self.needs_human:
            return "evidence supports the amount"
        if self.amount_origin is AmountOrigin.UNTRUSTED_TEXT:
            return "the amount came from customer-controlled text with no merchant record to match"
        return "the amount was attributed to a merchant record that does not support it"

    def to_dict(self) -> dict:
        return {
            "amount_origin": self.amount_origin.value,
            "corroborated": self.corroborated,
            "suspicion": self.suspicion,
            "evidence_declared": self.evidence_declared,
            "findings": [f.to_dict() for f in self.findings],
        }


def assess(evidence: Evidence, requested_paise: int) -> EvidenceAssessment:
    """Assess one money action's supporting evidence.

    Only untrusted spans are scanned. That restriction is doing real work: it
    is what lets the detectors use blunt patterns like "as per company policy"
    without flagging the merchant's own policy document every single time.
    """
    findings: list[Finding] = []
    for span in evidence.spans:
        if span.trust is Trust.UNTRUSTED:
            findings.extend(scan(span.text))

    corroborated = (
        evidence.merchant_approved_paise is not None
        and evidence.merchant_approved_paise == requested_paise
    )

    return EvidenceAssessment(
        amount_origin=evidence.amount_origin,
        corroborated=corroborated,
        suspicion=suspicion_score(tuple(findings)),
        findings=tuple(findings),
        evidence_declared=evidence.declared,
    )
