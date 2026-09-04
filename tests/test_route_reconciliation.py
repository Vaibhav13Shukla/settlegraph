"""Tests for Route marketplace payout reconciliation."""

from __future__ import annotations

from datetime import datetime

from settlegraph.engine.route_reconciliation import reconcile_route_splits, summarize
from settlegraph.models import RazorpaySettlementRecord, RoutePayoutRecord


def _rzp(entity_id: str = "pay_1", amount: int = 100000) -> RazorpaySettlementRecord:
    return RazorpaySettlementRecord(
        entity_id=entity_id,
        entity_type="payment",
        settlement_id="setl_1",
        amount_paise=amount,
        fee_paise=2000,
        tax_paise=360,
        net_amount_paise=amount - 2360,
        captured_at=datetime(2026, 1, 1),
        settled_at=datetime(2026, 1, 3),
        status="captured",
    )


def _payout(
    source: str = "pay_1", amount: int = 49000, fee: int = 1000, i: int = 0
) -> RoutePayoutRecord:
    return RoutePayoutRecord(
        transfer_id=f"trf_{source}_{i}",
        source_payment_id=source,
        linked_account_id=f"acc_{i:04d}",
        amount_paise=amount,
        route_fee_paise=fee,
        processed_at=datetime(2026, 1, 3),
        status="processed",
    )


def test_a_payment_with_no_route_legs_is_excluded_not_flagged() -> None:
    """An ordinary (non-marketplace) payment shouldn't show up as a
    mismatch just because it has zero payout legs."""
    results = reconcile_route_splits([_rzp()], [])
    assert results == []


def test_split_that_sums_correctly_is_verified() -> None:
    # amount=100000, fee_paise per leg 1000*2=2000 total route fee,
    # distributable = 98000, split into two 49000 legs
    results = reconcile_route_splits(
        [_rzp(amount=100000)],
        [_payout(amount=49000, fee=1000, i=0), _payout(amount=49000, fee=1000, i=1)],
    )
    assert results[0].status == "SPLIT_VERIFIED"


def test_shortfall_from_a_missing_leg_is_caught() -> None:
    """Only one of two expected legs present -- money that should have
    reached a vendor is unaccounted for."""
    results = reconcile_route_splits([_rzp(amount=100000)], [_payout(amount=49000, fee=1000, i=0)])
    assert results[0].status == "PAYOUT_SHORTFALL"
    assert results[0].diff_paise < 0


def test_overpayment_is_caught_not_just_shortfall() -> None:
    results = reconcile_route_splits(
        [_rzp(amount=100000)],
        [_payout(amount=60000, fee=1000, i=0), _payout(amount=49000, fee=1000, i=1)],
    )
    assert results[0].status == "PAYOUT_OVERPAYMENT"
    assert results[0].diff_paise > 0


def test_a_reversed_leg_is_not_counted_as_actually_distributed() -> None:
    """Code-review finding: the first version summed every leg's amount
    regardless of status, so a leg that bounced back after payout
    ("reversed" -- money never really reached the vendor) still counted
    toward `actual_distributed_paise` and could report SPLIT_VERIFIED on a
    payout that actually failed. RoutePayoutRecord.status is
    "processed" | "reversed" | "pending" for exactly this reason."""
    rzp = _rzp(amount=100000)
    processed = _payout(amount=49000, fee=1000, i=0)
    reversed_leg = _payout(amount=49000, fee=1000, i=1).model_copy(update={"status": "reversed"})

    results = reconcile_route_splits([rzp], [processed, reversed_leg])

    assert results[0].status == "PAYOUT_SHORTFALL"
    assert results[0].actual_distributed_paise == 49000  # only the processed leg


def test_a_pending_leg_is_not_counted_as_actually_distributed() -> None:
    rzp = _rzp(amount=100000)
    processed = _payout(amount=49000, fee=1000, i=0)
    pending = _payout(amount=49000, fee=1000, i=1).model_copy(update={"status": "pending"})

    results = reconcile_route_splits([rzp], [processed, pending])

    assert results[0].status == "PAYOUT_SHORTFALL"
    assert "pending" in results[0].detail


def test_tolerance_is_configurable_not_a_fixed_module_constant() -> None:
    """Code-review finding: the tolerance used to be a private hardcoded
    constant that PipelineConfig.amount_tolerance_paise had no way to
    reach. A diff just outside the default 100-paise tolerance must clear
    as verified when a wider tolerance is explicitly passed."""
    rzp = _rzp(amount=100000)
    legs = [
        _payout(amount=49000, fee=1000, i=0),
        _payout(amount=48850, fee=1000, i=1),
    ]  # 150 paise short

    default_result = reconcile_route_splits([rzp], legs)
    assert default_result[0].status == "PAYOUT_SHORTFALL"

    widened_result = reconcile_route_splits([rzp], legs, tolerance_paise=200)
    assert widened_result[0].status == "SPLIT_VERIFIED"


def test_all_legs_processed_and_summing_correctly_still_verifies() -> None:
    """The status check must not break the ordinary, fully-processed case."""
    rzp = _rzp(amount=100000)
    legs = [_payout(amount=49000, fee=1000, i=0), _payout(amount=49000, fee=1000, i=1)]
    results = reconcile_route_splits([rzp], legs)
    assert results[0].status == "SPLIT_VERIFIED"


def test_summarize_separates_shortfall_from_overpayment_exposure() -> None:
    results = reconcile_route_splits(
        [_rzp(entity_id="a", amount=100000), _rzp(entity_id="b", amount=100000)],
        [
            _payout(source="a", amount=49000, fee=1000, i=0),  # a: shortfall (missing 2nd leg)
            _payout(source="b", amount=49000, fee=1000, i=0),
            _payout(source="b", amount=49000, fee=1000, i=1),  # b: verified
        ],
    )
    summary = summarize(results)
    assert summary["total_marketplace_payments"] == 2
    assert summary["payout_shortfalls"] == 1
    assert summary["split_verified"] == 1
