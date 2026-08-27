from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from settlegraph.models import BankStatementRecord, RazorpaySettlementRecord


def test_payment_net_must_balance() -> None:
    with pytest.raises(ValidationError, match="net_amount_paise"):
        RazorpaySettlementRecord(
            entity_id="pay_1",
            entity_type="payment",
            settlement_id="setl_1",
            amount_paise=10_000,
            fee_paise=200,
            tax_paise=36,
            net_amount_paise=9_800,
            captured_at=datetime.now(UTC),
            settled_at=datetime.now(UTC),
            status="captured",
        )


def test_bank_requires_single_direction() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        BankStatementRecord(
            record_id="b1",
            transaction_date=datetime.now(UTC).date(),
            description="test",
            bank_name="ICICI",
            account_number="x",
        )
