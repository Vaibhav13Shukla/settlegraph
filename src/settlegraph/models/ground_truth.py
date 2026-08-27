from typing import Literal

from pydantic import BaseModel


class GroundTruthRecord(BaseModel):
    razorpay_record_id: str
    true_bank_record_ids: list[str]
    true_merchant_record_id: str | None = None
    relationship_type: Literal[
        "exact_match",
        "split",
        "merge",
        "refund_of",
        "adjustment_for",
        "no_counterpart",
        "timing_only",
    ]
    anomaly_type: str | None = None
    notes: str = ""
