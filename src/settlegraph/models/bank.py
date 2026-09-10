from datetime import date

from pydantic import BaseModel, Field, model_validator


class BankStatementRecord(BaseModel):
    record_id: str
    # The merchant that owns the bank account this line settled into.
    # Defaulted for backward compatibility; the generator always sets it.
    merchant_id: str = "merch_unknown"
    transaction_date: date
    value_date: date | None = None
    description: str
    reference_number: str | None = None
    debit_amount_paise: int | None = Field(default=None, ge=0)
    credit_amount_paise: int | None = Field(default=None, ge=0)
    balance_paise: int | None = None
    bank_name: str
    account_number: str

    @model_validator(mode="after")
    def exactly_one_direction(self) -> "BankStatementRecord":
        if (self.debit_amount_paise is None) == (self.credit_amount_paise is None):
            raise ValueError("exactly one of debit_amount_paise or credit_amount_paise is required")
        return self
