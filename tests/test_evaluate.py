"""Tests for the evaluation harness."""

from __future__ import annotations

import tempfile
from pathlib import Path

from settlegraph.engine.evaluate import (
    count_correctly_flagged_for_review,
    evaluate,
    evaluate_secondary_legs,
)


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


def test_correctly_flagged_for_review_distinguishes_right_from_wrong_likely_matches(
    tmp_path: Path,
) -> None:
    """The finding this function exists for (DEVLOG Day 5): a LIKELY_MATCH
    can be pointing at the genuinely correct counterpart, just held below
    the auto-match confidence bar -- distinct from one that's actually
    wrong. Both must be counted, separately."""
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
        "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,0.90,LIKELY_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
        "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_99,0.80,LIKELY_MATCH,10000,10000,RZP002,RZP099,order_2,\n"
        "razorpay,rzp_norm_pay_3,bank,bank_norm_bank_3,0.98,AUTO_MATCH,10000,10000,RZP003,RZP003,order_3,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
        "pay_2,bank_2,led_2,exact_match,,\n"
        "pay_3,bank_3,led_3,exact_match,,\n"
    )

    result = count_correctly_flagged_for_review(assignments_csv, gt_csv)

    # pay_1's LIKELY_MATCH correctly points at bank_1 -> correct.
    # pay_2's LIKELY_MATCH points at bank_99, but ground truth says bank_2 -> incorrect.
    # pay_3 is AUTO_MATCH, not LIKELY_MATCH -- excluded entirely.
    assert result == {"correct": 1, "incorrect": 1, "total": 2}


def test_evaluate_reports_safe_auto_resolution_and_false_auto_book_rate(
    tmp_path: Path,
) -> None:
    """The headline pair the Track 04 trust framing calls out by name:
    Safe Auto-Resolution Rate (correct auto-booked / total records) and
    False Auto-Book Rate (incorrect auto-booked / total records). Both are
    counted over *all* ground-truth records, not just the ones the system
    chose to auto-match -- that's what makes the pair resistant to
    Goodharting by auto-matching aggressively (false_auto_book_rate rises
    to punish it) or abstaining on everything (safe_auto_resolution_rate
    falls to punish it).

    pay_1: AUTO_MATCH, points at the true counterpart -> correct auto-match.
    pay_2: AUTO_MATCH, points at the wrong bank record -> false auto-book.
    pay_3: EXCEPTION, correctly held -> counts toward neither rate's numerator,
           but still counts in the shared total_records denominator.
    """
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
        "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,0.98,AUTO_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
        "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_99,0.96,AUTO_MATCH,10000,10000,RZP002,RZP099,order_2,\n"
        "razorpay,rzp_norm_pay_3,bank,bank_norm_bank_3,0.30,EXCEPTION,10000,10000,RZP003,RZP003,order_3,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
        "pay_2,bank_2,led_2,exact_match,,\n"
        "pay_3,bank_3,led_3,exact_match,,\n"
    )

    results = evaluate(assignments_csv, gt_csv)

    assert results["total_records"] == 3
    assert results["correct_auto_matches"] == 1
    assert results["false_auto_matches"] == 1
    assert results["safe_auto_resolution_rate"] == round(1 / 3, 4)
    assert results["false_auto_book_rate"] == round(1 / 3, 4)


