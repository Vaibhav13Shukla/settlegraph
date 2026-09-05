"""Held-out evaluation: score the real pipeline on a split it never saw.

Not a pytest test -- same pattern as scripts/noise_sweep.py and
scripts/stress_test.py: a standalone script meant to be run manually and
read by a human (or dropped straight into a DEVLOG/docs table), because it
generates data and runs the full pipeline several times. It writes into an
isolated temp workspace, never touching `data/generated`, `data/splits`, or
`results/` -- the directories the CLI's `generate`/`run` commands and every
number in `docs/EVALUATION.md` use.

The gap this closes: `datagen/generator.py::_write_splits` has produced
train/calibration/test/adversarial partitions since Day 6, and
`tests/test_splits.py` proves they are disjoint and leak-free -- but nothing
ever actually *ran the pipeline* against the held-out `test` split. Every
headline number this project has published came from `data/generated`, the
exact corpus development iterated against. That is precisely the "accidental
cheating" DATASET C in the brief's Definition of Done exists to prevent:
predictions must be produced with no visibility into `ground_truth_match_id`,
and only scored against it afterward, on a corpus the tuning loop never
touched.

This script:
  1. Generates a fresh dataset + splits into an isolated workspace, using a
     seed different from the development seed (42) by default -- the whole
     point is a corpus this system's thresholds, scoring formula, and
     generator quirks were never fitted against.
  2. Runs the real, unmodified `run_pipeline` against ONLY the chosen split's
     directory (default: `test`).
  3. Runs all three baselines (naive / amount+date / fuzzy) against the same
     split, scored by the identical `evaluate()` harness -- the same
     comparison `docs/EVALUATION.md` §2 runs, just on unseen data.
  4. Prints a comparison table, calibration + abstention-quality numbers for
     the SettleGraph run, and an explicit HELD-OUT VERDICT section comparing
     these numbers against the documented development-set numbers.

Usage:
    python scripts/eval_holdout.py [--records 1000] [--seed 90210]
        [--split test] [--json path/to/out.json] [--keep]

Exit code is 0 only if precision on the held-out split is exactly 100% and
zero invariant violations occurred. A precision drop or an invariant
violation on unseen data is reported loudly, with exit code 1 -- that is a
real finding (most likely overfitting to the generator), not a script bug.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from datagen.generator import SyntheticDataGenerator  # noqa: E402
from settlegraph.config import PipelineConfig  # noqa: E402
from settlegraph.engine.baseline import (  # noqa: E402
    run_amount_date_baseline,
    run_fuzzy_baseline,
    run_naive_baseline,
)
from settlegraph.engine.calibration import (  # noqa: E402
    compute_abstention_quality,
    compute_calibration,
)
from settlegraph.engine.evaluate import evaluate as run_evaluate  # noqa: E402
from settlegraph.engine.ingest import load_all  # noqa: E402
from settlegraph.engine.normalize import normalize_all  # noqa: E402
from settlegraph.engine.pipeline import run_pipeline  # noqa: E402

# Deliberately NOT 42 -- 42 is the development seed baked into
# `docs/EVALUATION.md`'s headline batch and every hand-run demo command in
# this repo. Defaulting to a different constant here is what actually makes
# this "a corpus development never saw" rather than a coincidence that
# depends on the caller remembering to pass --seed.
DEFAULT_HOLDOUT_SEED = 90210

SPLIT_CHOICES = ("train", "calibration", "test", "adversarial")

# The published development-set numbers this script's verdict is measured
# against -- copied verbatim from docs/EVALUATION.md §1 and §3 (batch: 1,000
# records, seed 42, anomaly_rate 0.15). These are constants, not recomputed
# here, because recomputing them from a locally-run dev batch would let this
# script's own generator drift silently redefine what "development
# performance" means; the number a reviewer can check against the committed
# doc is the more honest anchor.
DEV_REFERENCE: dict[str, float] = {
    "precision": 1.0,
    "recall": 0.8364,
    "f1": 0.9109,
    "safe_auto_resolution_rate": 0.8230,
    "false_auto_book_rate": 0.0,
    "abstention_precision": 0.0164,
}

# A recall gap this large (absolute percentage points) between development
# and held-out performance is "material" -- big enough that generator noise
# alone is an implausible explanation and overfitting to the dev corpus is
# the more likely one. Smaller than this, and normal sampling variation
# between two different-sized batches is the more honest explanation.
RECALL_DEGRADATION_DELTA = 0.05

BASELINE_ASSIGNMENT_FIELDS = (
    "source_a",
    "source_a_id",
    "source_b",
    "source_b_id",
    "confidence",
    "label",
    "a_amount_paise",
    "b_amount_paise",
    "a_utr",
    "b_utr",
    "a_order_id",
    "b_order_id",
)

TABLE_COLUMNS: tuple[tuple[str, int], ...] = (
    ("Approach", 22),
    ("Precision%", 11),
    ("Recall%", 9),
    ("F1%", 8),
    ("TP", 6),
    ("FP", 6),
    ("FalseBook%", 11),
    ("DangerMiss%", 12),
    ("ExcRecall%", 11),
    ("SafeAuto%", 10),
    ("Auto", 7),
    ("Likely", 7),
    ("Excep", 7),
    ("Rec/s", 9),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_HOLDOUT_SEED)
    parser.add_argument("--records", type=int, default=1000)
    parser.add_argument("--split", type=str, default="test", choices=SPLIT_CHOICES)
    parser.add_argument("--json", dest="json_path", type=str, default=None)
    parser.add_argument("--keep", action="store_true", help="Don't delete the temp workspace")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------
# Pure logic: building rows, formatting tables, and the verdict analysis.
# Kept free of any file I/O so it is directly unit-testable against
# synthetic metric dicts -- see tests/test_eval_holdout.py.
# --------------------------------------------------------------------------


def build_row(
    name: str,
    evaluation: dict[str, Any],
    auto_count: int,
    likely_count: int,
    exception_count: int,
    throughput_rps: float | None,
) -> dict[str, Any]:
    """Flatten one approach's `evaluate()` output plus assignment-label
    counts into the single flat dict shape the comparison table and the
    held-out verdict both consume."""
    return {
        "approach": name,
        "precision": evaluation.get("precision", 0.0),
        "recall": evaluation.get("recall", 0.0),
        "f1": evaluation.get("f1", 0.0),
        "true_positives": evaluation.get("true_positives", 0),
        "false_positives": evaluation.get("false_positives", 0),
        "false_auto_book_rate": evaluation.get("false_auto_book_rate", 0.0),
        "dangerous_miss_rate": evaluation.get("dangerous_miss_rate", 0.0),
        "exception_recall": evaluation.get("exception_recall", 0.0),
        "safe_auto_resolution_rate": evaluation.get("safe_auto_resolution_rate", 0.0),
        "auto_count": auto_count,
        "likely_count": likely_count,
        "exception_count": exception_count,
        "throughput_rps": throughput_rps,
    }


def format_comparison_table(rows: list[dict[str, Any]]) -> str:
    """Render one row per approach (SettleGraph + baselines) as a fixed-width
    ASCII table. Pure and I/O-free -- unit-testable directly against
    synthetic row dicts, same discipline as noise_sweep.py's format_table."""
    header = TABLE_COLUMNS[0][0].ljust(TABLE_COLUMNS[0][1]) + "".join(
        name.rjust(width) for name, width in TABLE_COLUMNS[1:]
    )
    separator = "-" * len(header)
    lines = [header, separator]
    for row in rows:
        throughput = row.get("throughput_rps")
        values = [
            f"{row['precision'] * 100:.2f}",
            f"{row['recall'] * 100:.2f}",
            f"{row['f1'] * 100:.2f}",
            str(row["true_positives"]),
            str(row["false_positives"]),
            f"{row['false_auto_book_rate'] * 100:.2f}",
            f"{row['dangerous_miss_rate'] * 100:.2f}",
            f"{row['exception_recall'] * 100:.2f}",
            f"{row['safe_auto_resolution_rate'] * 100:.2f}",
            str(row["auto_count"]),
            str(row["likely_count"]),
            str(row["exception_count"]),
            f"{throughput:.1f}" if throughput is not None else "?",
        ]
        approach = str(row["approach"])[: TABLE_COLUMNS[0][1]].ljust(TABLE_COLUMNS[0][1])
        rest = "".join(value.rjust(width) for value, (_, width) in zip(values, TABLE_COLUMNS[1:]))
        lines.append(approach + rest)
    return "\n".join(lines)


