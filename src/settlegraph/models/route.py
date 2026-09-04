from datetime import datetime

from pydantic import BaseModel, Field


class RoutePayoutRecord(BaseModel):
    """One vendor payout leg from Razorpay Route (marketplace/vendor splits).

    A marketplace collects one payment, then Route splits it across one or
    more linked vendor accounts, minus Razorpay's Route fee. The
    reconciliation question here is different from the core settlement
    match: not "does this bank credit match this payment" but "do this
    payment's payout legs sum to what should have been distributed."
    """

    transfer_id: str
    source_payment_id: str  # the RazorpaySettlementRecord.entity_id this splits
    linked_account_id: str  # the vendor's Razorpay linked account
    amount_paise: int = Field(ge=0)
    route_fee_paise: int = Field(ge=0)
    processed_at: datetime
    status: str  # "processed" | "reversed" | "pending"
