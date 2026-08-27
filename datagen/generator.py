from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from settlegraph.models import (
    BankStatementRecord,
    GroundTruthRecord,
    MerchantLedgerRecord,
    RazorpaySettlementRecord,
)

PAYMENT_METHODS = (
    ("upi", 0.45, 0.0),
    ("card", 0.25, 0.02),
    ("netbanking", 0.15, 0.018),
    ("wallet", 0.10, 0.018),
    ("emi", 0.05, 0.025),
)


@dataclass(frozen=True)
class TransactionReality:
    index: int
    payment_id: str
    order_id: str
    settlement_id: str
    utr: str
    customer_id: str
    amount_paise: int
    fee_paise: int
    tax_paise: int
    net_amount_paise: int
    method: str
    captured_at: datetime
    settled_at: datetime
    refunded_paise: int = 0


class SyntheticDataGenerator:
    """Generate truth first; source renderers may then introduce controlled noise."""

    def __init__(
        self, seed: int = 42, anomaly_rate: float = 0.15, output_dir: Path | str = "data/generated"
    ):
        if not 0 <= anomaly_rate <= 1:
            raise ValueError("anomaly_rate must be between 0 and 1")
        self.seed = seed
        self.anomaly_rate = anomaly_rate
        self.output_dir = Path(output_dir)
        self.rng = random.Random(seed)

    def generate(
        self, total_records: int
    ) -> tuple[
        list[RazorpaySettlementRecord],
        list[BankStatementRecord],
        list[MerchantLedgerRecord],
        list[GroundTruthRecord],
    ]:
        if total_records < 50:
            raise ValueError("SettleGraph requires at least 50 financial realities")
        realities = [self._reality(index) for index in range(1, total_records + 1)]
        razorpay = [self._razorpay(record) for record in realities]
        bank = [self._bank(record) for record in realities]
        merchant = [self._merchant(record) for record in realities]
        truth = [self._truth(record) for record in realities]
        self._inject_anomalies(bank, merchant, truth)
        return razorpay, bank, merchant, truth

    def write(self, total_records: int, split_output_dir: Path | str | None = None) -> Path:
        razorpay, bank, merchant, truth = self.generate(total_records)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._write_csv(
            self.output_dir / "razorpay_settlements.csv",
            [row.model_dump(mode="json") for row in razorpay],
        )
        self._write_csv(
            self.output_dir / "bank_statements.csv", [row.model_dump(mode="json") for row in bank]
        )
        self._write_csv(
            self.output_dir / "merchant_ledger.csv",
            [row.model_dump(mode="json") for row in merchant],
        )
        self._write_csv(
            self.output_dir / "ground_truth.csv", [self._truth_row(row) for row in truth]
        )
        self._write_csv(
            self.output_dir / "reality_manifest.csv",
            [
                {
                    "payment_id": r.entity_id,
                    "order_id": r.order_id or "",
                    "settlement_id": r.settlement_id,
                    "utr": r.settlement_utr or "",
                    "amount_paise": r.amount_paise,
                    "fee_paise": r.fee_paise,
                    "tax_paise": r.tax_paise,
                    "net_amount_paise": r.net_amount_paise,
                    "captured_at": r.captured_at.isoformat(),
                    "settled_at": r.settled_at.isoformat(),
                }
                for r in razorpay
            ],
        )
        if split_output_dir is not None:
            self._write_splits(
                razorpay=razorpay,
                bank=bank,
                merchant=merchant,
                truth=truth,
                split_output_dir=Path(split_output_dir),
            )
        return self.output_dir

    def _write_splits(
        self,
        razorpay: list[RazorpaySettlementRecord],
        bank: list[BankStatementRecord],
        merchant: list[MerchantLedgerRecord],
        truth: list[GroundTruthRecord],
        split_output_dir: Path,
    ) -> None:
        split_output_dir.mkdir(parents=True, exist_ok=True)
        split_names = ("train", "calibration", "test", "adversarial")
        split_ratios = (0.40, 0.15, 0.30, 0.15)

        payment_ids = [r.entity_id for r in razorpay]
        rng = random.Random(self.seed + 991)
        rng.shuffle(payment_ids)

        n = len(payment_ids)
        counts = [int(n * r) for r in split_ratios]
        counts[-1] = n - sum(counts[:-1])

        cursor = 0
        split_members: dict[str, set[str]] = {}
        for name, count in zip(split_names, counts, strict=True):
            split_members[name] = set(payment_ids[cursor : cursor + count])
            cursor += count

        bank_by_truth_id: dict[str, set[str]] = {}
        for row in truth:
            bank_ids = set(row.true_bank_record_ids)
            bank_by_truth_id[row.razorpay_record_id] = bank_ids

        for split_name, member_ids in split_members.items():
            out = split_output_dir / split_name
            out.mkdir(parents=True, exist_ok=True)

            rzp_rows = [r.model_dump(mode="json") for r in razorpay if r.entity_id in member_ids]
            truth_rows = [self._truth_row(t) for t in truth if t.razorpay_record_id in member_ids]

            bank_ids = set()
            for pid in member_ids:
                bank_ids |= bank_by_truth_id.get(pid, set())
            bank_rows = [b.model_dump(mode="json") for b in bank if b.record_id in bank_ids]

            merchant_ids = {f"led_{pid.replace('pay_', '')}" for pid in member_ids}
            merchant_rows = [
                m.model_dump(mode="json") for m in merchant if m.ledger_id in merchant_ids
            ]

            self._write_csv(out / "razorpay_settlements.csv", rzp_rows)
            self._write_csv(out / "bank_statements.csv", bank_rows)
            self._write_csv(out / "merchant_ledger.csv", merchant_rows)
            self._write_csv(out / "ground_truth.csv", truth_rows)

    def _reality(self, index: int) -> TransactionReality:
        method, _, fee_rate = self.rng.choices(
            PAYMENT_METHODS, weights=[row[1] for row in PAYMENT_METHODS], k=1
        )[0]
        # Integer log-normal-like rupee distribution, bounded to ₹1–₹1,00,000.
        amount_paise = min(10_000_000, max(100, int(self.rng.lognormvariate(7.5, 1.2) * 100)))
        fee = round(amount_paise * fee_rate)
        tax = round(fee * 0.18)
        net_amount_paise = amount_paise - fee - tax
        captured = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(
            days=self.rng.randrange(181), minutes=self.rng.randrange(1440)
        )
        settlement_delay = self.rng.choices([1, 2, 3, 7], weights=[0.30, 0.50, 0.15, 0.05], k=1)[0]
        refund = 0
        if self.rng.random() < 0.08:
            # This Day 1 bank view contains settlement credits only. Refunds are
            # therefore capped at the net settlement; a future refund view will
            # represent excess refund liability as a separate debit/adjustment.
            refund = (
                int(net_amount_paise * self.rng.uniform(0.10, 0.80))
                if self.rng.random() < 0.30
                else net_amount_paise
            )
        return TransactionReality(
            index,
            f"pay_{index:06d}",
            f"order_{index:06d}",
            f"setl_{index // 20:05d}",
            f"RZP{index:012d}",
            f"cust_{self.rng.randrange(1, 500):04d}",
            amount_paise,
            fee,
            tax,
            net_amount_paise,
            method,
            captured,
            captured + timedelta(days=settlement_delay),
            refund,
        )

    def _razorpay(self, record: TransactionReality) -> RazorpaySettlementRecord:
        return RazorpaySettlementRecord(
            entity_id=record.payment_id,
            entity_type="payment",
            settlement_id=record.settlement_id,
            settlement_utr=record.utr,
            order_id=record.order_id,
            amount_paise=record.amount_paise,
            fee_paise=record.fee_paise,
            tax_paise=record.tax_paise,
            net_amount_paise=record.net_amount_paise,
            payment_method=record.method,
            captured_at=record.captured_at,
            settled_at=record.settled_at,
            description=f"Order {record.order_id}",
            notes={"customer_id": record.customer_id},
            status="captured",
        )

    def _bank(self, record: TransactionReality) -> BankStatementRecord:
        net = record.net_amount_paise - record.refunded_paise
        return BankStatementRecord(
            record_id=f"bank_{record.index:06d}",
            transaction_date=record.settled_at.date(),
            value_date=record.settled_at.date(),
            description=f"NEFT/RAZORPAY/{record.utr}/{record.settlement_id}",
            reference_number=record.utr,
            credit_amount_paise=net,
            balance_paise=None,
            bank_name="ICICI",
            account_number="XXXX001234",
        )

    def _merchant(self, record: TransactionReality) -> MerchantLedgerRecord:
        return MerchantLedgerRecord(
            ledger_id=f"led_{record.index:06d}",
            order_id=record.order_id,
            invoice_number=f"INV-2026-{record.index:06d}",
            customer_id=record.customer_id,
            amount_paise=record.amount_paise,
            transaction_type="sale",
            created_at=record.captured_at,
            payment_status="paid",
            payment_gateway_id=record.payment_id,
            notes=f"Razorpay {record.payment_id}",
        )

    def _truth(self, record: TransactionReality) -> GroundTruthRecord:
        relationship = "refund_of" if record.refunded_paise else "exact_match"
        return GroundTruthRecord(
            razorpay_record_id=record.payment_id,
            true_bank_record_ids=[f"bank_{record.index:06d}"],
            true_merchant_record_id=f"led_{record.index:06d}",
            relationship_type=relationship,
            anomaly_type="partial_refund"
            if 0 < record.refunded_paise < record.amount_paise
            else ("full_refund" if record.refunded_paise else None),
            notes=f"refund_paise={record.refunded_paise}",
        )

    def _inject_anomalies(
        self,
        bank: list[BankStatementRecord],
        merchant: list[MerchantLedgerRecord],
        truth: list[GroundTruthRecord],
    ) -> None:
        count = int(len(truth) * self.anomaly_rate)
        for index in self.rng.sample(range(len(truth)), count):
            kind = self.rng.choice(
                [
                    "missing_utr",
                    "corrupted_utr",
                    "timing_difference",
                    "fee_mismatch",
                    "missing_merchant_record",
                ]
            )
            truth[index].anomaly_type = kind
            if kind == "missing_utr":
                bank[index].reference_number = None
                bank[index].description = "NEFT/RAZORPAY/SETTLEMENT CREDIT"
            elif kind == "corrupted_utr" and bank[index].reference_number:
                bank[index].reference_number = bank[index].reference_number[:-1] + "X"
            elif kind == "timing_difference":
                bank[index].transaction_date += timedelta(days=self.rng.choice([-3, -2, 2, 3]))
            elif kind == "fee_mismatch" and bank[index].credit_amount_paise:
                bank[index].credit_amount_paise += self.rng.choice([-1000, -500, 500, 1000])
            elif kind == "missing_merchant_record":
                merchant[index].payment_gateway_id = None
                merchant[index].order_id = None

    @staticmethod
    def _write_csv(path: Path, rows: list[dict]) -> None:
        if not rows:
            return
        fields = list(rows[0])
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _truth_row(row: GroundTruthRecord) -> dict[str, str]:
        return {
            "razorpay_record_id": row.razorpay_record_id,
            "true_bank_record_ids": "|".join(row.true_bank_record_ids),
            "true_merchant_record_id": row.true_merchant_record_id or "",
            "relationship_type": row.relationship_type,
            "anomaly_type": row.anomaly_type or "",
            "notes": row.notes,
        }
