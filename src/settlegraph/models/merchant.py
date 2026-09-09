from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MerchantLedgerRecord(BaseModel):
    ledger_id: str
    # Owning merchant. Defaulted for backward compatibility; the generator
    # always sets it. Reconciliation is merchant-scoped (ADR 0010).
    merchant_id: str = "merch_unknown"
    order_id: str | None = None
    invoice_number: str | None = None
    customer_id: str | None = None
    amount_paise: int = Field(gt=0)
    currency: str = "INR"
    transaction_type: Literal["sale", "refund", "adjustment", "credit_note", "debit_note"]
    created_at: datetime
    payment_status: str | None = None
    payment_gateway_id: str | None = None
    notes: str | None = None
