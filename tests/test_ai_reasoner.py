"""ClaudeReasoner fails closed on every path that is not a clean, verified
resolution.

Exercised against a fake client, so the suite needs no key and no network.
What is being tested is not the model -- it is that nothing the model or the
network can do turns into a ledger match without also clearing the same
deterministic invariant gate AUTO_MATCH has to clear.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from settlegraph.config import PipelineConfig
from settlegraph.engine.ai_reasoner import (
    ClaudeReasoner,
    Hypothesis,
    ReasoningRequest,
    _widen_candidates,
    merge_ai_assignments,
    parse_verdict,
    resolve_exceptions_with_ai,
)
from settlegraph.engine.exceptions import ExceptionReport
from settlegraph.models import NormalizedRecord

VALID = (
    '{"hypothesis": "resolve", "candidate_record_id": "bank_1", "confidence": 0.9,'
    ' "rationale": "amount and date line up", "cited_evidence": ["amount_paise"]}'
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


def _record(record_id: str, source: str, utr: str | None, amount: int = 10000) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        source=source,
        source_record_id=record_id,
        record_type="payment" if source == "razorpay" else "settlement_credit",
        payment_id=None,
        order_id=None,
        settlement_id=None,
        utr=utr,
        invoice_number=None,
        reference_text=None,
        amount_paise=amount,
        fee_paise=None,
        tax_paise=None,
        net_amount_paise=amount,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        settlement_date=date(2026, 1, 15),
        description=None,
        raw_record={},
        provenance={"source": source, "direction": "credit"},
    )


def _request(candidate_id: str = "bank_1") -> ReasoningRequest:
    exc = ExceptionReport(
        record_id="rzp_1",
        source="razorpay",
        category="MISSING_COUNTERPART",
        severity="HIGH",
        root_cause="no candidate",
        unexplained_amount_paise=10000,
        suggested_action="investigate",
        evidence={},
    )
    return ReasoningRequest(
        exception=exc,
        record={"record_id": "rzp_1", "amount_paise": 10000},
        candidates=({"record_id": candidate_id, "amount_paise": 10000},),
    )


def test_a_clean_resolve_verdict_is_parsed():
    reasoner = ClaudeReasoner(client=FakeClient(FakeResponse(content=[FakeBlock(VALID)])))
    verdict = reasoner.resolve(_request())
    assert verdict.hypothesis is Hypothesis.RESOLVE
    assert verdict.candidate_record_id == "bank_1"
    assert verdict.confidence == 0.9


def test_an_api_error_leaves_the_exception_open():
    reasoner = ClaudeReasoner(client=FakeClient(RuntimeError("connection reset")))
    verdict = reasoner.resolve(_request())
    assert verdict.hypothesis is Hypothesis.CANNOT_RESOLVE
    assert verdict.confidence == 0.0


def test_a_refusal_leaves_the_exception_open():
    reasoner = ClaudeReasoner(
        client=FakeClient(FakeResponse(content=[FakeBlock(VALID)], stop_reason="refusal"))
    )
    assert reasoner.resolve(_request()).hypothesis is Hypothesis.CANNOT_RESOLVE


def test_malformed_json_leaves_the_exception_open():
    reasoner = ClaudeReasoner(client=FakeClient(FakeResponse(content=[FakeBlock("not json")])))
    assert reasoner.resolve(_request()).hypothesis is Hypothesis.CANNOT_RESOLVE


def test_a_hallucinated_candidate_id_is_rejected():
    """The model naming a record id it was never shown is treated exactly
    like malformed JSON -- it is not given the benefit of the doubt."""
    payload = (
        '{"hypothesis": "resolve", "candidate_record_id": "bank_999", "confidence": 0.9,'
        ' "rationale": "x", "cited_evidence": []}'
    )
    verdict = parse_verdict(payload, valid_ids=frozenset({"bank_1"}))
    assert verdict.hypothesis is Hypothesis.CANNOT_RESOLVE


def test_an_out_of_range_confidence_leaves_the_exception_open():
    payload = '{"hypothesis": "resolve", "candidate_record_id": "bank_1", "confidence": 7.0, "rationale": "x", "cited_evidence": []}'
    assert parse_verdict(payload, frozenset({"bank_1"})).hypothesis is Hypothesis.CANNOT_RESOLVE


def test_no_candidates_shown_never_calls_the_model():
    """An exception with nothing to reason about doesn't even round-trip --
    there is no question to ask."""
    client = FakeClient(FakeResponse(content=[FakeBlock(VALID)]))
    reasoner = ClaudeReasoner(client=client)
    empty_request = ReasoningRequest(
        exception=_request().exception, record={"record_id": "rzp_1"}, candidates=()
    )
    verdict = reasoner.resolve(empty_request)
    assert verdict.hypothesis is Hypothesis.CANNOT_RESOLVE
    assert client.messages.calls == []


def test_the_reasoner_is_never_given_tools():
    """Structural, not prompted. A reasoner with no tools cannot move money
    or edit records however persuasive the candidate list looks."""
    client = FakeClient(FakeResponse(content=[FakeBlock(VALID)]))
    ClaudeReasoner(client=client).resolve(_request())
    assert "tools" not in client.messages.calls[0]


def test_a_timeout_is_applied_to_every_call():
    client = FakeClient(FakeResponse(content=[FakeBlock(VALID)]))
    ClaudeReasoner(client=client, timeout=5.0).resolve(_request())
    assert client.timeouts == [5.0]


# --- resolve_exceptions_with_ai: the hypothesis still has to clear the
# invariant gate, even when the model is maximally confident. ---


def test_a_verified_hypothesis_is_promoted_to_ai_resolved_match():
    rzp = _record("rzp_1", "razorpay", utr=None, amount=10000)
    bank = _record("bank_1", "bank", utr=None, amount=10000)
    exc = ExceptionReport(
        record_id="rzp_1",
        source="razorpay",
        category="MISSING_COUNTERPART",
        severity="HIGH",
        root_cause="no candidate",
        unexplained_amount_paise=10000,
        suggested_action="investigate",
        evidence={},
    )
    norm_map = {"rzp_1": rzp, "bank_1": bank}
    reasoner = ClaudeReasoner(client=FakeClient(FakeResponse(content=[FakeBlock(VALID)])))

    new_assignments, updated_reports, log = resolve_exceptions_with_ai(
        [exc], [(rzp, bank, 0.4)], norm_map, [bank], PipelineConfig(), reasoner=reasoner
    )

    assert len(new_assignments) == 1
    assert new_assignments[0]["label"] == "AI_RESOLVED_MATCH"
    assert updated_reports == []
    assert log[0]["outcome"] == "promoted"


def test_a_confident_but_invariant_failing_hypothesis_is_rejected():
    """Even a maximally confident model cannot buy its way past the ledger
    invariants -- an amount mismatch stays an amount mismatch."""
    rzp = _record("rzp_1", "razorpay", utr=None, amount=10000)
    bank = _record("bank_1", "bank", utr=None, amount=100)  # wildly mismatched
    exc = ExceptionReport(
        record_id="rzp_1",
        source="razorpay",
        category="MISSING_COUNTERPART",
        severity="HIGH",
        root_cause="no candidate",
        unexplained_amount_paise=10000,
        suggested_action="investigate",
        evidence={},
    )
    norm_map = {"rzp_1": rzp, "bank_1": bank}
    payload = (
        '{"hypothesis": "resolve", "candidate_record_id": "bank_1", "confidence": 1.0,'
        ' "rationale": "definitely a match", "cited_evidence": []}'
    )
    reasoner = ClaudeReasoner(client=FakeClient(FakeResponse(content=[FakeBlock(payload)])))

    new_assignments, updated_reports, log = resolve_exceptions_with_ai(
        [exc], [(rzp, bank, 0.4)], norm_map, [bank], PipelineConfig(), reasoner=reasoner
    )

    assert new_assignments == []
    assert len(updated_reports) == 1
    assert "ai_note" in updated_reports[0].evidence
    assert log[0]["outcome"] == "rejected_invariant_failure"


def test_widen_candidates_finds_a_near_exact_amount_within_date_window():
    record = _record("rzp_1", "razorpay", utr=None, amount=10000)
    same = _record("bank_close", "bank", utr=None, amount=10000)
    # 2.5x -- outside both the near-exact band and the ~100x unit-confusion
    # band, so this must never be treated as plausible.
    implausible = _record("bank_far", "bank", utr=None, amount=25000)

    found = _widen_candidates(record, [same, implausible])

    assert [r.record_id for r in found] == ["bank_close"]


def test_widen_candidates_never_crosses_a_merchant_boundary():
    """The widen scan bypasses the candidate graph (that is its whole point --
    it runs when the graph produced nothing). So it must enforce merchant
    isolation itself: a perfect same-amount, same-day bank leg belonging to a
    *different* merchant must not be surfaced to the model at all. The
    same-merchant leg is still found. This is the AI-path counterpart to
    `verify_merchant_invariant` -- ADR 0010 must hold here too."""
    record = _record("rzp_1", "razorpay", utr=None, amount=10000)
    record.merchant_id = "merch_apollo"
    same_merchant = _record("bank_same", "bank", utr=None, amount=10000)
    same_merchant.merchant_id = "merch_apollo"
    other_merchant = _record("bank_other", "bank", utr=None, amount=10000)
    other_merchant.merchant_id = "merch_zomato"

    found = _widen_candidates(record, [same_merchant, other_merchant])

    assert [r.record_id for r in found] == ["bank_same"]


def test_merge_ai_assignments_removes_the_stale_row_for_a_promoted_record():
    """Code-review finding: a promoted record's old EXCEPTION/LIKELY_MATCH
    row must not survive alongside its new AI_RESOLVED_MATCH row -- that
    would leave the same record_id with two conflicting outcomes in
    assignments.csv."""
    stale = {"source_a_id": "rzp_1", "source_b_id": "bank_old", "label": "EXCEPTION"}
    unrelated = {"source_a_id": "rzp_2", "source_b_id": "bank_2", "label": "AUTO_MATCH"}
    promoted = {"source_a_id": "rzp_1", "source_b_id": "bank_new", "label": "AI_RESOLVED_MATCH"}

    merged = merge_ai_assignments([stale, unrelated], [promoted])

    assert stale not in merged
    assert unrelated in merged
    assert promoted in merged
    assert sum(1 for a in merged if a["source_a_id"] == "rzp_1") == 1


def test_merge_ai_assignments_is_a_no_op_with_no_promotions():
    assignments = [{"source_a_id": "rzp_1", "source_b_id": "bank_1", "label": "EXCEPTION"}]
    assert merge_ai_assignments(assignments, []) == assignments


def test_widen_candidates_catches_the_hundredx_unit_confusion_band():
    record = _record("rzp_1", "razorpay", utr=None, amount=10000)
    scaled = _record("bank_scaled", "bank", utr=None, amount=1_000_000)

    found = _widen_candidates(record, [scaled])

    assert [r.record_id for r in found] == ["bank_scaled"]