def format_calibration_section(calibration: dict[str, Any], abstention: dict[str, Any]) -> str:
    """Pure formatting of the calibration + abstention-quality numbers for
    the SettleGraph run on the held-out split. Takes the raw dicts returned
    by `compute_calibration` / `compute_abstention_quality` directly."""
    lines = ["=" * 70, "CALIBRATION + ABSTENTION QUALITY (SettleGraph, held-out split)", "=" * 70]
    lines.append(
        f"  Expected Calibration Error (ECE): {calibration.get('expected_calibration_error', 0.0):.4f}"
    )
    lines.append(f"  Brier score:                       {calibration.get('brier_score', 0.0):.4f}")
    lines.append(f"  Confidence-scored assignments:      {calibration.get('total_scored', 0)}")
    lines.append("")
    lines.append(f"  Abstentions (LIKELY_MATCH/EXCEPTION): {abstention.get('abstentions', 0)}")
    lines.append(
        f"  Justified (protected the books):      {abstention.get('justified_abstentions', 0)}"
    )
    lines.append(
        f"  Unjustified (held candidate was right): {abstention.get('unjustified_abstentions', 0)}"
    )
    lines.append(
        f"  Abstention precision:                 {abstention.get('abstention_precision', 0.0):.4f}"
    )
    lines.append(
        f"  Abstention rate:                      {abstention.get('abstention_rate', 0.0) * 100:.2f}%"
    )
    lines.append("=" * 70)
    return "\n".join(lines)


