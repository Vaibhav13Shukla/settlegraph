from datetime import date

from pydantic import BaseModel, Field


class GSTInvoiceRecord(BaseModel):
    """One line item from a GST invoice register / GSTR-2B-shaped feed.

    Razorpay charges GST (currently 18%) on its own MDR fee, invoiced back
    to the merchant. This is the merchant's input-credit claim on that tax
    -- a real reconciliation surface distinct from the settlement-amount
    matching the rest of this engine does, and the PDF's own named
    "Tax-line matcher" example direction.
    """

    invoice_id: str
    settlement_id: str
    taxable_value_paise: int = Field(ge=0)  # the fee amount GST was charged on
    cgst_paise: int = Field(ge=0)
    sgst_paise: int = Field(ge=0)
    igst_paise: int = Field(ge=0)
    gst_rate_percent: float = Field(gt=0, le=28)
    invoice_date: date
    gstin: str

    @property
    def total_tax_paise(self) -> int:
        return self.cgst_paise + self.sgst_paise + self.igst_paise
