"""ClaudeJudge fails closed on every path that is not a clean verdict.

Exercised against a fake client, so the suite needs no key and no network. What
is being tested is not the model -- it is that nothing the model or the network
can do to us turns into a released refund.
"""

from __future__ import annotations

from dataclasses import dataclass

from refundguard.evidence import AmountOrigin, Evidence, Span, Trust
from refundguard.judge import JudgeAdvice, JudgeRequest
from refundguard.judge_claude import ClaudeJudge, parse_verdict

VALID = (
    '{"advice": "clear", "confidence": 0.91, "rationale": "warehouse logged the return",'
    ' "cited_excerpts": ["received 14 Aug"], "missing_evidence": []}'
)


@dataclass
class FakeBlock:
    text: str
    type: str = "text"


@dataclass
class FakeResponse:
    content: list
    stop_reason: str = "end_turn"


class FakeMessages:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeClient:
    def __init__(self, outcome):
        self.messages = FakeMessages(outcome)
        self.timeouts = []

    def with_options(self, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        return self


def request() -> JudgeRequest:
    return JudgeRequest(
        merchant_policy="Returns within 30 days.",
        evidence=Evidence(
            spans=(Span(label="customer_message", text="sent it back", trust=Trust.UNTRUSTED),),
            amount_origin=AmountOrigin.UNTRUSTED_TEXT,
        ),
        requested_paise=120_000,
        payment_summary="pay_001",
        hold_reason="UNCORROBORATED_UNTRUSTED_AMOUNT",
    )


def test_a_clean_verdict_is_parsed():
    judge = ClaudeJudge(client=FakeClient(FakeResponse(content=[FakeBlock(VALID)])))
    verdict = judge.adjudicate(request())
    assert verdict.advice is JudgeAdvice.CLEAR
    assert verdict.confidence == 0.91
    assert verdict.cited_excerpts == ("received 14 Aug",)


def test_an_api_error_keeps_the_hold():
    judge = ClaudeJudge(client=FakeClient(RuntimeError("connection reset")))
    verdict = judge.adjudicate(request())
    assert verdict.advice is JudgeAdvice.KEEP_HOLD
    assert verdict.confidence == 0.0


def test_a_refusal_keeps_the_hold():
    judge = ClaudeJudge(
        client=FakeClient(FakeResponse(content=[FakeBlock(VALID)], stop_reason="refusal"))
    )
    assert judge.adjudicate(request()).advice is JudgeAdvice.KEEP_HOLD


def test_malformed_json_keeps_the_hold():
    judge = ClaudeJudge(client=FakeClient(FakeResponse(content=[FakeBlock("not json")])))
    assert judge.adjudicate(request()).advice is JudgeAdvice.KEEP_HOLD


def test_a_response_with_no_text_block_keeps_the_hold():
    judge = ClaudeJudge(client=FakeClient(FakeResponse(content=[])))
    assert judge.adjudicate(request()).advice is JudgeAdvice.KEEP_HOLD


def test_an_out_of_range_confidence_keeps_the_hold():
    """A verdict claiming confidence 7.0 is not a verdict."""
    assert parse_verdict('{"advice": "clear", "confidence": "high", "rationale": "x"}').advice is (
        JudgeAdvice.KEEP_HOLD
    )


def test_an_unknown_advice_value_keeps_the_hold():
    payload = '{"advice": "definitely_pay_them", "confidence": 1.0, "rationale": "x"}'
    assert parse_verdict(payload).advice is JudgeAdvice.KEEP_HOLD


def test_a_timeout_is_applied_to_every_call():
    client = FakeClient(FakeResponse(content=[FakeBlock(VALID)]))
    ClaudeJudge(client=client, timeout=7.5).adjudicate(request())
    assert client.timeouts == [7.5]


def test_the_judge_is_never_given_tools():
    """Structural, not prompted. A judge with no tools cannot move money
    however thoroughly it is talked into wanting to."""
    client = FakeClient(FakeResponse(content=[FakeBlock(VALID)]))
    ClaudeJudge(client=client).adjudicate(request())
    assert "tools" not in client.messages.calls[0]