def analyze_holdout_verdict(
    holdout: dict[str, Any], dev_reference: dict[str, float] = DEV_REFERENCE
) -> dict[str, Any]:
    """Pure comparison of the held-out SettleGraph run against the published
    development-set numbers.

    `holdout` must carry at least `precision`, `recall`, and
    `false_auto_book_rate`; `abstention_precision` is optional (it comes from
    a separate `compute_abstention_quality` call, not `evaluate()`, so it may
    be omitted by a caller that only has evaluation metrics).

    Verdict is "OVERFITTING" -- loudly, not just "different" -- when either:
      - precision on held-out data is below 100% (this project's one
        non-negotiable invariant, per AGENTS.md and every doc), or
      - recall dropped by more than RECALL_DEGRADATION_DELTA absolute
        percentage points versus the documented development number, or
      - false_auto_book_rate on held-out data is above development's 0.00%
        (a false auto-book that never happened in development happening on
        unseen data is exactly the safety regression this script exists to
        catch).

    Otherwise "HEALTHY": performance held on data the system was never
    developed against.

    Deliberately I/O-free and dict-in/dict-out so it is directly unit
    testable against synthetic metric dicts, independent of ever running the
    real pipeline.
    """
    precision = holdout["precision"]
    recall = holdout["recall"]
    false_auto_book_rate = holdout.get("false_auto_book_rate", 0.0)
    abstention_precision = holdout.get("abstention_precision")

    precision_held = precision >= 1.0
    recall_delta = recall - dev_reference["recall"]
    recall_degraded = recall_delta < -RECALL_DEGRADATION_DELTA
    false_book_regressed = false_auto_book_rate > dev_reference.get("false_auto_book_rate", 0.0)

    abstention_precision_delta = None
    if abstention_precision is not None and "abstention_precision" in dev_reference:
        abstention_precision_delta = abstention_precision - dev_reference["abstention_precision"]

    reasons: list[str] = []
    if not precision_held:
        reasons.append(
            f"precision on held-out data is {precision * 100:.2f}%, below the 100% guarantee "
            f"documented for development (this is the single most important number in this "
            f"report -- it means the zero-false-positive property does NOT generalize to unseen "
            f"data)"
        )
    if recall_degraded:
        reasons.append(
            f"recall dropped {abs(recall_delta) * 100:.2f} percentage points versus development "
            f"({dev_reference['recall'] * 100:.2f}% -> {recall * 100:.2f}%), more than the "
            f"{RECALL_DEGRADATION_DELTA * 100:.0f}pp threshold for a material regression -- "
            f"consistent with overfitting to the development corpus"
        )
    if false_book_regressed:
        reasons.append(
            f"false_auto_book_rate on held-out data is {false_auto_book_rate * 100:.2f}%, above "
            f"development's {dev_reference.get('false_auto_book_rate', 0.0) * 100:.2f}% -- the "
            f"system incorrectly auto-booked something on unseen data that it never did in "
            f"development"
        )

    return {
        "precision_held": precision_held,
        "recall_delta": round(recall_delta, 4),
        "recall_degraded": recall_degraded,
        "false_book_regressed": false_book_regressed,
        "abstention_precision_delta": (
            round(abstention_precision_delta, 4) if abstention_precision_delta is not None else None
        ),
        "verdict": "OVERFITTING" if reasons else "HEALTHY",
        "reasons": reasons,
    }


