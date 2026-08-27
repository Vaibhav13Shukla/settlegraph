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

    # Build ground truth lookup: razorpay_record_id -> expected bank record IDs
    gt_map: dict[str, list[str]] = {}
    for row in truth:
        rzp_id = row["razorpay_record_id"]
        bank_ids = row["true_bank_record_ids"].split("|")
        gt_map[rzp_id] = bank_ids

    # Build assignment lookup: razorpay entity_id -> matched bank record_id
    rzp_to_bank: dict[str, str] = {}
    for a in assignments:
        sources = {a["source_a"], a["source_b"]}
        if sources == {"razorpay", "bank"} and a["label"] == "AUTO_MATCH":
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

    for rzp_id, expected_bank_ids in gt_map.items():
        if rzp_id not in rzp_to_bank:
            false_negatives += 1
            # Find anomaly type
            for row in truth:
                if row["razorpay_record_id"] == rzp_id:
                    anomaly = row.get("anomaly_type", "none")
                    if anomaly not in anomaly_breakdown:
                        anomaly_breakdown[anomaly] = {"fn": 0, "fp": 0, "tp": 0}
                    anomaly_breakdown[anomaly]["fn"] += 1
                    break
        else:
            matched_bank = rzp_to_bank[rzp_id]
            if matched_bank in expected_bank_ids:
                true_positives += 1
                for row in truth:
                    if row["razorpay_record_id"] == rzp_id:
                        anomaly = row.get("anomaly_type", "none") or "none"
                        if anomaly not in anomaly_breakdown:
                            anomaly_breakdown[anomaly] = {"tp": 0, "fp": 0, "fn": 0}
                        anomaly_breakdown[anomaly]["tp"] += 1
                        break
            else:
                false_positives += 1
                for row in truth:
                    if row["razorpay_record_id"] == rzp_id:
                        anomaly = row.get("anomaly_type", "none") or "none"
                        if anomaly not in anomaly_breakdown:
                            anomaly_breakdown[anomaly] = {"tp": 0, "fp": 0, "fn": 0}
                        anomaly_breakdown[anomaly]["fp"] += 1
                        break

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

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "auto_matches": auto_matches,
        "exceptions": exceptions,
        "total_assignments": total,
        "exception_rate": round(exceptions / total, 4),
        "anomaly_breakdown": anomaly_breakdown,
    }
