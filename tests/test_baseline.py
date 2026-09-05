"""Tests for the baseline matchers used in the evaluation comparison.

`run_naive_baseline` (Baseline A) already exists and is not touched here.
This file covers the two additional baselines required by the Buildathon
spec to demonstrate the precision/recall safety tradeoff:

- Baseline B: exact amount + date window (`run_amount_date_baseline`)
- Baseline C: fuzzy UTR + loose amount tolerance (`run_fuzzy_baseline`)

Both are deliberately less safe than the real engine, and several tests
below assert that danger explicitly (a false match really does happen)
rather than merely checking the happy path.
"""

from __future__ import annotations

from datetime import date

from settlegraph.engine.baseline import (
    run_amount_date_baseline,
    run_fuzzy_baseline,
    run_naive_baseline,
)
from settlegraph.models import NormalizedRecord


def _make_rzp(
    record_id: str,
    utr: str | None = None,
    order_id: str | None = None,
    amount: int = 10000,
    net: int | None = 9764,
    transaction_date: date | None = None,
    settlement_date: date | None = None,
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
        transaction_date=transaction_date or date(2026, 1, 15),
        settlement_date=settlement_date if settlement_date is not None else date(2026, 1, 17),
        description=None,
        raw_record={},
        provenance={"source": "razorpay"},
    )


def _make_bank(
    record_id: str,
    utr: str | None = None,
    amount: int = 9764,
    transaction_date: date | None = None,
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
        transaction_date=transaction_date or date(2026, 1, 17),
        settlement_date=transaction_date or date(2026, 1, 17),
        description=None,
        raw_record={},
        provenance={"source": "bank"},
    )


NAIVE_ASSIGNMENT_KEYS = {
    "source_a",
    "source_a_id",
    "source_b",
    "source_b_id",
    "confidence",
    "label",
    "a_amount_paise",
    "b_amount_paise",
    "a_utr",
    "b_utr",
    "a_order_id",
    "b_order_id",
}


# --- Baseline B: exact amount + date window -------------------------------


def test_amount_date_baseline_matches_within_date_tolerance() -> None:
    rzp = _make_rzp("rzp_1", net=5000, settlement_date=date(2026, 2, 1))
    bank = _make_bank("bank_1", amount=5000, transaction_date=date(2026, 2, 3))

    assignments = run_amount_date_baseline([rzp], [bank], [], date_tolerance_days=3)

    assert len(assignments) == 1
    a = assignments[0]
    assert a["source_a"] == "razorpay"
    assert a["source_a_id"] == "rzp_1"
    assert a["source_b"] == "bank"
    assert a["source_b_id"] == "bank_1"
    assert a["label"] == "AUTO_MATCH"


def test_amount_date_baseline_does_not_match_outside_date_tolerance() -> None:
    rzp = _make_rzp("rzp_1", net=5000, settlement_date=date(2026, 2, 1))
    bank = _make_bank("bank_1", amount=5000, transaction_date=date(2026, 2, 10))

    assignments = run_amount_date_baseline([rzp], [bank], [], date_tolerance_days=3)

    assert assignments == []


def test_amount_date_baseline_enforces_one_to_one_exclusivity() -> None:
    """Two payments share the exact same amount and date. Only one bank
    record exists to satisfy them, so only one of the two razorpay records
    may walk away with a match -- the bank record cannot be consumed twice."""
    rzp1 = _make_rzp("rzp_1", net=5000, settlement_date=date(2026, 2, 1))
    rzp2 = _make_rzp("rzp_2", net=5000, settlement_date=date(2026, 2, 1))
    bank = _make_bank("bank_1", amount=5000, transaction_date=date(2026, 2, 1))

    assignments = run_amount_date_baseline([rzp1, rzp2], [bank], [], date_tolerance_days=3)

    assert len(assignments) == 1
    assert len({a["source_b_id"] for a in assignments}) == 1


