"""Tests for global assignment."""

from __future__ import annotations

from datetime import date

from settlegraph.config import PipelineConfig
from settlegraph.engine.assign import classify_unmatched, global_assign
from settlegraph.models import NormalizedRecord


def _make_rzp(
    record_id: str,
    utr: str | None = None,
    order_id: str | None = None,
    amount: int = 10000,
    net: int = 9764,
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        source="razorpay",
        source_record_id=record_id,
        record_type="payment",
        payment_id=None,
        order_id=order_id,
        settlement_id="setl_1",
        utr=utr,
        invoice_number=None,
        reference_text=None,
        amount_paise=amount,
        fee_paise=200,
        tax_paise=36,
        net_amount_paise=net,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        settlement_date=date(2026, 1, 17),
        description=None,
        raw_record={},
        provenance={"source": "razorpay"},
    )


def _make_bank(
    record_id: str,
    utr: str | None = None,
    amount: int = 9764,
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        source="bank",
        source_record_id=record_id,
        record_type="settlement_credit",
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
        transaction_date=date(2026, 1, 17),
        settlement_date=date(2026, 1, 17),
        description=None,
        raw_record={},
        provenance={"source": "bank"},
    )


def test_high_confidence_edge_becomes_auto_match() -> None:
    config = PipelineConfig(auto_match_threshold=0.95, exception_threshold=0.70)
    rzp = _make_rzp("rzp_1", utr="RZP001")
    bank = _make_bank("bank_1", utr="RZP001")

    scored = [(rzp, bank, 0.98)]
    assignments = global_assign(scored, config)

    assert len(assignments) == 1
    assert assignments[0]["label"] == "AUTO_MATCH"


def test_low_confidence_edge_becomes_exception() -> None:
    config = PipelineConfig(auto_match_threshold=0.95, exception_threshold=0.70)
    rzp = _make_rzp("rzp_1")
    bank = _make_bank("bank_1")

    scored = [(rzp, bank, 0.3)]
    assignments = global_assign(scored, config)

    assert len(assignments) == 1
    assert assignments[0]["label"] == "EXCEPTION"


def test_greedy_selects_highest_confidence() -> None:
    config = PipelineConfig(auto_match_threshold=0.95, exception_threshold=0.70)
    rzp1 = _make_rzp("rzp_1", utr="RZP001")
    rzp2 = _make_rzp("rzp_2", utr="RZP002")
    bank1 = _make_bank("bank_1", utr="RZP001")
    bank2 = _make_bank("bank_2", utr="RZP002")

    # Both edges have UTR match, but first is higher confidence
    scored = [
        (rzp1, bank1, 0.98),
        (rzp2, bank2, 0.96),
    ]
    assignments = global_assign(scored, config)

    assert len(assignments) == 2
    assert all(a["label"] == "AUTO_MATCH" for a in assignments)


def test_tied_confidence_edges_resolve_identically_regardless_of_input_order() -> None:
    """Code-review finding: two edges tied on confidence used to be
    resolved by input order alone (Python's `sorted` is stable), which
    made the winner depend on whatever order candidate generation happened
    to produce them in -- not a property that should matter to a
    deterministic financial matcher. Two rzp records compete for the same
    bank record at identical confidence; whichever wins must be the same
    no matter which order the candidate list arrives in."""
    config = PipelineConfig(auto_match_threshold=0.95, exception_threshold=0.70)
    rzp_a = _make_rzp("rzp_a", utr="RZP001")
    rzp_b = _make_rzp("rzp_b", utr="RZP001")
    bank = _make_bank("bank_1", utr="RZP001")

    forward = [(rzp_a, bank, 0.97), (rzp_b, bank, 0.97)]
    reversed_order = [(rzp_b, bank, 0.97), (rzp_a, bank, 0.97)]

    winner_forward = global_assign(forward, config)[0]["source_a_id"]
    winner_reversed = global_assign(reversed_order, config)[0]["source_a_id"]

    assert winner_forward == winner_reversed


