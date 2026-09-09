from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class NormalizedRecord(BaseModel):
    record_id: str
    source: Literal["razorpay", "bank", "merchant"]
    source_record_id: str
    # Carried through from the source record. The candidate graph refuses to
    # link two records with different known merchant_ids (ADR 0010).
    merchant_id: str = "merch_unknown"
    record_type: Literal["payment", "refund", "settlement_credit", "sale", "adjustment", "unknown"]
    payment_id: str | None = None
    order_id: str | None = None
    settlement_id: str | None = None
    utr: str | None = None
    invoice_number: str | None = None
    reference_text: str | None = None
    amount_paise: int = Field(ge=0)
    fee_paise: int | None = Field(default=None, ge=0)
    tax_paise: int | None = Field(default=None, ge=0)
    net_amount_paise: int | None = Field(default=None, ge=0)
    currency: str = "INR"
    transaction_date: date
    settlement_date: date | None = None
    description: str | None = None
    raw_record: dict[str, Any]
    provenance: dict[str, Any]
