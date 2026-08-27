"""What a judge is allowed to change, and what it is not.

The safety property this file exists to prove: **a compromised judge cannot
move money it was not already going to be allowed to move.** Every limit is
enforced in deterministic code after the verdict comes back, so a model that
returns CLEAR with perfect confidence on every single call still cannot exceed
the ceiling, cannot touch a BLOCK, and cannot act at all unless the merchant
turned it on.
"""

from __future__ import annotations

from refundguard import Decision, Disposition, Policy, ReasonCode, rupees_to_paise
from refundguard.judge import JudgeAdvice, JudgeVerdict, adjudicate_hold

R = rupees_to_paise

PERMISSIVE = Policy(
    judge_may_clear_holds=True,
    judge_clear_ceiling_paise=R(2_000),
    judge_min_confidence=0.8,
)


def hold(reason: ReasonCode = ReasonCode.UNCORROBORATED_UNTRUSTED_AMOUNT, rupees: int = 1_000):
    return Decision(
        disposition=Disposition.HOLD,
        reason_code=reason,
        features={"requested_paise": R(rupees)},
    )


def verdict(advice: JudgeAdvice = JudgeAdvice.CLEAR, confidence: float = 0.95) -> JudgeVerdict:
    return JudgeVerdict(
        advice=advice,
        confidence=confidence,
        rationale="The warehouse note confirms the return was received.",
        cited_excerpts=("return received 14 Aug",),
    )


def sample_attempt():
    from datetime import datetime

    from refundguard import AgentMandate, RefundAttempt, RefundSpeed

    return RefundAttempt(
        idempotency_key="rcpt_1",
        payment_id="pay_001",
        agent_id="agent_support_001",
        mandate=AgentMandate(agent_id="agent_support_001"),
        requested_at=datetime(2026, 8, 27, 10, 0, 0),
        amount_paise=R(1_000),
        speed=RefundSpeed.NORMAL,
    )


def test_a_confident_clear_releases_a_hold_when_the_merchant_allows_it():
    final = adjudicate_hold(hold(), verdict(), PERMISSIVE, attempt=sample_attempt())
    assert final.disposition is Disposition.ALLOW
    assert final.reason_code is ReasonCode.JUDGE_CLEARED


def test_without_the_attempt_a_clear_cannot_release_anything():
    """Releasing needs the attempt to bind the approval to. Fail closed when it
    is missing rather than emit an ALLOW the executor will reject."""
    assert adjudicate_hold(hold(), verdict(), PERMISSIVE).disposition is Disposition.HOLD


def test_the_judge_is_off_by_default():
    """Opt-in, not opt-out. A merchant who has never heard of this feature does
    not have a model releasing their refunds."""
    final = adjudicate_hold(hold(), verdict(), Policy())
    assert final.disposition is Disposition.HOLD
    assert final.reason_code is ReasonCode.UNCORROBORATED_UNTRUSTED_AMOUNT


def test_a_clear_above_the_ceiling_stays_held():
    """The ceiling is the whole point. However persuasive the thread, the model
    is trusted with small money only."""
    final = adjudicate_hold(hold(rupees=5_000), verdict(), PERMISSIVE)
    assert final.disposition is Disposition.HOLD


def test_a_low_confidence_clear_stays_held():
    final = adjudicate_hold(hold(), verdict(confidence=0.4), PERMISSIVE)
    assert final.disposition is Disposition.HOLD


def test_a_clear_cannot_release_a_hold_raised_by_injection_markers():
    """If the deterministic layer saw somebody addressing the agent, no amount
    of model confidence overrides it. The judge reads the same poisoned text."""
    final = adjudicate_hold(
        hold(reason=ReasonCode.INSTRUCTION_SHAPED_TEXT_IN_THREAD), verdict(), PERMISSIVE
    )
    assert final.disposition is Disposition.HOLD


