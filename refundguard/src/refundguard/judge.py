"""Adjudication of held refunds, and the bounds the adjudicator works inside.

The evidence layer is deliberately conservative: an amount that came from a
customer's own words with no merchant record to match it gets held, and a great
many of those holds are honest customers who are owed their money. That is the
right default and it is also an operational problem, because a queue nobody can
clear is a queue that gets cleared without being read.

A judge reads the thread and says whether the evidence in it actually supports
the refund. What it is *allowed to do with that opinion* is decided here, in
deterministic code, after the verdict comes back.

The property that makes this safe to ship:

    A judge that has been completely subverted -- one that returns CLEAR at
    confidence 1.0 on every call -- still cannot release anything above the
    merchant's ceiling, anything the deterministic detectors flagged, or
    anything at all unless the merchant switched the feature on.

The model's opinion is an input to a policy function. It is never the policy.

Three further constraints, all structural rather than prompted:

* The judge only ever sees HOLD decisions. A BLOCK is arithmetic or contract,
  and no amount of persuasion in an email changes whether a refund exceeds the
  balance.
* The judge has no tools and returns no action, only a verdict. A successful
  injection can at worst produce a wrong label, never a money movement.
* Escalation needs no opt-in. Making a refusal stronger fails towards a delayed
  refund; making one weaker fails towards a lost one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .decision_engine import decision_binding
from .detectors import Finding
from .evidence import Evidence
from .types import Decision, Disposition, Policy, ReasonCode, RefundAttempt

# Holds the judge is never allowed to release, whatever it thinks. These are
# the ones raised because somebody in the thread was addressing the agent --
# and the judge reads that same thread.
UNRELEASABLE = frozenset({ReasonCode.INSTRUCTION_SHAPED_TEXT_IN_THREAD})


@dataclass(frozen=True)
class JudgeRequest:
    """Everything the judge is shown, and nothing else.

    Note what is absent: no tools, no account access, no ability to look
    anything up. The judge reasons over exactly this material and returns a
    label.
    """

    merchant_policy: str
    evidence: Evidence
    requested_paise: int
    payment_summary: str
    hold_reason: str
    findings: tuple[Finding, ...] = ()


class JudgeAdvice(str, Enum):
    CLEAR = "clear"
    KEEP_HOLD = "keep_hold"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class JudgeVerdict:
    advice: JudgeAdvice
    confidence: float
    rationale: str
    cited_excerpts: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "advice": self.advice.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "cited_excerpts": list(self.cited_excerpts),
            "missing_evidence": list(self.missing_evidence),
        }


UNAVAILABLE = JudgeVerdict(
    advice=JudgeAdvice.KEEP_HOLD,
    confidence=0.0,
    rationale="The judge was unavailable, so the hold stands.",
)


def adjudicate_hold(
    decision: Decision,
    verdict: JudgeVerdict,
    policy: Policy,
    attempt: RefundAttempt | None = None,
) -> Decision:
    """Apply a judge's verdict to a decision, within policy.

    Returns the original object untouched for anything that is not a HOLD, so
    the caller can pass every decision through without branching.

    ``attempt`` is required to release a hold. A cleared hold becomes an ALLOW,
    and the executor refuses any ALLOW that is not bound to the attempt in
    front of it, so an unbound release would be approved by the gate and then
    silently rejected at the door. Without the attempt the hold stands.
    """
    if decision.disposition is not Disposition.HOLD:
        return decision

    annotated = replace(decision, features={**decision.features, "judge": verdict.to_dict()})

    if verdict.advice is JudgeAdvice.ESCALATE:
        return replace(
            annotated,
            disposition=Disposition.BLOCK,
            reason_code=ReasonCode.JUDGE_ESCALATED,
        )

    if verdict.advice is not JudgeAdvice.CLEAR:
        return annotated

    if not policy.judge_may_clear_holds:
        return annotated
    if decision.reason_code in UNRELEASABLE:
        return annotated
    if verdict.confidence < policy.judge_min_confidence:
        return annotated
    requested = decision.features.get("requested_paise", 0)
    if requested > policy.judge_clear_ceiling_paise:
        return annotated
    if attempt is None:
        return annotated

    return replace(
        annotated,
        disposition=Disposition.ALLOW,
        reason_code=ReasonCode.JUDGE_CLEARED,
        bound_to=decision_binding(attempt, requested),
    )
