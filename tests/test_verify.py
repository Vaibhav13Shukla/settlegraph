"""Tests for invariant verification."""

from __future__ import annotations

from datetime import date

import pytest

from settlegraph.config import PipelineConfig
from settlegraph.engine.score import score_edge
from settlegraph.engine.verify import (
    InvariantViolation,
    verify_amount_invariant,
    verify_date_invariant,
    verify_direction_invariant,
    verify_settlegraph_invariants,
)
from settlegraph.models import NormalizedRecord


def _make_rzp(
    net: int = 9764,
    settlement_date: date | None = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id="rzp_1",
        source="razorpay",
        source_record_id="rzp_1",
        record_type="payment",
        payment_id=None,
        order_id=None,
        settlement_id="setl_1",
        utr="RZP001",
        invoice_number=None,
        reference_text=None,
        amount_paise=10000,
        fee_paise=200,
        tax_paise=36,
        net_amount_paise=net,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        settlement_date=settlement_date or date(2026, 1, 17),
        description=None,
        raw_record={},
        provenance={"source": "razorpay"},
    )


def _make_bank(
    amount: int = 9764,
    transaction_date: date | None = None,
    direction: str | None = None,
) -> NormalizedRecord:
    provenance: dict[str, str] = {"source": "bank"}
    if direction is not None:
        provenance["direction"] = direction
    return NormalizedRecord(
        record_id="bank_1",
        source="bank",
        source_record_id="bank_1",
        record_type="settlement_credit" if direction != "debit" else "adjustment",
        payment_id=None,
        order_id=None,
        settlement_id=None,
        utr="RZP001",
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
        provenance=provenance,
    )


def test_amount_invariant_passes_on_exact_match() -> None:
    rzp = _make_rzp(net=9764)
    bank = _make_bank(amount=9764)

    result = verify_amount_invariant(rzp, bank)
    assert result is True


def test_amount_invariant_raises_on_large_mismatch() -> None:
    rzp = _make_rzp(net=9764)
    bank = _make_bank(amount=9000)

    with pytest.raises(InvariantViolation, match="Amount mismatch"):
        verify_amount_invariant(rzp, bank)


def test_date_invariant_passes_on_same_day() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(settlement_date=date(2026, 1, 17))
    bank = _make_bank(transaction_date=date(2026, 1, 17))

    result = verify_date_invariant(rzp, bank, config)
    assert result is True


def test_date_invariant_raises_on_excessive_gap() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(settlement_date=date(2026, 1, 17))
    bank = _make_bank(transaction_date=date(2026, 1, 25))

    with pytest.raises(InvariantViolation, match="Date violation"):
        verify_date_invariant(rzp, bank, config)


def test_verify_settlegraph_invariants_returns_violations() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(net=9764)
    bank = _make_bank(amount=9000)  # Amount mismatch

    violations = verify_settlegraph_invariants(rzp, bank, config)

    assert len(violations) >= 1
    assert isinstance(violations[0], InvariantViolation)


def test_direction_invariant_passes_on_credit() -> None:
    bank = _make_bank(direction="credit")
    assert verify_direction_invariant(bank) is True


def test_direction_invariant_passes_when_direction_undeclared() -> None:
    """Undeclared provenance is a deliberate silent pass, not a failure --

    the same default RefundGuard's evidence layer uses so an unwired
    integration is never blocked. Strict mode (require direction on every
    bank record) is a follow-up, not implemented here.
    """
    bank = _make_bank(direction=None)
    assert verify_direction_invariant(bank) is True


def test_direction_invariant_raises_on_debit() -> None:
    """A debit/adjustment row must never satisfy a settlement-credit match.

    build_candidate_graph does not discriminate on direction when proposing
    links, so this is the last line of defense against a refund payout (or
    any other debit that shares a UTR or amount+date window) being accepted
    as if it were the settlement credit for a Razorpay payment.
    """
    bank = _make_bank(direction="debit")

    with pytest.raises(InvariantViolation, match="Direction violation"):
        verify_direction_invariant(bank)


def test_verify_settlegraph_invariants_flags_debit_matched_as_settlement() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(net=9764, settlement_date=date(2026, 1, 17))
    bank = _make_bank(amount=9764, transaction_date=date(2026, 1, 17), direction="debit")

    violations = verify_settlegraph_invariants(rzp, bank, config)

    assert any("Direction violation" in str(v) for v in violations)


def test_verify_settlegraph_invariants_empty_on_valid() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(net=9764, settlement_date=date(2026, 1, 17))
    bank = _make_bank(amount=9764, transaction_date=date(2026, 1, 17))

    violations = verify_settlegraph_invariants(rzp, bank, config)

    assert len(violations) == 0


# ---------------------------------------------------------------------------
# Boundary and high-confidence coverage.
#
# The tests above all exercise *obvious* violations -- a 764-paise amount gap,
# an 8-day date gap. An invariant that only rejects the obvious case is not
# much of a control: the dangerous input is the one sitting exactly on the
# boundary, or the one arriving with a high match score that makes it look
# safe. Every invariant here is therefore covered three ways: obvious
# (above), subtle boundary (below), and a violation that would otherwise be
# auto-booked on confidence alone (below).
# ---------------------------------------------------------------------------


def test_amount_invariant_accepts_exactly_at_the_tolerance_boundary() -> None:
    """`verify_amount_invariant` rejects on `diff > 100` paise, so a diff of
    exactly 100 paise (Rs 1.00) must PASS. Pinning the boundary explicitly:
    a later refactor flipping this to `>=` would silently narrow tolerance
    for every settlement in production, and no aggregate metric would
    notice."""
    rzp = _make_rzp(net=9764)
    bank = _make_bank(amount=9664)  # exactly 100 paise below

    assert verify_amount_invariant(rzp, bank) is True


