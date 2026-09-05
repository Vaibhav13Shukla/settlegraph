"""Tests for confidence-quality and decision-stability metrics."""

from __future__ import annotations

from pathlib import Path

from settlegraph.engine.calibration import (
    compute_abstention_quality,
    compute_calibration,
    compute_replay_consistency,
)

ASSIGNMENTS_HEADER = (
    "source_a,source_a_id,source_b,source_b_id,confidence,label,"
    "a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
)
GT_HEADER = "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"


def test_perfectly_calibrated_confidence_scores_near_zero_error(tmp_path: Path) -> None:
    """Confidence 1.0 stated on matches that are all actually correct ->
    ECE and Brier should both be (near) zero: the confidence numbers are
    telling the truth."""
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        ASSIGNMENTS_HEADER
        + "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,1.0,AUTO_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
        + "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_2,1.0,AUTO_MATCH,20000,20000,RZP002,RZP002,order_2,\n"
        + "razorpay,rzp_norm_pay_3,bank,bank_norm_bank_3,1.0,AUTO_MATCH,30000,30000,RZP003,RZP003,order_3,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        GT_HEADER
        + "pay_1,bank_1,led_1,exact_match,,\n"
        + "pay_2,bank_2,led_2,exact_match,,\n"
        + "pay_3,bank_3,led_3,exact_match,,\n"
    )

    result = compute_calibration(assignments_csv, gt_csv)

    assert result["total_scored"] == 3
    assert result["expected_calibration_error"] == 0.0
    assert result["brier_score"] == 0.0


def test_overconfident_wrong_matches_produce_high_ece_and_brier(tmp_path: Path) -> None:
    """This is the failure mode the metric exists to catch: confidence 0.99
    stated on matches that are all actually WRONG. A UI showing 0.99 here
    would be actively lying to whoever reads it. ECE and Brier must both
    come back near the worst possible value (1.0), not near zero."""
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        ASSIGNMENTS_HEADER
        + "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_99,0.99,AUTO_MATCH,10000,10000,RZP001,RZP099,order_1,\n"
        + "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_98,0.99,AUTO_MATCH,20000,20000,RZP002,RZP098,order_2,\n"
        + "razorpay,rzp_norm_pay_3,bank,bank_norm_bank_97,0.99,AUTO_MATCH,30000,30000,RZP003,RZP097,order_3,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        GT_HEADER
        + "pay_1,bank_1,led_1,exact_match,,\n"
        + "pay_2,bank_2,led_2,exact_match,,\n"
        + "pay_3,bank_3,led_3,exact_match,,\n"
    )

    result = compute_calibration(assignments_csv, gt_csv)

    assert result["total_scored"] == 3
    assert result["expected_calibration_error"] > 0.9
    assert result["brier_score"] > 0.9


def test_reliability_bins_sum_to_total_scored_and_stay_in_bounds(tmp_path: Path) -> None:
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        ASSIGNMENTS_HEADER
        + "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,1.0,AUTO_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
        + "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_99,0.75,LIKELY_MATCH,20000,20000,RZP002,RZP099,order_2,\n"
        + "razorpay,rzp_norm_pay_3,bank,bank_norm_bank_3,0.9,LIKELY_MATCH,30000,30000,RZP003,RZP003,order_3,\n"
        + "razorpay,rzp_norm_pay_4,bank,bank_norm_bank_50,0.6,EXCEPTION,40000,10000,RZP004,RZP050,order_4,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        GT_HEADER
        + "pay_1,bank_1,led_1,exact_match,,\n"
        + "pay_2,bank_2,led_2,exact_match,,\n"
        + "pay_3,bank_3,led_3,exact_match,,\n"
        + "pay_4,bank_4,led_4,exact_match,,\n"
    )

    result = compute_calibration(assignments_csv, gt_csv, n_bins=10)

    bins = result["reliability_bins"]
    assert len(bins) == 10
    assert sum(b["count"] for b in bins) == result["total_scored"] == 4
    for b in bins:
        assert 0.0 <= b["accuracy"] <= 1.0
        assert b["bin_lower"] < b["bin_upper"]


def test_compute_calibration_handles_empty_input_without_crashing(tmp_path: Path) -> None:
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(ASSIGNMENTS_HEADER)
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(GT_HEADER)

    result = compute_calibration(assignments_csv, gt_csv)

    assert result["total_scored"] == 0
    assert result["expected_calibration_error"] == 0.0
    assert result["brier_score"] == 0.0
    assert sum(b["count"] for b in result["reliability_bins"]) == 0