def format_verdict_section(
    verdict: dict[str, Any],
    holdout: dict[str, Any],
    dev_reference: dict[str, float] = DEV_REFERENCE,
) -> str:
    """Pure formatting of `analyze_holdout_verdict`'s output as the
    HELD-OUT VERDICT section. Unit-testable on its own against a synthetic
    verdict + holdout dict pair."""
    lines = ["=" * 70, "HELD-OUT VERDICT", "=" * 70]

    if verdict["precision_held"]:
        lines.append(
            f"Precision held at 100.00% on data the system was never developed against "
            f"(development: {dev_reference['precision'] * 100:.2f}%)."
        )
    else:
        lines.append(
            f"Precision did NOT hold: {holdout['precision'] * 100:.2f}% on held-out data versus "
            f"{dev_reference['precision'] * 100:.2f}% claimed for development."
        )

    lines.append(
        f"Recall: {holdout['recall'] * 100:.2f}% held-out vs {dev_reference['recall'] * 100:.2f}% "
        f"development (delta {verdict['recall_delta'] * 100:+.2f}pp)."
    )
    lines.append(
        f"False auto-book rate: {holdout.get('false_auto_book_rate', 0.0) * 100:.2f}% held-out vs "
        f"{dev_reference.get('false_auto_book_rate', 0.0) * 100:.2f}% development."
    )
    if verdict["abstention_precision_delta"] is not None:
        lines.append(
            f"Abstention precision: {holdout.get('abstention_precision', 0.0):.4f} held-out vs "
            f"{dev_reference.get('abstention_precision', 0.0):.4f} development "
            f"(delta {verdict['abstention_precision_delta']:+.4f})."
        )

    lines.append("")
    if verdict["verdict"] == "HEALTHY":
        lines.append(
            "VERDICT: HEALTHY -- performance on this held-out split matches development within "
            "tolerance. No evidence of overfitting to the generator's development corpus."
        )
    else:
        lines.append(
            "VERDICT: OVERFITTING -- held-out performance is materially WORSE than the "
            "documented development-set numbers. This is the single most important finding "
            "this script can produce:"
        )
        for reason in verdict["reasons"]:
            lines.append(f"  - {reason}")
    lines.append("=" * 70)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# I/O: generating data, running the pipeline and baselines, writing a CSV.
# --------------------------------------------------------------------------


