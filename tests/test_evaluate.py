"""Tests for the evaluation harness."""

from __future__ import annotations

import tempfile
from pathlib import Path

from settlegraph.engine.evaluate import evaluate


def test_evaluate_returns_metrics(tmp_path: Path) -> None:
    # Write assignments CSV
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
        "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,0.98,AUTO_MATCH,10000,9764,RZP001,RZP001,order_1,\n"
        "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_2,0.30,EXCEPTION,10000,9764,RZP002,RZP002,order_2,\n"
    )

    # Write ground truth CSV
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
        "pay_2,bank_2,led_2,exact_match,,\n"
    )

    results = evaluate(assignments_csv, gt_csv)

    assert "precision" in results
    assert "recall" in results
    assert "f1" in results
    assert "true_positives" in results
    assert "false_positives" in results
    assert "false_negatives" in results
    assert results["precision"] >= 0
    assert results["recall"] >= 0
    assert results["f1"] >= 0


def test_evaluate_handles_no_matches() -> None:
    tmp_path = Path(tempfile.mkdtemp(prefix="test_eval_empty_"))

    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
    )

    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
    )

    results = evaluate(assignments_csv, gt_csv)

    assert results["precision"] == 0.0
    assert results["recall"] == 0.0
    assert results["f1"] == 0.0
    assert results["false_negatives"] == 1
