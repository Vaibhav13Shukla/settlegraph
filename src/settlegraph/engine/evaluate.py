"""Evaluation harness: measure reconciliation quality against ground truth."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def _load_ground_truth(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _load_assignments(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def count_correctly_flagged_for_review(
    assignment_path: Path, ground_truth_path: Path
) -> dict[str, int]:
    """How many `LIKELY_MATCH` assignments actually point at the right
    counterpart, held for review rather than auto-approved.

    Purely additive -- doesn't touch `evaluate()`'s existing return shape or
    change what "recall" means anywhere else in this codebase. Exists
    because of a real finding (see DEVLOG Day 5): a 15-20 day settlement
    delay zeroes the date-proximity scoring component even when UTR and
    amount both match exactly, landing a genuinely correct match at
    confidence 0.90 -- just under the 0.95 auto-match bar. `evaluate()`
    only credits AUTO_MATCH/AI_RESOLVED_MATCH as a true positive, which is
    the right definition for "recall" (this project has never wanted a
    fuzzy definition of that word), but it means a naive baseline with zero
    safety margin -- one that doesn't look at dates at all -- can score
    higher on raw recall while being strictly less safe. This number is
    what actually explains the gap: not lost information, correctly
    identified and conservatively held.
    """
    truth = _load_ground_truth(ground_truth_path)
    assignments = _load_assignments(assignment_path)

    gt_map: dict[str, list[str]] = {}
    for row in truth:
        gt_map[row["razorpay_record_id"]] = row["true_bank_record_ids"].split("|")

    correct = 0
    incorrect = 0
    for a in assignments:
        if a["label"] != "LIKELY_MATCH":
            continue
        sources = {a["source_a"], a["source_b"]}
        if sources != {"razorpay", "bank"}:
            continue
        if a["source_a"] == "razorpay":
            rzp_orig = a["source_a_id"].replace("rzp_norm_", "")
            bank_orig = a["source_b_id"].replace("bank_norm_", "")
        else:
            rzp_orig = a["source_b_id"].replace("rzp_norm_", "")
            bank_orig = a["source_a_id"].replace("bank_norm_", "")
        expected = gt_map.get(rzp_orig)
        if expected is None:
            continue
        if bank_orig in expected:
            correct += 1
        else:
            incorrect += 1

    return {"correct": correct, "incorrect": incorrect, "total": correct + incorrect}


def evaluate(assignment_path: Path, ground_truth_path: Path) -> dict[str, Any]:
    """Evaluate reconciliation results against hidden ground truth.

    Metrics:
    - Precision: fraction of AUTO_MATCH assignments that are correct
    - Recall: fraction of ground truth matches that were found
    - F1: harmonic mean of precision and recall
    - Per-anomaly-type breakdown
    - Exception rate: fraction of assignments flagged as EXCEPTION
    """
    truth = _load_ground_truth(ground_truth_path)
    assignments = _load_assignments(assignment_path)

    # Build ground truth lookup: razorpay_record_id -> expected bank record IDs.
    # An empty field must map to [], not [""] -- csv.DictReader gives back ""
    # for a blank cell, and "".split("|") is [""], not []. This only matters
    # for `relationship_type == "no_counterpart"` (Day 7): every other
    # relationship type has always had a real, non-empty
    # true_bank_record_ids, so this is a no-op for existing data shapes.
    gt_map: dict[str, list[str]] = {}
    for row in truth:
        rzp_id = row["razorpay_record_id"]
        raw = row["true_bank_record_ids"]
        gt_map[rzp_id] = raw.split("|") if raw else []

    # Build assignment lookup: razorpay entity_id -> matched bank record_id.
    # AI_RESOLVED_MATCH counts here alongside AUTO_MATCH -- both have already
    # cleared the same deterministic invariant gate by the time they reach
    # this label; the difference is *how the hypothesis was generated*, not
    # how strictly it was checked. `ai_assisted_matches` below keeps that
    # distinction visible in the metrics rather than erasing it.
    rzp_to_bank: dict[str, str] = {}
    ai_assisted_matches = 0
    matchable_labels = {"AUTO_MATCH", "AI_RESOLVED_MATCH"}
    for a in assignments:
        sources = {a["source_a"], a["source_b"]}
        if sources == {"razorpay", "bank"} and a["label"] in matchable_labels:
            if a["label"] == "AI_RESOLVED_MATCH":
                ai_assisted_matches += 1
            if a["source_a"] == "razorpay":
                rzp_orig = a["source_a_id"].replace("rzp_norm_", "")
                bank_orig = a["source_b_id"].replace("bank_norm_", "")
            else:
                rzp_orig = a["source_b_id"].replace("rzp_norm_", "")
                bank_orig = a["source_a_id"].replace("bank_norm_", "")
            rzp_to_bank[rzp_orig] = bank_orig

    # Compute metrics
    true_positives = 0
    false_positives = 0
    false_negatives = 0

    anomaly_breakdown: dict[str, dict[str, int]] = {}

    # Was an O(n) linear scan of `truth` per ground-truth record below (three
    # separate scans, one per branch) -- O(n^2) overall, right next to
    # `gt_map` above which already builds exactly this shape of dict in O(n).
    # At the 20,000-record stress-test scale this is unnecessary bookkeeping
    # cost. Found by code review. Built once, looked up per record instead;
    # the two `.get(..., "none")` call shapes below are kept exactly as they
    # were per branch (see tests/test_evaluate.py's
    # test_anomaly_breakdown_is_correct_across_tp_fp_and_fn for the pre-
    # existing "" vs "none" quirk this preserves rather than silently fixes).
    anomaly_by_id: dict[str, str] = {
        row["razorpay_record_id"]: row.get("anomaly_type", "none") for row in truth
    }

    # Dangerous Miss Rate / Exception Recall (Day 7): `no_counterpart` is the
    # one relationship_type where the honest ground-truth answer is "there is
    # no true match at all" -- a settlement that genuinely never reached the
    # bank. Leaving it unmatched is *correct*, not a miss, so it must not
    # inflate false_negatives or drag down recall (which is specifically
    # about records that DO have a true match to find). The dangerous
    # opposite -- the system confidently AUTO_MATCHing a no_counterpart
    # record onto some unrelated bank row -- is worse than an ordinary false
    # positive (the ledger now claims a settlement that never happened at
    # all), so it is still counted in false_positives via the existing
    # branch below (an empty expected_bank_ids list can never contain a
    # matched_bank id) *and* surfaced explicitly here.
    no_counterpart_total = 0
    dangerous_misses = 0
    correctly_abstained_no_counterpart = 0

    for rzp_id, expected_bank_ids in gt_map.items():
        is_no_counterpart = not expected_bank_ids
        if rzp_id not in rzp_to_bank:
            if is_no_counterpart:
                no_counterpart_total += 1
                correctly_abstained_no_counterpart += 1
                continue
            false_negatives += 1
            anomaly = anomaly_by_id.get(rzp_id, "none")
            if anomaly not in anomaly_breakdown:
                anomaly_breakdown[anomaly] = {"fn": 0, "fp": 0, "tp": 0}
            anomaly_breakdown[anomaly]["fn"] += 1
        else:
            matched_bank = rzp_to_bank[rzp_id]
            if matched_bank in expected_bank_ids:
                true_positives += 1
                anomaly = anomaly_by_id.get(rzp_id, "none") or "none"
                if anomaly not in anomaly_breakdown:
                    anomaly_breakdown[anomaly] = {"tp": 0, "fp": 0, "fn": 0}
                anomaly_breakdown[anomaly]["tp"] += 1
            else:
                false_positives += 1
                if is_no_counterpart:
                    no_counterpart_total += 1
                    dangerous_misses += 1
                anomaly = anomaly_by_id.get(rzp_id, "none") or "none"
                if anomaly not in anomaly_breakdown:
                    anomaly_breakdown[anomaly] = {"tp": 0, "fp": 0, "fn": 0}
                anomaly_breakdown[anomaly]["fp"] += 1

    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives) > 0
        else 0.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives) > 0
        else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Exception rate
    auto_matches = sum(1 for a in assignments if a["label"] == "AUTO_MATCH")
    exceptions = sum(1 for a in assignments if a["label"] == "EXCEPTION")
    total = len(assignments) if assignments else 1

    # Safe Auto-Resolution Rate / False Auto-Book Rate: the headline pair
    # from the Track 04 trust framing. Deliberately reuse true_positives/
    # false_positives above rather than re-deriving them -- those are
    # already "correct auto-booked" and "incorrect auto-booked" restricted
    # to matchable_labels (AUTO_MATCH + AI_RESOLVED_MATCH), just not named
    # for it yet. The denominator is total *ground-truth* records
    # (len(gt_map)), not total assignments -- an aggressive matcher that
    # auto-books everything can't inflate safe_auto_resolution_rate without
    # false_auto_book_rate rising to match, and a matcher that abstains on
    # everything can't hide from a falling safe_auto_resolution_rate. That
    # symmetry is the whole point of reporting the pair together.
    total_records = len(gt_map) if gt_map else 1

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "auto_matches": auto_matches,
        "ai_assisted_matches": ai_assisted_matches,
        "exceptions": exceptions,
        "total_assignments": total,
        "exception_rate": round(exceptions / total, 4),
        "anomaly_breakdown": anomaly_breakdown,
        # Aliases matching the Track 04 brief's own vocabulary verbatim
        # (Match Rate / Accuracy / False Match Rate), so the numbers on
        # screen don't need translating against the rubric they're graded
        # on. Same underlying values as precision/recall above -- Accuracy
        # here means "correct matches / total matches made" per the brief's
        # own definition, which for this evaluator is precision.
        "match_rate": round(recall, 4),
        "accuracy": round(precision, 4),
        "false_match_rate": round(false_positives / (true_positives + false_positives), 4)
        if (true_positives + false_positives) > 0
        else 0.0,
        # Tier-1 trust metrics (Track 04 framing): same tp/fp counts above,
        # denominated over every ground-truth record rather than only the
        # ones the system chose to act on.
        "total_records": len(gt_map),
        "correct_auto_matches": true_positives,
        "false_auto_matches": false_positives,
        "safe_auto_resolution_rate": round(true_positives / total_records, 4) if gt_map else 0.0,
        "false_auto_book_rate": round(false_positives / total_records, 4) if gt_map else 0.0,
        # Tier-2 safety metrics (Track 04 "calibrated abstention" framing).
        # Both are 0.0 when there are no no_counterpart records in this
        # batch to measure them against -- an undefined ratio reported as a
        # perfect score would overstate; reported as 0.0 it is at least a
        # visible "n=0" via no_counterpart_total rather than a silent lie.
        "no_counterpart_total": no_counterpart_total,
        "dangerous_misses": dangerous_misses,
        "dangerous_miss_rate": round(dangerous_misses / no_counterpart_total, 4)
        if no_counterpart_total
        else 0.0,
        "exception_recall": round(correctly_abstained_no_counterpart / no_counterpart_total, 4)
        if no_counterpart_total
        else 0.0,
    }