def test_abstention_quality_counts_justified_and_unjustified_separately(
    tmp_path: Path,
) -> None:
    """pay_1's LIKELY_MATCH holds bank_99 as a candidate, but ground truth
    says the true counterpart is bank_1 -- the held candidate is wrong, so
    abstaining was justified (it protected the books from a false
    auto-book). pay_2's EXCEPTION holds bank_2, which IS the true
    counterpart -- the candidate was actually right, so this abstention is
    unjustified (safe, but it cost throughput, not a bug)."""
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        ASSIGNMENTS_HEADER
        + "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_99,0.75,LIKELY_MATCH,10000,10000,RZP001,RZP099,order_1,\n"
        + "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_2,0.6,EXCEPTION,20000,20000,RZP002,RZP002,order_2,\n"
        + "razorpay,rzp_norm_pay_3,bank,bank_norm_bank_3,0.98,AUTO_MATCH,30000,30000,RZP003,RZP003,order_3,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        GT_HEADER
        + "pay_1,bank_1,led_1,exact_match,,\n"
        + "pay_2,bank_2,led_2,exact_match,,\n"
        + "pay_3,bank_3,led_3,exact_match,,\n"
    )

    result = compute_abstention_quality(assignments_csv, gt_csv)

    assert result["abstentions"] == 2
    assert result["justified_abstentions"] == 1
    assert result["unjustified_abstentions"] == 1
    assert result["abstention_precision"] == round(1 / 2, 4)
    assert result["abstention_rate"] == round(2 / 3, 4)


def test_abstention_quality_handles_empty_input_without_crashing(tmp_path: Path) -> None:
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(ASSIGNMENTS_HEADER)
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(GT_HEADER)

    result = compute_abstention_quality(assignments_csv, gt_csv)

    assert result == {
        "abstentions": 0,
        "justified_abstentions": 0,
        "unjustified_abstentions": 0,
        "abstention_precision": 0.0,
        "abstention_rate": 0.0,
    }


def test_replay_consistency_identical_files_are_fully_stable(tmp_path: Path) -> None:
    content = (
        ASSIGNMENTS_HEADER
        + "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,1.0,AUTO_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
        + "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_2,0.9,LIKELY_MATCH,20000,20000,RZP002,RZP002,order_2,\n"
    )
    run_a = tmp_path / "run_a.csv"
    run_a.write_text(content)
    run_b = tmp_path / "run_b.csv"
    run_b.write_text(content)

    result = compute_replay_consistency(run_a, run_b)

    assert result["total_decisions"] == 2
    assert result["stable_decisions"] == 2
    assert result["changed_decisions"] == 0
    assert result["stability_rate"] == 1.0
    assert result["changed_examples"] == []


def test_replay_consistency_detects_a_single_changed_label(tmp_path: Path) -> None:
    """Same evidence, same two entities compared -- but run B flips pay_2's
    label from LIKELY_MATCH to AUTO_MATCH. That is exactly the instability
    Replay Consistency exists to catch, and it must be the only one
    reported."""
    run_a = tmp_path / "run_a.csv"
    run_a.write_text(
        ASSIGNMENTS_HEADER
        + "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,1.0,AUTO_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
        + "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_2,0.9,LIKELY_MATCH,20000,20000,RZP002,RZP002,order_2,\n"
    )
    run_b = tmp_path / "run_b.csv"
    run_b.write_text(
        ASSIGNMENTS_HEADER
        + "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,1.0,AUTO_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
        + "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_2,0.97,AUTO_MATCH,20000,20000,RZP002,RZP002,order_2,\n"
    )

    result = compute_replay_consistency(run_a, run_b)

    assert result["total_decisions"] == 2
    assert result["stable_decisions"] == 1
    assert result["changed_decisions"] == 1
    assert result["stability_rate"] == 0.5
    assert len(result["changed_examples"]) == 1
    example = result["changed_examples"][0]
    assert example["record_id"] == "rzp_norm_pay_2"
    assert example["run_a"] == {"label": "LIKELY_MATCH", "counterpart": "bank_norm_bank_2"}
    assert example["run_b"] == {"label": "AUTO_MATCH", "counterpart": "bank_norm_bank_2"}


def test_replay_consistency_handles_empty_input_without_crashing(tmp_path: Path) -> None:
    run_a = tmp_path / "run_a.csv"
    run_a.write_text(ASSIGNMENTS_HEADER)
    run_b = tmp_path / "run_b.csv"
    run_b.write_text(ASSIGNMENTS_HEADER)

    result = compute_replay_consistency(run_a, run_b)

    assert result["total_decisions"] == 0
    assert result["stable_decisions"] == 0
    assert result["changed_decisions"] == 0
    assert result["stability_rate"] == 1.0
    assert result["changed_examples"] == []
