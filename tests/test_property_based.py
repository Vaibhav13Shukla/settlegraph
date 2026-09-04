"""Property-based tests over the money- and date-sensitive functions.

Unit tests pin specific scenarios; these throw a much wider net of adversarial
inputs at the same functions to check invariants that must hold for *every*
input, not just the examples someone thought to write down. This is the
direct answer to "stress test it until it breaks" at the function level,
complementing scripts/stress_test.py's batch-level version.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from settlegraph.config import PipelineConfig
from settlegraph.engine.exceptions import _levenshtein
from settlegraph.engine.idempotency import IdempotencyShield
from settlegraph.engine.score import score_edge
from settlegraph.engine.verify import InvariantViolation, verify_amount_invariant
from settlegraph.models import NormalizedRecord

paise = st.integers(min_value=0, max_value=10_000_000_000)  # up to ₹100 crore
short_text = st.text(min_size=0, max_size=40)
utrs = st.text(alphabet=st.characters(min_codepoint=48, max_codepoint=122), min_size=1, max_size=20)


def _record(
    source: str, amount: int, net: int | None = None, utr: str | None = "RZP001"
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=f"{source}_1",
        source=source,  # type: ignore[arg-type]
        source_record_id=f"{source}_1",
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
        net_amount_paise=net if net is not None else amount,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        settlement_date=date(2026, 1, 15),
        description=None,
        raw_record={},
        provenance={"source": source, "direction": "credit"},
    )


# --- score_edge: confidence must always land in [0, 1], for any amount ---


@given(rzp_amount=paise, bank_amount=paise)
@settings(max_examples=200)
def test_score_edge_is_always_within_unit_interval(rzp_amount: int, bank_amount: int) -> None:
    rzp = _record("razorpay", amount=rzp_amount, utr="RZP001")
    bank = _record("bank", amount=bank_amount, utr="RZP001")
    score = score_edge(rzp, bank)
    assert 0.0 <= score <= 1.0


@given(amount=paise)
@settings(max_examples=100)
def test_score_edge_exact_match_at_any_scale_scores_high(amount: int) -> None:
    """An exact UTR + amount + date match should score confidently at every
    scale from ₹0 to ₹100 crore -- large amounts must not silently degrade
    confidence just by being large."""
    rzp = _record("razorpay", amount=amount, utr="RZP001")
    bank = _record("bank", amount=amount, utr="RZP001")
    assert score_edge(rzp, bank) >= 0.90


# --- verify_amount_invariant: must never silently accept a real mismatch,
# and must never reject a genuine exact match, at any scale ---


@given(net=paise)
@settings(max_examples=200)
def test_amount_invariant_never_rejects_an_exact_match(net: int) -> None:
    rzp = _record("razorpay", amount=net, net=net)
    bank = _record("bank", amount=net, net=net)
    assert verify_amount_invariant(rzp, bank) is True


@given(
    net=st.integers(min_value=1000, max_value=10_000_000_000),
    gap=st.integers(min_value=101, max_value=10_000),
)
@settings(max_examples=200)
def test_amount_invariant_always_rejects_a_gap_over_tolerance(net: int, gap: int) -> None:
    """A gap strictly greater than the fixed 1-rupee tolerance must always
    raise, at any transaction scale -- this is the fixed-tolerance sibling
    of the relative-tolerance check `test_score_edge_...` above targets."""
    rzp = _record("razorpay", amount=net, net=net)
    bank = _record("bank", amount=max(0, net - gap), net=max(0, net - gap))
    try:
        verify_amount_invariant(rzp, bank)
        raised = False
    except InvariantViolation:
        raised = True
    assert raised


# --- IdempotencyShield: fingerprinting must be a pure function of the
# canonical payload, and must never collide two different transactions ---


@given(amount=paise, source_id=st.text(min_size=1, max_size=20))
@settings(max_examples=100)
def test_fingerprint_is_deterministic(amount: int, source_id: str) -> None:
    r = _record("razorpay", amount=amount)
    r = r.model_copy(update={"source_record_id": source_id})
    assert IdempotencyShield.compute_fingerprint(r) == IdempotencyShield.compute_fingerprint(r)


@given(a=paise, b=paise)
@settings(max_examples=200)
def test_different_amounts_never_share_a_fingerprint(a: int, b: int) -> None:
    if a == b:
        return
    r1 = _record("razorpay", amount=a).model_copy(update={"source_record_id": "same_id"})
    r2 = _record("razorpay", amount=b).model_copy(update={"source_record_id": "same_id"})
    assert IdempotencyShield.compute_fingerprint(r1) != IdempotencyShield.compute_fingerprint(r2)


# --- _levenshtein: the primitive the widened UTR-corruption check relies on ---


@given(s=utrs)
@settings(max_examples=100)
def test_levenshtein_distance_to_self_is_zero(s: str) -> None:
    assert _levenshtein(s, s) == 0


@given(s=utrs, t=utrs)
@settings(max_examples=200)
def test_levenshtein_is_symmetric_and_non_negative(s: str, t: str) -> None:
    d1 = _levenshtein(s, t)
    d2 = _levenshtein(t, s)
    assert d1 == d2
    assert d1 >= 0
    assert d1 <= max(len(s), len(t))


# --- date tolerance boundary: settlement windows around year boundaries,
# leap days, and other calendar edge cases must not silently misbehave ---


@given(
    base=st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)),
    delta_days=st.integers(min_value=-30, max_value=30),
)
@settings(max_examples=200)
def test_date_invariant_boundary_is_exact_at_every_calendar_date(
    base: date, delta_days: int
) -> None:
    """Exercises leap days, year boundaries, and month-length edge cases --
    the date-tolerance check must be exactly as strict at every calendar
    date, not just the ones a hand-written test happened to pick."""
    from settlegraph.engine.verify import verify_date_invariant

    config = PipelineConfig(date_tolerance_days=3)
    rzp = _record("razorpay", amount=10000)
    rzp = rzp.model_copy(update={"settlement_date": base})
    bank = _record("bank", amount=10000)
    bank = bank.model_copy(update={"transaction_date": base + timedelta(days=delta_days)})

    if abs(delta_days) <= 3:
        assert verify_date_invariant(rzp, bank, config) is True
    else:
        try:
            verify_date_invariant(rzp, bank, config)
            raised = False
        except InvariantViolation:
            raised = True
        assert raised