def _write_baseline_csv(path: Path, assignments: list[dict[str, Any]]) -> None:
    """Write a baseline's assignment list to CSV using a fixed header
    (`BASELINE_ASSIGNMENT_FIELDS`) regardless of whether any assignments were
    produced, so `evaluate()` always has a well-formed file to read -- an
    empty baseline result must not crash the comparison."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=list(BASELINE_ASSIGNMENT_FIELDS), extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(assignments)


def run_baseline_row(
    name: str,
    baseline_fn: Any,
    rzp_norm: list[Any],
    bank_norm: list[Any],
    merch_norm: list[Any],
    csv_path: Path,
    ground_truth_path: Path,
) -> dict[str, Any]:
    """Run one baseline function against already-normalized records, score
    it with the real `evaluate()` harness, and reduce the result to one flat
    comparison-table row."""
    t0 = time.perf_counter()
    assignments = baseline_fn(rzp_norm, bank_norm, merch_norm)
    elapsed = time.perf_counter() - t0

    _write_baseline_csv(csv_path, assignments)
    evaluation = run_evaluate(csv_path, ground_truth_path)

    total_input_records = len(rzp_norm) + len(bank_norm) + len(merch_norm)
    throughput = total_input_records / elapsed if elapsed > 0 else None

    auto_count = evaluation.get("auto_matches", 0)
    likely_count = sum(1 for a in assignments if a["label"] == "LIKELY_MATCH")
    exception_count = evaluation.get("exceptions", 0)

    return build_row(name, evaluation, auto_count, likely_count, exception_count, throughput)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    workspace = REPO_ROOT / ".eval-holdout-tmp"
    if workspace.exists():
        shutil.rmtree(workspace)

    data_dir = workspace / "data_full"
    splits_dir = workspace / "splits"
    results_dir = workspace / "results_settlegraph"

    print(
        f"[1/4] Generating {args.records:,} realities + splits "
        f"(seed={args.seed}, dev seed is 42 -- this is a corpus development never saw)..."
    )
    t0 = time.perf_counter()
    SyntheticDataGenerator(seed=args.seed, output_dir=data_dir).write(
        args.records, split_output_dir=splits_dir
    )
    print(f"  Generated in {time.perf_counter() - t0:.1f}s")

    split_dir = splits_dir / args.split
    gt_path = split_dir / "ground_truth.csv"
    if not gt_path.exists():
        print(f"[FAIL] Split directory {split_dir} has no ground_truth.csv -- nothing to evaluate.")
        if not args.keep:
            shutil.rmtree(workspace, ignore_errors=True)
        return 1

    with gt_path.open("r", encoding="utf-8") as fh:
        split_record_count = sum(1 for _ in csv.DictReader(fh))
    print(f"  Evaluating on '{args.split}' split: {split_record_count} ground-truth records")

    print(
        f"\n[2/4] Running the real pipeline against ONLY '{args.split}' -- never AUTO_MATCH-time "
        "visible ground truth..."
    )
    config = PipelineConfig(generated_data_directory=split_dir)
    summary = run_pipeline(data_dir=split_dir, output_dir=results_dir, config=config)
    evaluation = summary.get("evaluation")
    if evaluation is None:
        print("[FAIL] No evaluation produced -- ground truth missing at pipeline time.")
        if not args.keep:
            shutil.rmtree(workspace, ignore_errors=True)
        return 1

    settlegraph_row = build_row(
        "SettleGraph",
        evaluation,
        auto_count=summary["assignments"]["auto_match"],
        likely_count=summary["assignments"]["likely_match"],
        exception_count=summary["assignments"]["exception"],
        throughput_rps=summary["throughput"].get("records_per_second"),
    )

    print("\n[3/4] Running the three baselines against the identical held-out split...")
    rzp, bank, merchant = load_all(split_dir)
    rzp_norm, bank_norm, merch_norm = normalize_all(rzp, bank, merchant)

    baseline_rows = [
        run_baseline_row(
            "Baseline A (naive)",
            run_naive_baseline,
            rzp_norm,
            bank_norm,
            merch_norm,
            workspace / "baseline_a.csv",
            gt_path,
        ),
        run_baseline_row(
            "Baseline B (amt+date)",
            run_amount_date_baseline,
            rzp_norm,
            bank_norm,
            merch_norm,
            workspace / "baseline_b.csv",
            gt_path,
        ),
        run_baseline_row(
            "Baseline C (fuzzy)",
            run_fuzzy_baseline,
            rzp_norm,
            bank_norm,
            merch_norm,
            workspace / "baseline_c.csv",
            gt_path,
        ),
    ]

    print("\n[4/4] Calibration + abstention quality on the held-out split...")
    assignments_csv = results_dir / "assignments.csv"
    calibration = compute_calibration(assignments_csv, gt_path)
    abstention = compute_abstention_quality(assignments_csv, gt_path)

    print()
    print(format_comparison_table([settlegraph_row, *baseline_rows]))
    print()
    print(format_calibration_section(calibration, abstention))
    print()

    verdict_holdout = dict(settlegraph_row)
    verdict_holdout["abstention_precision"] = abstention.get("abstention_precision", 0.0)
    verdict = analyze_holdout_verdict(verdict_holdout, DEV_REFERENCE)
    print(format_verdict_section(verdict, verdict_holdout, DEV_REFERENCE))

    if args.json_path:
        out_path = Path(args.json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "seed": args.seed,
                    "records": args.records,
                    "split": args.split,
                    "split_record_count": split_record_count,
                    "settlegraph": settlegraph_row,
                    "baselines": baseline_rows,
                    "calibration": calibration,
                    "abstention": abstention,
                    "dev_reference": DEV_REFERENCE,
                    "verdict": verdict,
                    "invariant_violations": summary.get("invariant_violations"),
                },
                fh,
                indent=2,
            )
        print(f"\nWrote machine-readable results to {out_path}")

    if not args.keep:
        shutil.rmtree(workspace, ignore_errors=True)

    invariant_violations = summary.get("invariant_violations", -1)
    ok = settlegraph_row["precision"] >= 1.0 and invariant_violations == 0
    if not ok:
        print(
            f"\n[FAIL] Held-out precision={settlegraph_row['precision'] * 100:.2f}%, "
            f"invariant_violations={invariant_violations} -- the zero-false-positive guarantee "
            "did not hold on unseen data."
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
