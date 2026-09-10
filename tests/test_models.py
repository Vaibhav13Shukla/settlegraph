from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from settlegraph.models import (
    BankStatementRecord,
    GroundTruthRecord,
    RazorpaySettlementRecord,
)


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


def test_ground_truth_rejects_unimplemented_relationship_types() -> None:
    """`merge` (N:1) was a relationship_type the system never generated,
    matched, or evaluated -- an enum value claiming a capability that does not
    exist (expert feedback C). It, `adjustment_for`, and `timing_only` were
    removed; the vocabulary is now exactly what the pipeline implements. Adding
    real N:1 aggregation is a deliberate reconciliation-unit change, not a
    Literal edit -- so the value must be rejected until that work exists."""
    for dead in ("merge", "adjustment_for", "timing_only"):
        with pytest.raises(ValidationError):
            GroundTruthRecord(
                razorpay_record_id="pay_1",
                true_bank_record_ids=["bank_1"],
                relationship_type=dead,
            )


def test_ground_truth_accepts_the_implemented_relationship_types() -> None:
    """The complement: the four types the pipeline actually produces and
    consumes must all still validate."""
    for live in ("exact_match", "split", "refund_of", "no_counterpart"):
        record = GroundTruthRecord(
            razorpay_record_id="pay_1",
            true_bank_record_ids=["bank_1"],
            relationship_type=live,
        )
        assert record.relationship_type == live
