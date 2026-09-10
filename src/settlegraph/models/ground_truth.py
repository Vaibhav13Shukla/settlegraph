from typing import Literal

from pydantic import BaseModel


class GroundTruthRecord(BaseModel):
    razorpay_record_id: str
    merchant_id: str = "merch_unknown"
    true_bank_record_ids: list[str]
    true_merchant_record_id: str | None = None
    # Only relationship types the generator actually produces and the
    # evaluator actually consumes. `merge` (N:1 aggregation), `adjustment_for`
    # and `timing_only` were previously listed here but never generated,
    # matched, evaluated, or present in any committed ground-truth data --
    # vocabulary claiming capabilities the system does not have (expert
    # feedback C singled out `merge` as an N:1 capability that does not exist).
    # Removed rather than left as aspirational enum values. Adding real N:1
    # aggregation is a deliberate change to the reconciliation unit (see
    # ADR 0012 / the audit), not a matter of restoring a Literal member.
    relationship_type: Literal[
        "exact_match",
        "split",
        "refund_of",
        "no_counterpart",
    ]
    anomaly_type: str | None = None
    notes: str = ""
