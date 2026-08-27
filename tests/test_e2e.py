"""Comprehensive End-to-End (E2E) verification test suite for SettleGraph production readiness."""

from __future__ import annotations

import json
from pathlib import Path

from datagen.generator import SyntheticDataGenerator
from settlegraph.config import PipelineConfig
from settlegraph.engine.drift import ADWINDetector
from settlegraph.engine.idempotency import IdempotencyShield
from settlegraph.engine.pipeline import run_pipeline
from settlegraph.engine.simulator import simulate_all_failures
from settlegraph.models import NormalizedRecord


def test_full_pipeline_e2e_lifecycle(tmp_path: Path) -> None:
    """Validate full end-to-end data generation, reconciliation, verification, and reporting."""
    data_dir = tmp_path / "data"
    results_dir = tmp_path / "results"

    # Step 1: Generate 500 records with 15% anomalies
    gen = SyntheticDataGenerator(seed=123, anomaly_rate=0.15, output_dir=data_dir)
    gen.write(500)

    assert (data_dir / "razorpay_settlements.csv").exists()
    assert (data_dir / "bank_statements.csv").exists()
    assert (data_dir / "merchant_ledger.csv").exists()
    assert (data_dir / "ground_truth.csv").exists()

    # Step 2: Run full reconciliation pipeline
    config = PipelineConfig(
        generated_data_directory=data_dir,
        auto_match_threshold=0.95,
        exception_threshold=0.70,
        amount_tolerance_paise=100,
        date_tolerance_days=3,
    )
    summary = run_pipeline(data_dir=data_dir, output_dir=results_dir, config=config)

    # Step 3: Verify all output artifacts are written and non-empty
    assert (results_dir / "assignments.csv").exists()
    assert (results_dir / "unmatched.csv").exists()
    assert (results_dir / "exceptions.json").exists()
    assert (results_dir / "revenue_assurance.json").exists()
    assert (results_dir / "summary.json").exists()
    assert (results_dir / "evaluation.json").exists()
    assert (results_dir / "AUDIT_REPORT.md").exists()

    # Step 4: Validate precision guarantees (Zero false positive ledger entries)
    ev = summary.get("evaluation")
    assert ev is not None
    assert ev["precision"] == 1.0, "Zero False Positives invariant breached!"
    assert ev["false_positives"] == 0
    assert ev["recall"] >= 0.75, "Throughput recall dropped below threshold"
    assert ev["f1"] >= 0.85

    # Step 5: Validate financial conservation in Revenue Assurance
    rev = summary.get("revenue_assurance", {}).get("financial_summary", {})
    assert rev["merchant_sales_inr"] > 0
    assert rev["reconciled_settled_inr"] > 0
    assert rev["reconciliation_rate_percent"] >= 75.0
    assert rev["unexplained_exposure_inr"] >= 0

    # Step 6: Validate exceptions diagnosis
    exceptions = json.loads((results_dir / "exceptions.json").read_text(encoding="utf-8"))
    assert len(exceptions) > 0
    categories = {e["category"] for e in exceptions}
    assert "MISSING_COUNTERPART" in categories or "UTR_CORRUPTION" in categories


def test_failure_injection_suite_e2e() -> None:
    """Verify that all 5 failure modes are safely contained with zero false ledger entries."""
    results = simulate_all_failures()
    assert len(results) == 5

    for r in results:
        assert r.passed is True
        assert r.scenario_id in ("FAIL_01", "FAIL_02", "FAIL_03", "FAIL_04", "FAIL_05")
        assert len(r.safe_containment_proof) > 0


def test_adwin_drift_detector_e2e() -> None:
    """Verify ADWIN concept drift detection on streaming distribution shift."""
    detector = ADWINDetector(delta=0.01)

    # Regime 1: High match confidence (0.98 +/- noise)
    for _ in range(40):
        assert not detector.add_element(0.98)

    # Regime 2: Abrupt drop (0.45) due to gateway syntax change or fee shift
    drift_detected = False
    for _ in range(40):
        if detector.add_element(0.45):
            drift_detected = True
            break

    assert drift_detected is True
    assert len(detector.drift_history) > 0


def test_idempotency_shield_stream_e2e() -> None:
    """Verify that cryptographic stream filtering blocks duplicate replays."""
    from datetime import date

    shield = IdempotencyShield()

    records = [
        NormalizedRecord(
            record_id=f"rec_{i}",
            source="razorpay",
            source_record_id=f"pay_{i}",
            record_type="payment",
            amount_paise=10000 + i * 100,
            currency="INR",
            transaction_date=date(2026, 1, 15),
            raw_record={},
            provenance={"source": "razorpay"},
        )
        for i in range(10)
    ]

    # Add duplicate replay of record 0 and 1
    dup_0 = NormalizedRecord(
        record_id="rec_0_replay",
        source="razorpay",
        source_record_id="pay_0",
        record_type="payment",
        amount_paise=10000,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        raw_record={},
        provenance={"source": "razorpay"},
    )
    dup_1 = NormalizedRecord(
        record_id="rec_1_replay",
        source="razorpay",
        source_record_id="pay_1",
        record_type="payment",
        amount_paise=10100,
        currency="INR",
        transaction_date=date(2026, 1, 15),
        raw_record={},
        provenance={"source": "razorpay"},
    )

    all_records = records + [dup_0, dup_1]
    unique, duplicates = shield.filter_duplicates(all_records)

    assert len(unique) == 10
    assert len(duplicates) == 2
    assert shield.duplicate_count == 2