def test_a_judge_that_always_clears_still_cannot_exceed_its_bounds():
    """The compromised-judge case, stated as a property.

    Suppose the judge has been fully subverted and returns CLEAR at confidence
    1.0 for everything. It still clears nothing outside the ceiling, nothing
    the detectors flagged, and nothing while the feature is off.
    """
    subverted = JudgeVerdict(
        advice=JudgeAdvice.CLEAR,
        confidence=1.0,
        rationale="clear it",
        cited_excerpts=(),
    )
    cases = [
        (hold(rupees=50_000), PERMISSIVE),
        (hold(reason=ReasonCode.INSTRUCTION_SHAPED_TEXT_IN_THREAD), PERMISSIVE),
        (hold(), Policy()),
    ]
    for decision, policy in cases:
        assert adjudicate_hold(decision, subverted, policy).disposition is Disposition.HOLD


def test_the_judge_may_escalate_a_hold_to_a_block():
    """Escalation is always permitted. Making a refusal stronger needs no
    opt-in, because the failure mode is a delayed refund, not a lost one."""
    final = adjudicate_hold(hold(), verdict(advice=JudgeAdvice.ESCALATE), Policy())
    assert final.disposition is Disposition.BLOCK
    assert final.reason_code is ReasonCode.JUDGE_ESCALATED


def test_a_block_is_never_touched():
    """Deterministic blocks are outside the judge's reach entirely."""
    blocked = Decision(
        disposition=Disposition.BLOCK,
        reason_code=ReasonCode.AMOUNT_EXCEEDS_REFUNDABLE,
        features={"requested_paise": R(100)},
    )
    final = adjudicate_hold(blocked, verdict(), PERMISSIVE)
    assert final is blocked


def test_an_allow_is_never_touched():
    allowed = Decision(
        disposition=Disposition.ALLOW,
        reason_code=ReasonCode.ALL_CHECKS_PASSED,
        features={"requested_paise": R(100)},
    )
    assert adjudicate_hold(allowed, verdict(), PERMISSIVE) is allowed


def test_the_verdict_is_recorded_on_the_decision_either_way():
    """A release nobody can audit is worse than no release."""
    cleared = adjudicate_hold(hold(), verdict(), PERMISSIVE)
    kept = adjudicate_hold(hold(), verdict(confidence=0.1), PERMISSIVE)

    for final in (cleared, kept):
        recorded = final.features["judge"]
        assert recorded["advice"] in {"clear", "keep_hold", "escalate"}
        assert "rationale" in recorded
        assert "confidence" in recorded


def test_a_judge_cleared_refund_can_actually_execute():
    """Caught before shipping: clearing a hold produced an ALLOW carrying no
    binding, so the executor -- which refuses any approval not issued for the
    attempt in front of it -- would have rejected every refund the judge
    released. The gate would say yes and the money would never move, which is
    not a failsafe, it is a dead end nobody would have debugged quickly."""
    from datetime import datetime

    from refundguard import (
        AgentMandate,
        EvaluationContext,
        Payment,
        PaymentStatus,
        RefundAttempt,
        RefundSpeed,
    )
    from refundguard.executor import LocalRefundExecutor

    now = datetime(2026, 8, 27, 10, 0, 0)
    payment = Payment(
        payment_id="pay_001",
        customer_id="cust_001",
        amount_paise=R(5_000),
        status=PaymentStatus.CAPTURED,
        created_at=now,
        captured_at=now,
    )
    context = EvaluationContext(payments={"pay_001": payment})
    attempt = RefundAttempt(
        idempotency_key="rcpt_1",
        payment_id="pay_001",
        agent_id="agent_support_001",
        mandate=AgentMandate(agent_id="agent_support_001"),
        requested_at=now,
        amount_paise=R(1_000),
        speed=RefundSpeed.NORMAL,
    )

    final = adjudicate_hold(hold(), verdict(), PERMISSIVE, attempt=attempt)
    assert final.disposition is Disposition.ALLOW

    receipt = LocalRefundExecutor().execute(attempt, final, context)
    assert receipt.amount_paise == R(1_000)