def test_amount_date_baseline_produces_a_false_match_when_ambiguous() -> None:
    """This is the demonstration the baseline exists for: it has no UTR to
    disambiguate, so when two payments share an amount and a date it can
    confidently wire a payment to the WRONG bank credit. Both razorpay
    records here carry a UTR (ignored by this baseline) that reveals which
    bank record is actually theirs -- rzp_1's true counterpart is bank_2,
    and rzp_2's true counterpart is bank_1. Both candidates share the same
    amount and date, so `run_amount_date_baseline` takes the first
    available bank record for rzp_1, which is bank_1 -- the wrong one."""
    rzp1 = _make_rzp("rzp_1", utr="TRUE_FOR_BANK_2", net=7000, settlement_date=date(2026, 3, 5))
    rzp2 = _make_rzp("rzp_2", utr="TRUE_FOR_BANK_1", net=7000, settlement_date=date(2026, 3, 5))
    bank1 = _make_bank(
        "bank_1", utr="TRUE_FOR_BANK_1", amount=7000, transaction_date=date(2026, 3, 5)
    )
    bank2 = _make_bank(
        "bank_2", utr="TRUE_FOR_BANK_2", amount=7000, transaction_date=date(2026, 3, 5)
    )

    assignments = run_amount_date_baseline([rzp1, rzp2], [bank1, bank2], [], date_tolerance_days=3)

    rzp1_match = next(a for a in assignments if a["source_a_id"] == "rzp_1")
    # The correct counterpart for rzp_1 is bank_2 (matching UTR), but this
    # baseline has no way to know that and wires it to bank_1 instead.
    assert rzp1_match["source_b_id"] == "bank_1"
    assert rzp1_match["a_utr"] != rzp1_match["b_utr"]
    assert rzp1_match["label"] == "AUTO_MATCH"


# --- Baseline C: fuzzy UTR + loose amount tolerance ------------------------


def test_fuzzy_baseline_matches_a_corrupted_utr_that_exact_match_would_miss() -> None:
    rzp = _make_rzp("rzp_1", utr="RZP000001", net=9764)
    bank = _make_bank("bank_1", utr="RZP00000X", amount=9764)

    naive_assignments = run_naive_baseline([rzp], [bank], [])
    fuzzy_assignments = run_fuzzy_baseline([rzp], [bank], [], similarity_threshold=0.8)

    assert naive_assignments == []
    assert len(fuzzy_assignments) == 1
    a = fuzzy_assignments[0]
    assert a["source_a_id"] == "rzp_1"
    assert a["source_b_id"] == "bank_1"
    assert a["label"] == "AUTO_MATCH"


def test_fuzzy_baseline_enforces_one_to_one_exclusivity() -> None:
    rzp1 = _make_rzp("rzp_1", utr="RZP000001", net=9764)
    rzp2 = _make_rzp("rzp_2", utr="RZP000002", net=9764)
    bank = _make_bank("bank_1", utr="RZP000001", amount=9764)

    assignments = run_fuzzy_baseline([rzp1, rzp2], [bank], [], similarity_threshold=0.8)

    assert len(assignments) == 1
    assert len({a["source_b_id"] for a in assignments}) == 1


# --- Schema parity with Baseline A -----------------------------------------


def test_amount_date_baseline_returns_same_key_set_as_naive_baseline() -> None:
    rzp = _make_rzp("rzp_1", utr="RZP001", net=9764)
    bank = _make_bank("bank_1", utr="RZP001", amount=9764)
    naive_keys = set(run_naive_baseline([rzp], [bank], [])[0].keys())

    b_rzp = _make_rzp("rzp_b", net=5000, settlement_date=date(2026, 2, 1))
    b_bank = _make_bank("bank_b", amount=5000, transaction_date=date(2026, 2, 1))
    assignments = run_amount_date_baseline([b_rzp], [b_bank], [])

    assert len(assignments) == 1
    assert set(assignments[0].keys()) == naive_keys == NAIVE_ASSIGNMENT_KEYS


def test_fuzzy_baseline_returns_same_key_set_as_naive_baseline() -> None:
    rzp = _make_rzp("rzp_1", utr="RZP001", net=9764)
    bank = _make_bank("bank_1", utr="RZP001", amount=9764)
    naive_keys = set(run_naive_baseline([rzp], [bank], [])[0].keys())

    c_rzp = _make_rzp("rzp_c", utr="RZP000001", net=9764)
    c_bank = _make_bank("bank_c", utr="RZP00000X", amount=9764)
    assignments = run_fuzzy_baseline([c_rzp], [c_bank], [])

    assert len(assignments) == 1
    assert set(assignments[0].keys()) == naive_keys == NAIVE_ASSIGNMENT_KEYS