def test_anomaly_breakdown_is_correct_across_tp_fp_and_fn(tmp_path: Path) -> None:
    """Regression coverage for the anomaly-type lookup: was an O(n) scan of
    `truth` per ground-truth record (O(n^2) overall -- found by code review,
    real cost at the 20,000-record stress-test scale), replaced with a single
    dict built once. This pins the exact output first so the refactor is
    provably behavior-preserving, not just "probably fine."

    Also documents a pre-existing quirk, deliberately left as-is (narrowest
    change; recorded, not silently fixed): the false-negative branch uses
    `row.get("anomaly_type", "none")` with no `or "none"` fallback, while the
    true-positive/false-positive branches use `... or "none"`. A ground-truth
    row with an empty-string `anomaly_type` (the normal case for a clean,
    non-anomalous record in the generator's CSVs) therefore buckets under the
    key `""` when it's a false negative, but under `"none"` when it's a
    true/false positive -- two different dict keys for what should be one
    category. pay_2 below (normal record, false negative) demonstrates it.
    """
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
        "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,0.98,AUTO_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
        "razorpay,rzp_norm_pay_3,bank,bank_norm_bank_99,0.96,AUTO_MATCH,10000,10000,RZP003,RZP099,order_3,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
        "pay_2,bank_2,led_2,exact_match,,\n"
        "pay_3,bank_3,led_3,exact_match,utr_corruption,\n"
    )

    results = evaluate(assignments_csv, gt_csv)

    # pay_1: AUTO_MATCH -> bank_1, matches ground truth -> true positive, "none".
    # pay_2: no assignment at all -> false negative, empty-string anomaly_type
    #        (the fn branch's fallback quirk -- see docstring above).
    # pay_3: AUTO_MATCH -> bank_99, ground truth says bank_3 -> false positive, "utr_corruption".
    breakdown = results["anomaly_breakdown"]
    assert breakdown["none"] == {"tp": 1, "fp": 0, "fn": 0}
    assert breakdown[""] == {"fn": 1, "fp": 0, "tp": 0}
    assert breakdown["utr_corruption"] == {"tp": 0, "fp": 1, "fn": 0}
    assert results["true_positives"] == 1
    assert results["false_positives"] == 1
    assert results["false_negatives"] == 1


def test_no_counterpart_correctly_abstained_does_not_count_as_a_missed_match(
    tmp_path: Path,
) -> None:
    """A `no_counterpart` ground-truth record (Day 7: the generator now
    actually produces these -- a settlement that genuinely never reached
    the bank) has no true match to find. Leaving it unmatched is the
    *correct* answer, not a miss -- it must not inflate false_negatives or
    drag down recall, which is specifically about records that DO have a
    true match. Tracked instead as a correct abstention (`exception_recall`)."""
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
        "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,0.98,AUTO_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
        "pay_2,,led_2,no_counterpart,no_counterpart,\n"
    )

    results = evaluate(assignments_csv, gt_csv)

    # pay_1: correctly auto-matched -> the only thing recall's denominator
    # should be about.
    assert results["true_positives"] == 1
    assert results["false_negatives"] == 0
    assert results["recall"] == 1.0
    # pay_2: no assignment at all, correctly -- there was nothing to find.
    assert results["no_counterpart_total"] == 1
    assert results["dangerous_misses"] == 0
    assert results["dangerous_miss_rate"] == 0.0
    assert results["exception_recall"] == 1.0


def test_no_counterpart_dangerously_auto_matched_is_flagged(tmp_path: Path) -> None:
    """The failure mode this is actually for: a `no_counterpart` record --
    ground truth says there is no true bank counterpart at all -- somehow
    ends up AUTO_MATCH'd anyway (an unrelated bank row that happened to
    share amount/date/a candidate). This is more dangerous than an ordinary
    false positive: the ledger now claims a settlement that never
    happened. Already correctly counted as a false_positive by the existing
    tp/fp logic (a match against an empty expected-ids list is never a
    match); this test pins that it is *also* surfaced explicitly, not
    buried inside the general false-positive count."""
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
        "razorpay,rzp_norm_pay_2,bank,bank_norm_bank_99,0.96,AUTO_MATCH,10000,10000,RZP002,RZP099,order_2,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_2,,led_2,no_counterpart,no_counterpart,\n"
    )

    results = evaluate(assignments_csv, gt_csv)

    assert results["false_positives"] == 1
    assert results["no_counterpart_total"] == 1
    assert results["dangerous_misses"] == 1
    assert results["dangerous_miss_rate"] == 1.0
    assert results["exception_recall"] == 0.0
    # Not a real recall miss either way -- excluded from that denominator.
    assert results["false_negatives"] == 0


