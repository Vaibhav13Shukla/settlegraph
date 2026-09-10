from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RazorpaySettlementRecord(BaseModel):
    entity_id: str
    # Which merchant this settlement belongs to. Defaulted so existing
    # single-tenant CSVs and fixtures load unchanged; the generator always
    # sets a real value. Reconciliation never crosses merchants -- see
    # engine/match.py's isolation guard and ADR 0010.
    merchant_id: str = "merch_unknown"
    entity_type: Literal["payment", "refund", "transfer", "adjustment"]
    settlement_id: str
    settlement_utr: str | None = None
    order_id: str | None = None
    amount_paise: int = Field(gt=0)
    currency: str = "INR"
    fee_paise: int = Field(ge=0)
    tax_paise: int = Field(ge=0)
    net_amount_paise: int = Field(ge=0)
    payment_method: str | None = None
    captured_at: datetime
    settled_at: datetime
    description: str | None = None
    notes: dict[str, str] | None = None
    status: str

    @model_validator(mode="after")
    def net_must_balance(self) -> "RazorpaySettlementRecord":
        if self.entity_type == "payment":
            expected = self.amount_paise - self.fee_paise - self.tax_paise
            if self.net_amount_paise != expected:
                raise ValueError("net_amount_paise must equal amount - fee - tax")
        return self