def test_near_tie_competitor_blocks_auto_match() -> None:
    """The "no competing explanation" clause, made executable.

    This project's stated rule for automation has always been three-part:
    evidence above threshold, AND no unresolved competing explanation, AND
    invariants hold. The first and third were enforced; the middle one was
    documented everywhere and implemented nowhere. Found by
    `datagen/adversarial.py::near_tie_scores`: two bank credits competing
    for one payment at 0.96 and 0.95 were resolved by a coin flip and the
    winner stamped AUTO_MATCH, with the runner-up vanishing from the output
    entirely.

    A margin that thin is not evidence, it is a tie-break. Correct behavior
    is to hold the record for review rather than book it.
    """
    config = PipelineConfig(auto_match_threshold=0.95, exception_threshold=0.70)
    rzp = _make_rzp("rzp_1", utr="RZP001")
    bank_a = _make_bank("bank_a", utr="RZP001")
    bank_b = _make_bank("bank_b", utr="RZP001")

    scored = [(rzp, bank_a, 0.96), (rzp, bank_b, 0.95)]
    assignments = global_assign(scored, config)

    winner = assignments[0]
    assert winner["label"] == "LIKELY_MATCH", "a 0.01 margin must not be auto-booked"
    assert winner["competing_candidates"] == 1
    assert "competing" in winner["abstention_reason"].lower()


def test_clear_winner_still_auto_matches() -> None:
    """The complement: suppression must not fire when the runner-up is
    genuinely far behind, or the gate would simply destroy throughput."""
    config = PipelineConfig(auto_match_threshold=0.95, exception_threshold=0.70)
    rzp = _make_rzp("rzp_1", utr="RZP001")
    bank_a = _make_bank("bank_a", utr="RZP001")
    bank_b = _make_bank("bank_b", utr="RZP001")

    scored = [(rzp, bank_a, 0.99), (rzp, bank_b, 0.40)]
    assignments = global_assign(scored, config)

    winner = assignments[0]
    assert winner["label"] == "AUTO_MATCH"
    assert winner["competing_candidates"] == 0
    assert winner["abstention_reason"] == ""


def test_competition_is_scoped_to_one_leg() -> None:
    """A payment legitimately has both a bank counterpart and a merchant
    counterpart. Those are answers to different questions, not competing
    explanations for the same one, so a strong merchant match must never
    suppress a strong bank match."""
    config = PipelineConfig(auto_match_threshold=0.95, exception_threshold=0.70)
    rzp = _make_rzp("rzp_1", utr="RZP001", order_id="order_1")
    bank = _make_bank("bank_1", utr="RZP001")
    merch = NormalizedRecord(
        record_id="merch_1",
        source="merchant",
        source_record_id="led_1",
        record_type="sale",
        order_id="order_1",
        amount_paise=10000,
        net_amount_paise=10000,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        raw_record={},
        provenance={"source": "merchant"},
    )

    scored = [(rzp, bank, 0.98), (rzp, merch, 0.97)]
    assignments = global_assign(scored, config)

    assert len(assignments) == 2
    assert all(a["label"] == "AUTO_MATCH" for a in assignments)
    assert all(a["competing_candidates"] == 0 for a in assignments)


def test_classify_unmatched_identifies_orphans() -> None:
    config = PipelineConfig()
    rzp1 = _make_rzp("rzp_1", utr="RZP001")
    bank1 = _make_bank("bank_1", utr="RZP001")
    rzp2 = _make_rzp("rzp_2", utr="RZP002")

    scored = [(rzp1, bank1, 0.98)]
    assignments = global_assign(scored, config)

    unmatched = classify_unmatched(
        {rzp1.record_id, rzp2.record_id},
        {bank1.record_id},
        set(),
        assignments,
    )

    unmatched_rzp = [u for u in unmatched if u["source"] == "razorpay"]
    assert any(u["record_id"] == "rzp_2" for u in unmatched_rzp)
