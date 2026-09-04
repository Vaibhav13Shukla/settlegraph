"""Route reconciliation: do a marketplace payment's vendor payout legs sum
to what should have been distributed.

Razorpay Route splits one collected payment across linked vendor accounts,
minus a Route fee. The failure mode here isn't "wrong bank match" -- it's a
payout leg that silently never reached a vendor, or a fee that doesn't add
up, which is a liability sitting on the marketplace's books, not a
settlement timing question. A satellite module for the same reason
`tax_matcher.py` is: a genuinely different reconciliation question, kept out
of the core candidate graph rather than blurred into it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from settlegraph.config import PipelineConfig
from settlegraph.models import RazorpaySettlementRecord, RoutePayoutRecord

ROUTE_TOLERANCE_PAISE = 100  # ~1 rupee, matching the rest of this engine's tolerance


class RouteSplitResult:
    def __init__(
        self,
        source_payment_id: str,
        status: str,
        expected_distributable_paise: int,
        actual_distributed_paise: int,
        diff_paise: int,
        leg_count: int,
        detail: str,
    ) -> None:
        self.source_payment_id = source_payment_id
        self.status = status
        self.expected_distributable_paise = expected_distributable_paise
        self.actual_distributed_paise = actual_distributed_paise
        self.diff_paise = diff_paise
        self.leg_count = leg_count
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_payment_id": self.source_payment_id,
            "status": self.status,
            "expected_distributable_paise": self.expected_distributable_paise,
            "actual_distributed_paise": self.actual_distributed_paise,
            "diff_paise": self.diff_paise,
            "leg_count": self.leg_count,
            "detail": self.detail,
        }


def reconcile_route_splits(
    razorpay: list[RazorpaySettlementRecord],
    payouts: list[RoutePayoutRecord],
    tolerance_paise: int | None = None,
) -> list[RouteSplitResult]:
    """One result per payment that has at least one Route payout leg.

    A payment with zero payout legs isn't in this list at all -- it was
    never a marketplace collection, and reporting "0 legs, mismatch" on
    every ordinary payment would drown the real findings in noise.

    `tolerance_paise` defaults to `ROUTE_TOLERANCE_PAISE` when not given --
    a private module constant here was a real DRY violation against
    `config.py`'s own stated rule ("financial thresholds are never magic
    numbers"): a deployment tuning `PipelineConfig.amount_tolerance_paise`
    had no way to make Route reconciliation follow along. Found by code
    review. `run_route_reconciliation` passes the real config through when
    it has one; direct callers and tests keep working with the default.
    """
    tolerance = tolerance_paise if tolerance_paise is not None else ROUTE_TOLERANCE_PAISE
    by_payment: dict[str, list[RoutePayoutRecord]] = {}
    for p in payouts:
        by_payment.setdefault(p.source_payment_id, []).append(p)

    results: list[RouteSplitResult] = []
    for record in razorpay:
        legs = by_payment.get(record.entity_id)
        if not legs:
            continue

        # Only a "processed" leg is money that actually reached a vendor.
        # RoutePayoutRecord.status can also be "reversed" (paid out, then
        # bounced back) or "pending" (not yet paid) -- summing every leg's
        # amount regardless of status, as the first version of this did,
        # would let a payment whose real payout failed report
        # SPLIT_VERIFIED just because a reversed leg's amount still added
        # up on paper. The route fee is charged at split time regardless of
        # what happens to the leg afterward, so it's counted across every
        # leg, not just processed ones.
        processed_legs = [leg for leg in legs if leg.status == "processed"]
        reversed_legs = [leg for leg in legs if leg.status == "reversed"]
        pending_legs = [leg for leg in legs if leg.status == "pending"]

        route_fee_total = sum(leg.route_fee_paise for leg in legs)
        expected_distributable = record.amount_paise - route_fee_total
        actual_distributed = sum(leg.amount_paise for leg in processed_legs)
        diff = actual_distributed - expected_distributable

        status_note = ""
        if reversed_legs:
            status_note += f" {len(reversed_legs)} leg(s) reversed after payout."
        if pending_legs:
            status_note += f" {len(pending_legs)} leg(s) still pending."

        if abs(diff) <= tolerance and not reversed_legs and not pending_legs:
            status = "SPLIT_VERIFIED"
            detail = ""
        elif diff < 0 or reversed_legs or pending_legs:
            status = "PAYOUT_SHORTFALL"
            detail = (
                f"₹{abs(diff) / 100:.2f} less was actually distributed to vendors than the "
                f"split should have paid out -- a leg may be missing, reversed, or still "
                f"pending.{status_note}"
            )
        else:
            status = "PAYOUT_OVERPAYMENT"
            detail = f"₹{diff / 100:.2f} more was distributed than the source payment supports.{status_note}"

        results.append(
            RouteSplitResult(
                record.entity_id,
                status,
                expected_distributable,
                actual_distributed,
                diff,
                len(legs),
                detail,
            )
        )
    return results


def summarize(results: list[RouteSplitResult]) -> dict[str, Any]:
    verified = sum(1 for r in results if r.status == "SPLIT_VERIFIED")
    shortfalls = [r for r in results if r.status == "PAYOUT_SHORTFALL"]
    overpayments = [r for r in results if r.status == "PAYOUT_OVERPAYMENT"]
    total = len(results)

    return {
        "total_marketplace_payments": total,
        "split_verified": verified,
        "verification_rate": round(verified / total, 4) if total else 0.0,
        "payout_shortfalls": len(shortfalls),
        "shortfall_exposure_inr": round(sum(abs(r.diff_paise) for r in shortfalls) / 100, 2),
        "payout_overpayments": len(overpayments),
        "overpayment_exposure_inr": round(sum(r.diff_paise for r in overpayments) / 100, 2),
    }


def run_route_reconciliation(
    data_dir: Path, output_dir: Path, config: PipelineConfig | None = None
) -> dict[str, Any]:
    from settlegraph.engine.ingest import load_razorpay, load_route_payouts

    razorpay = load_razorpay(data_dir / "razorpay_settlements.csv")
    payouts = load_route_payouts(data_dir / "route_payouts.csv")
    tolerance = config.amount_tolerance_paise if config is not None else None
    results = reconcile_route_splits(razorpay, payouts, tolerance_paise=tolerance)
    summary = summarize(results)

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "route_reconciliation.json").open("w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "results": [r.to_dict() for r in results]}, fh, indent=2)

    return summary
