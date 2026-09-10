"""Tests for the candidate graph builder."""

from __future__ import annotations

from datetime import date

from settlegraph.config import PipelineConfig
from settlegraph.engine.match import build_candidate_graph
from settlegraph.models import NormalizedRecord


def _make_rzp(
    record_id: str,
    utr: str | None = None,
    order_id: str | None = None,
    payment_id: str | None = None,
    amount: int = 10000,
    net: int = 9764,
    settlement_date: date | None = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        source="razorpay",
        source_record_id=record_id,
        record_type="payment",
        payment_id=payment_id,
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
        settlement_date=settlement_date or date(2026, 1, 17),
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


def _make_merchant(
    record_id: str,
    order_id: str | None = None,
    payment_id: str | None = None,
    amount: int = 10000,
) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=record_id,
        source="merchant",
        source_record_id=record_id,
        record_type="sale",
        payment_id=payment_id,
        order_id=order_id,
        settlement_id=None,
        utr=None,
        invoice_number=None,
        reference_text=None,
        amount_paise=amount,
        fee_paise=None,
        tax_paise=None,
        net_amount_paise=amount,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        settlement_date=None,
        description=None,
        raw_record={},
        provenance={"source": "merchant"},
    )


def test_utr_match_creates_candidate() -> None:
    config = PipelineConfig()
    rzp = [_make_rzp("rzp_1", utr="RZP001", order_id="order_1", payment_id="pay_1")]
    bank = [_make_bank("bank_1", utr="RZP001")]
    merch = [_make_merchant("merch_1", order_id="order_1", payment_id="pay_1")]

    candidates = build_candidate_graph(rzp, bank, merch, config)

    # UTR match + order_id match + payment_id match should all create candidates
    assert len(candidates) >= 1
    # At least one candidate should be (rzp, bank) via UTR
    rzp_bank_pairs = [
        (a, b) for a, b in candidates if a.source == "razorpay" and b.source == "bank"
    ]
    assert len(rzp_bank_pairs) >= 1


def test_utr_collision_keeps_every_competing_razorpay_record() -> None:
    """A recycled/reused bank reference number must not silently erase a
    payment from the candidate graph.

    Found by the adversarial corpus (`datagen/adversarial.py::
    high_confidence_wrong_match`): `build_candidate_graph` used to index
    Razorpay records into a plain `dict[utr] -> record`, so when two payments
    shared a UTR the second **overwrote** the first before scoring ever ran.
    The true counterpart never became a candidate at all, and the bank credit
    was then confidently AUTO_MATCHed to the wrong payment at 0.95 (UTR +
    exact amount + same-day date) with nothing anywhere in the output
    signalling that a collision had happened.

    That is the single most dangerous failure this system can produce: a
    confident, invariant-passing, factually wrong ledger entry. The fix is
    not to pick a better winner -- it is to stop discarding the evidence that
    a competition existed, so the ambiguity is visible to assignment (which
    then abstains, see test_assign.py) instead of being resolved by dict
    insertion order.
    """
    config = PipelineConfig()
    rzp_true = _make_rzp("rzp_true", utr="RZP999")
    rzp_impostor = _make_rzp("rzp_impostor", utr="RZP999")
    bank = [_make_bank("bank_1", utr="RZP999")]

    candidates = build_candidate_graph([rzp_true, rzp_impostor], bank, [], config)

    rzp_ids = {a.record_id for a, b in candidates if a.source == "razorpay"}
    assert rzp_ids == {"rzp_true", "rzp_impostor"}, (
        "both payments sharing the UTR must reach the scorer; dropping one "
        "hides the collision and produces a confident wrong match"
    )


def test_order_id_match_creates_candidate() -> None:
    config = PipelineConfig()
    rzp = [_make_rzp("rzp_1", order_id="order_1", payment_id="pay_1")]
    bank = [_make_bank("bank_1")]  # No UTR
    merch = [_make_merchant("merch_1", order_id="order_1", payment_id="pay_1")]

    candidates = build_candidate_graph(rzp, bank, merch, config)

    # Should have at least one (rzp, merch) pair via order_id
    rzp_merch_pairs = [
        (a, b) for a, b in candidates if a.source == "razorpay" and b.source == "merchant"
    ]
    assert len(rzp_merch_pairs) >= 1


def test_payment_id_match_creates_candidate() -> None:
    config = PipelineConfig()
    rzp = [_make_rzp("rzp_1", payment_id="pay_1")]
    merch = [_make_merchant("merch_1", payment_id="pay_1")]

    candidates = build_candidate_graph(rzp, [], merch, config)

    rzp_merch_pairs = [
        (a, b) for a, b in candidates if a.source == "razorpay" and b.source == "merchant"
    ]
    assert len(rzp_merch_pairs) >= 1


def test_no_candidates_when_no_overlap() -> None:
    config = PipelineConfig()
    rzp = [_make_rzp("rzp_1", order_id="order_1")]
    bank = [_make_bank("bank_1", utr="DIFFERENT_UTR")]
    merch = [_make_merchant("merch_1", order_id="DIFFERENT_ORDER")]

    candidates = build_candidate_graph(rzp, bank, merch, config)

    # Should have zero candidates (no UTR match, no order_id match, no payment_id match)
    assert len(candidates) == 0


def test_candidate_graph_never_links_across_merchants() -> None:
    """Cross-merchant isolation (ADR 0010): two records sharing a UTR, an
    exact amount, and a settlement date -- the strongest possible match
    signal -- must still never become a candidate when they belong to
    different merchants. Booking merchant A's settlement against merchant B's
    bank account is a cross-tenant integrity failure that no downstream
    amount or date invariant would catch, so the boundary is enforced at
    candidate-generation time, before scoring ever runs.
    """
    config = PipelineConfig()
    # Identical UTR/amount/date across two DIFFERENT merchants (a recycled or
    # coincidentally-colliding bank reference number).
    rzp_a = _make_rzp("rzp_a", utr="SAMEUTR0000000001").model_copy(
        update={"merchant_id": "merch_apollo"}
    )
    bank_b = _make_bank("bank_b", utr="SAMEUTR0000000001").model_copy(
        update={"merchant_id": "merch_zomato"}
    )
    # A legitimate same-merchant pair, as a control that matching still works.
    rzp_a2 = _make_rzp("rzp_a2", utr="OWNUTR00000000002").model_copy(
        update={"merchant_id": "merch_apollo"}
    )
    bank_a2 = _make_bank("bank_a2", utr="OWNUTR00000000002").model_copy(
        update={"merchant_id": "merch_apollo"}
    )

    candidates = build_candidate_graph([rzp_a, rzp_a2], [bank_b, bank_a2], [], config)
    pairs = {(a.record_id, b.record_id) for a, b in candidates}

    # The cross-merchant pair is absent despite an identical UTR.
    assert ("rzp_a", "bank_b") not in pairs
    assert ("bank_b", "rzp_a") not in pairs
    # The same-merchant pair is still proposed.
    assert ("rzp_a2", "bank_a2") in pairs or ("bank_a2", "rzp_a2") in pairs
    # Nothing in the returned candidate set crosses a merchant boundary.
    for a, b in candidates:
        assert a.merchant_id == b.merchant_id
