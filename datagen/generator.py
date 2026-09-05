from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from settlegraph.engine.tax_matcher import TRUE_GST_RATE_PERCENT
from settlegraph.models import (
    BankStatementRecord,
    GroundTruthRecord,
    GSTInvoiceRecord,
    MerchantLedgerRecord,
    RazorpaySettlementRecord,
    RoutePayoutRecord,
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

    def write_gst_and_route(self, razorpay: list[RazorpaySettlementRecord]) -> None:
        """Additive to `write()`, not folded into it: the core generate/write
        path is exercised by every existing test and demo script, and this
        keeps that surface untouched. Call after `write()`, passing the same
        `razorpay` list `generate()` returned.
        """
        gst_invoices = self.generate_gst_invoices(razorpay)
        route_payouts = self.generate_route_payouts(razorpay)
        self._write_csv(
            self.output_dir / "gst_invoices.csv",
            [row.model_dump(mode="json") for row in gst_invoices],
        )
        self._write_csv(
            self.output_dir / "route_payouts.csv",
            [row.model_dump(mode="json") for row in route_payouts],
        )

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
        # Not every merchant settles to a traditional bank account -- a real
        # and growing share settle straight into a RazorpayX current
        # account instead. Modeled here as just another value of the
        # existing `bank_name` field (BankStatementRecord already supported
        # this; nothing about the schema needed to change) so the core
        # engine's reconciliation logic, which is bank-name-agnostic, gets
        # to prove it already generalizes rather than needing a parallel
        # code path for "the Razorpay-native rail."
        bank_name, account_number = self.rng.choices(
            [("ICICI", "XXXX001234"), ("HDFC", "XXXX007788"), ("RazorpayX", "RZPX00XXXX2201")],
            weights=[0.55, 0.25, 0.20],
            k=1,
        )[0]
        return BankStatementRecord(
            record_id=f"bank_{record.index:06d}",
            transaction_date=record.settled_at.date(),
            value_date=record.settled_at.date(),
            description=f"NEFT/RAZORPAY/{record.utr}/{record.settlement_id}",
            reference_number=record.utr,
            credit_amount_paise=net,
            balance_paise=None,
            bank_name=bank_name,
            account_number=account_number,
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
        """Inject controlled noise so the batch breaks clean automation on purpose.

        The original five kinds (missing/corrupted UTR, timing, fee mismatch,
        missing merchant record) only exercise single-record corruption. Real
        settlement feeds fail in stranger shapes than that -- a payment
        settling as two bank credits, a bank reusing a reference number,
        a statement arriving dated before the payment it settles, a unit-
        confusion-shaped amount error, and gateway/ledger currency drift are
        all business edge cases a merchant's ops team has actually seen. Each
        new kind still follows the existing pattern: mutate the already-
        rendered bank/merchant view, never the underlying reality, so the
        generator's own double-entry conservation invariant stays intact for
        every record this function doesn't touch.
        """
        count = int(len(truth) * self.anomaly_rate)
        extra_bank_records: list[BankStatementRecord] = []
        indices_to_drop_from_bank: set[int] = set()
        kinds = [
            "missing_utr",
            "corrupted_utr",
            "timing_difference",
            "fee_mismatch",
            "missing_merchant_record",
            "split_settlement",
            "duplicate_utr_reuse",
            "out_of_order_arrival",
            "extreme_amount_mismatch",
            "malformed_description",
            "currency_mismatch",
            "no_counterpart",
        ]
        for index in self.rng.sample(range(len(truth)), count):
            kind = self.rng.choice(kinds)
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
            elif kind == "split_settlement" and bank[index].credit_amount_paise:
                # A gateway sometimes settles one payment as two bank credits
                # (partial batch cutoff). true_bank_record_ids already supports
                # a list; GroundTruthRecord.relationship_type already has
                # "split" as a valid value -- this was designed for, just
                # never exercised.
                total = bank[index].credit_amount_paise
                first_share = total // 2
                second_share = total - first_share
                if first_share > 0 and second_share > 0:
                    bank[index].credit_amount_paise = first_share
                    second_record = bank[index].model_copy(
                        update={
                            "record_id": f"{bank[index].record_id}_split2",
                            "credit_amount_paise": second_share,
                        }
                    )
                    extra_bank_records.append(second_record)
                    truth[index].relationship_type = "split"
                    truth[index].true_bank_record_ids = [
                        bank[index].record_id,
                        second_record.record_id,
                    ]
            elif kind == "duplicate_utr_reuse":
                # A bank reference number gets reused across two unrelated
                # settlements (recycled batch numbering). The correct
                # behaviour is that at most one of the two competing bank
                # rows wins the match -- never both, and never silently the
                # wrong one without the exception queue noticing.
                donor_pool = [
                    i for i in range(len(bank)) if i != index and bank[i].reference_number
                ]
                if donor_pool:
                    donor = self.rng.choice(donor_pool)
                    bank[index].reference_number = bank[donor].reference_number
            elif kind == "out_of_order_arrival":
                # Backdated bank statement / clock skew: the credit is dated
                # well before the payment it settles, not just a few days off
                # like `timing_difference`.
                bank[index].transaction_date -= timedelta(days=self.rng.randint(10, 20))
                bank[index].value_date = bank[index].transaction_date
            elif kind == "extreme_amount_mismatch" and bank[index].credit_amount_paise:
                # Unit-confusion-shaped error (paise read as rupees, or vice
                # versa) rather than a small rounding drift -- tests whether
                # the *relative*-tolerance branch in score_edge
                # (`diff / rzp_net < 0.01`) can be fooled by scale the way a
                # fixed absolute tolerance could.
                bank[index].credit_amount_paise *= 100
            elif kind == "malformed_description":
                bank[index].description = "NEFT/RAZORPAY/⚠️​<<<INJECTED>>>/" + "ട" * 200
            elif kind == "currency_mismatch":
                merchant[index].currency = "USD"
            elif kind == "no_counterpart":
                # A settlement that genuinely never reached the bank -- not
                # corrupted, not delayed, not split: it simply never
                # arrived (a gateway/bank data-loss event, or a capture that
                # was reversed downstream after ground truth was recorded).
                # The merchant ledger still shows the sale -- that half of
                # the business event actually happened -- but there is no
                # true bank counterpart to find, ever, in this batch.
                #
                # relationship_type="no_counterpart" and
                # true_bank_record_ids=[] have existed on GroundTruthRecord
                # since Day 1 but were never actually produced until this,
                # which is why Dangerous Miss Rate and Exception Recall
                # stayed unmeasurable: there was nothing to measure them on.
                # Marked for removal here rather than deleted immediately --
                # `bank[index]` positions must stay stable for every other
                # index this same loop still has to process.
                indices_to_drop_from_bank.add(index)
                truth[index].relationship_type = "no_counterpart"
                truth[index].true_bank_record_ids = []
        for i in sorted(indices_to_drop_from_bank, reverse=True):
            del bank[i]
        bank.extend(extra_bank_records)

    def generate_gst_invoices(
        self, razorpay: list[RazorpaySettlementRecord], anomaly_rate: float = 0.12
    ) -> list[GSTInvoiceRecord]:
        """Render a GSTR-2B-shaped invoice feed for the GST charged on
        Razorpay's own MDR fee -- a reconciliation surface distinct from the
        settlement-amount matching the rest of this engine does.

        One invoice per *settlement batch*, not per payment: `settlement_id`
        already groups ~20 payments together (see `_reality`), matching how
        Razorpay actually raises one consolidated GST invoice per
        settlement cycle rather than one per transaction. Keying this
        per-payment instead was tried first and produced a batch of ~20
        payments each individually "matched" against the same one invoice
        -- which the matcher correctly, but uselessly, flagged as
        DUPLICATE_INVOICE on nearly everything. Aggregating here is what
        makes "duplicate" mean something real again.

        Anomaly kinds:
        - clean: taxable value and rate match the batch exactly.
        - rate_mismatch: invoice shows 12% or 28% instead of the true 18%.
        - missing_invoice: no invoice was raised at all for this batch.
        - rounding_drift: a few paise of rounding noise, within tolerance.
        - duplicate_invoice: two invoices raised for the same batch -- a
          real input-tax-credit overclaim risk.
        """
        rng = random.Random(self.seed + 7331)
        invoices: list[GSTInvoiceRecord] = []
        # Shared with engine/tax_matcher.py rather than redefined here --
        # the rate that decides RATE_MISMATCH vs MATCHED is real-money
        # logic; two independently hardcoded 18.0 literals in different
        # files (as this was before) is exactly the kind of drift
        # config.py's own docstring warns against ("financial thresholds
        # are never magic numbers"). Found by code review.
        true_rate = TRUE_GST_RATE_PERCENT

        by_settlement: dict[str, list[RazorpaySettlementRecord]] = {}
        for record in razorpay:
            if record.entity_type == "payment" and record.fee_paise > 0:
                by_settlement.setdefault(record.settlement_id, []).append(record)

        for settlement_id in sorted(by_settlement):
            batch = by_settlement[settlement_id]
            total_fee = sum(r.fee_paise for r in batch)
            invoice_date = max(r.settled_at for r in batch).date()

            roll = rng.random()
            if roll < anomaly_rate * 0.3:
                kind = "missing_invoice"
            elif roll < anomaly_rate * 0.6:
                kind = "rate_mismatch"
            elif roll < anomaly_rate * 0.85:
                kind = "rounding_drift"
            elif roll < anomaly_rate:
                kind = "duplicate_invoice"
            else:
                kind = "clean"

            if kind == "missing_invoice":
                continue

            rate = rng.choice([12.0, 28.0]) if kind == "rate_mismatch" else true_rate
            expected_tax = round(total_fee * rate / 100)
            drift = rng.choice([-3, -2, -1, 1, 2, 3]) if kind == "rounding_drift" else 0
            total_tax = max(0, expected_tax + drift)
            # Intra-state assumption throughout this synthetic feed: split
            # evenly into CGST/SGST, IGST always zero. A real feed would
            # carry actual place-of-supply data; that's out of scope here.
            cgst = total_tax // 2
            sgst = total_tax - cgst

            invoice = GSTInvoiceRecord(
                invoice_id=f"gst_{kind}_{settlement_id}",
                settlement_id=settlement_id,
                taxable_value_paise=total_fee,
                cgst_paise=cgst,
                sgst_paise=sgst,
                igst_paise=0,
                gst_rate_percent=rate,
                invoice_date=invoice_date,
                gstin=f"29AAAAA{rng.randrange(1000, 9999)}A1Z{rng.randrange(1, 9)}",
            )
            invoices.append(invoice)
            if kind == "duplicate_invoice":
                invoices.append(
                    invoice.model_copy(update={"invoice_id": invoice.invoice_id + "_dup"})
                )

        return invoices

    def generate_route_payouts(
        self, razorpay: list[RazorpaySettlementRecord], marketplace_rate: float = 0.10
    ) -> list[RoutePayoutRecord]:
        """A fraction of payments are marketplace collections that Route
        splits across 2-3 linked vendor accounts, minus a Route fee.

        A small slice of these split badly on purpose (payout legs that
        don't sum to the original amount minus fee) -- the thing
        `engine/route_reconciliation.py` exists to catch.
        """
        rng = random.Random(self.seed + 5051)
        payouts: list[RoutePayoutRecord] = []

        for record in razorpay:
            if record.entity_type != "payment" or rng.random() >= marketplace_rate:
                continue
            n_vendors = rng.choice([2, 2, 3])
            route_fee = round(record.amount_paise * 0.02)
            distributable = record.amount_paise - route_fee
            shares = self._split_amount(distributable, n_vendors, rng)

            broken = rng.random() < 0.15
            if broken:
                # Three real Route failure shapes, not just one: a leg that
                # silently never reached a vendor, a leg underpaid, or a
                # leg overpaid (e.g. a fee miscalculation that leaves too
                # much distributed). The first version of this only ever
                # produced shortfalls -- reconcile_route_splits's
                # PAYOUT_OVERPAYMENT path existed and was unit-tested, but
                # nothing here ever actually exercised it end to end.
                fail_kind = rng.choice(["drop_leg", "underpay", "overpay"])
                if fail_kind == "drop_leg" and len(shares) > 1:
                    shares.pop(rng.randrange(len(shares)))
                elif fail_kind == "overpay":
                    shares[rng.randrange(len(shares))] += rng.randint(100, 5000)
                else:
                    shares[rng.randrange(len(shares))] -= rng.randint(100, 5000)

            for i, share in enumerate(shares):
                if share <= 0:
                    continue
                payouts.append(
                    RoutePayoutRecord(
                        transfer_id=f"trf_{record.entity_id}_{i}",
                        source_payment_id=record.entity_id,
                        linked_account_id=f"acc_{rng.randrange(1, 200):04d}",
                        amount_paise=share,
                        route_fee_paise=route_fee // n_vendors,
                        processed_at=record.settled_at,
                        status="processed",
                    )
                )
        return payouts

    @staticmethod
    def _split_amount(total: int, n: int, rng: random.Random) -> list[int]:
        """Split `total` paise into `n` positive-ish shares that sum exactly
        to `total` -- exact by construction, not by rounding luck."""
        if n <= 1:
            return [total]
        cuts = (
            sorted(rng.randrange(1, total) for _ in range(n - 1))
            if total > n
            else list(range(1, n))
        )
        bounds = [0, *cuts, total]
        return [bounds[i + 1] - bounds[i] for i in range(n)]

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