def test_secondary_legs_score_merchant_and_bank_merchant_matches(tmp_path: Path) -> None:
    """`evaluate()` only ever scored the razorpay<->bank leg. On a real
    1,000-record batch that left **1,283 of 2,106 auto-matches unmeasured**
    (990 razorpay<->merchant + 293 bank<->merchant) -- automatic financial
    decisions with no ground-truth check at all. Rated HIGH in
    `docs/RED_TEAM.md`; this closes it.

    Both legs are scoreable from the ground truth already on disk:
    - razorpay<->merchant directly, via `true_merchant_record_id`.
    - bank<->merchant by derivation: the pair is correct when some payment's
      ground truth points at both that bank record and that merchant record.
      This leg has no identifier signal of its own (`_score_bank_merchant`
      uses only amount and date), which is exactly why leaving it unmeasured
      was the risk.
    """
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
        # razorpay<->merchant: correct
        "razorpay,rzp_norm_pay_1,merchant,merch_norm_led_1,0.98,AUTO_MATCH,10000,10000,U1,,order_1,order_1\n"
        # razorpay<->merchant: wrong merchant
        "razorpay,rzp_norm_pay_2,merchant,merch_norm_led_99,0.96,AUTO_MATCH,10000,10000,U2,,order_2,order_2\n"
        # bank<->merchant: correct by derivation (pay_1 -> bank_1 and led_1)
        "bank,bank_norm_bank_1,merchant,merch_norm_led_1,1.0,AUTO_MATCH,10000,10000,U1,,,\n"
        # bank<->merchant: wrong (bank_1 belongs to pay_1, led_2 to pay_2)
        "bank,bank_norm_bank_1,merchant,merch_norm_led_2,1.0,AUTO_MATCH,10000,10000,U1,,,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
        "pay_2,bank_2,led_2,exact_match,,\n"
    )

    result = evaluate_secondary_legs(assignments_csv, gt_csv)

    merch = result["razorpay_merchant_leg"]
    assert merch["true_positives"] == 1
    assert merch["false_positives"] == 1
    assert merch["precision"] == 0.5

    bm = result["bank_merchant_leg"]
    assert bm["true_positives"] == 1
    assert bm["false_positives"] == 1
    assert bm["precision"] == 0.5


def test_secondary_legs_handle_empty_input_without_dividing_by_zero(
    tmp_path: Path,
) -> None:
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
    )

    result = evaluate_secondary_legs(assignments_csv, gt_csv)

    assert result["razorpay_merchant_leg"]["precision"] == 0.0
    assert result["bank_merchant_leg"]["precision"] == 0.0
    assert result["razorpay_merchant_leg"]["true_positives"] == 0


def test_evaluate_includes_secondary_leg_metrics(tmp_path: Path) -> None:
    """The legs must land in `evaluate()`'s own output, not a side channel --
    otherwise they stay out of `evaluation.json`, the audit report and the
    dashboard, which is how they went unmeasured in the first place."""
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
        "razorpay,rzp_norm_pay_1,merchant,merch_norm_led_1,0.98,AUTO_MATCH,10000,10000,U1,,order_1,order_1\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
    )

    results = evaluate(assignments_csv, gt_csv)

    assert results["razorpay_merchant_leg"]["true_positives"] == 1
    assert "bank_merchant_leg" in results


def test_metrics_default_sensibly_when_no_no_counterpart_records_exist(
    tmp_path: Path,
) -> None:
    assignments_csv = tmp_path / "assignments.csv"
    assignments_csv.write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label,a_amount_paise,b_amount_paise,a_utr,b_utr,a_order_id,b_order_id\n"
        "razorpay,rzp_norm_pay_1,bank,bank_norm_bank_1,0.98,AUTO_MATCH,10000,10000,RZP001,RZP001,order_1,\n"
    )
    gt_csv = tmp_path / "ground_truth.csv"
    gt_csv.write_text(
        "razorpay_record_id,true_bank_record_ids,true_merchant_record_id,relationship_type,anomaly_type,notes\n"
        "pay_1,bank_1,led_1,exact_match,,\n"
    )

    results = evaluate(assignments_csv, gt_csv)

    assert results["no_counterpart_total"] == 0
    assert results["dangerous_misses"] == 0
    assert results["dangerous_miss_rate"] == 0.0
    assert results["exception_recall"] == 0.0