def test_amount_invariant_rejects_one_paise_past_the_boundary() -> None:
    """101 paise -- one paise past tolerance. The smallest possible real
    violation, and the one a fixed-tolerance check is most likely to get
    wrong."""
    rzp = _make_rzp(net=9764)
    bank = _make_bank(amount=9663)  # 101 paise below

    with pytest.raises(InvariantViolation, match="Amount mismatch"):
        verify_amount_invariant(rzp, bank)


def test_date_invariant_accepts_exactly_at_the_tolerance_boundary() -> None:
    """delta == date_tolerance_days must pass (`delta > tolerance` rejects)."""
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(settlement_date=date(2026, 1, 17))
    bank = _make_bank(transaction_date=date(2026, 1, 20))  # exactly 3 days

    assert verify_date_invariant(rzp, bank, config) is True


def test_date_invariant_rejects_one_day_past_the_boundary() -> None:
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(settlement_date=date(2026, 1, 17))
    bank = _make_bank(transaction_date=date(2026, 1, 21))  # 4 days

    with pytest.raises(InvariantViolation, match="Date violation"):
        verify_date_invariant(rzp, bank, config)


def test_date_invariant_is_symmetric_around_the_boundary() -> None:
    """A bank credit dated *before* the settlement (clock skew, backdated
    statement) is the same magnitude of violation as one dated after --
    `abs()` is load-bearing here, so assert it rather than trusting it."""
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(settlement_date=date(2026, 1, 17))
    bank_before = _make_bank(transaction_date=date(2026, 1, 13))  # 4 days early

    with pytest.raises(InvariantViolation, match="Date violation"):
        verify_date_invariant(rzp, bank_before, config)


def test_perfect_score_inputs_still_fail_the_direction_invariant() -> None:
    """The third way, and the one that matters most.

    This pair is *perfect* on every signal the scorer looks at: identical
    UTR, identical amount, identical date. `score_edge` gives it a top-of-
    range razorpay<->bank score, comfortably above the 0.95 auto-match
    threshold -- scoring never inspects ledger direction at all. The only
    thing standing between this debit line (a refund payout, say) and a
    corrupted ledger entry is the direction invariant, so it has to hold
    precisely here, where confidence is highest and least deserved.

    See `pipeline.enforce_invariant_gate` for the demotion this triggers.
    """
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(net=9764, settlement_date=date(2026, 1, 17))
    bank = _make_bank(amount=9764, transaction_date=date(2026, 1, 17), direction="debit")

    # Confirm the premise rather than asserting it: this really would score
    # at the top of the range and be auto-booked on confidence alone.
    assert score_edge(rzp, bank) >= config.auto_match_threshold

    violations = verify_settlegraph_invariants(rzp, bank, config)

    assert any("Direction violation" in str(v) for v in violations)


def test_non_payment_razorpay_record_cannot_satisfy_a_settlement_credit() -> None:
    """A Razorpay `adjustment` or `refund` row is not a payment, and must not
    be booked against a bank settlement credit as though it were one.

    Found by `datagen/adversarial.py::new_transaction_category`. `score_edge`
    never inspects `record_type`, so an adjustment with a matching UTR,
    exact amount and same-day date scores 0.95 -- byte-identical to the
    ordinary payment happy path -- and was confidently AUTO_MATCHed. That is
    a confident wrong booking on an unfamiliar transaction category, which is
    precisely the failure this project exists to prevent.

    It was invisible to the whole test suite because `datagen/generator.py`
    only ever emits `entity_type="payment"`, so no generated batch could
    reach it. Real Razorpay settlement feeds contain refunds, transfers and
    adjustments, so the gap is reachable on real data and not on ours -- the
    worst combination.

    Direction is a separate invariant: `verify_direction_invariant` checks
    the *bank* side's credit/debit provenance. This checks the *Razorpay*
    side's transaction category. Both are needed.
    """
    config = PipelineConfig(date_tolerance_days=3)
    bank = _make_bank(amount=9764, transaction_date=date(2026, 1, 17))

    for bad_type in ("refund", "adjustment"):
        rzp = _make_rzp(net=9764, settlement_date=date(2026, 1, 17))
        rzp = rzp.model_copy(update={"record_type": bad_type})

        # Premise: it still scores at the top of the range.
        assert score_edge(rzp, bank) >= config.auto_match_threshold

        violations = verify_settlegraph_invariants(rzp, bank, config)
        assert any("Record type" in str(v) for v in violations), (
            f"a {bad_type} record must not satisfy a settlement-credit match"
        )


def test_ordinary_payment_still_passes_the_record_type_invariant() -> None:
    """The complement: the gate must not reject the normal case, or it would
    simply block every match in the batch."""
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(net=9764, settlement_date=date(2026, 1, 17))
    bank = _make_bank(amount=9764, transaction_date=date(2026, 1, 17))

    assert rzp.record_type == "payment"
    assert verify_settlegraph_invariants(rzp, bank, config) == []


def test_perfect_utr_and_date_do_not_excuse_an_amount_violation() -> None:
    """Same shape, amount invariant: a matching UTR is the single
    highest-weighted signal in the scorer (0.60), so a record can carry
    strong confidence from UTR + date alone while the money does not
    reconcile. Evidence on one axis must not buy a pass on another."""
    config = PipelineConfig(date_tolerance_days=3)
    rzp = _make_rzp(net=9764, settlement_date=date(2026, 1, 17))
    bank = _make_bank(amount=5000, transaction_date=date(2026, 1, 17))  # UTR + date perfect

    violations = verify_settlegraph_invariants(rzp, bank, config)

    assert any("Amount mismatch" in str(v) for v in violations)
