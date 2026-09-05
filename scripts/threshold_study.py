"""Threshold study: does a lower `auto_match_threshold` buy recall for free?

Not a pytest test -- same standalone-script pattern as scripts/noise_sweep.py
and scripts/eval_holdout.py: generates data, runs the real pipeline several
times, and prints a table + a written recommendation for a human to read.

THE MEASURED WEAKNESS this study investigates (established elsewhere, not
re-derived here -- see DEVLOG Day 5 and `engine/calibration.py`'s own
docstrings): `compute_abstention_quality` reports abstention precision of
0.0164 on the development batch -- 120 of 122 abstentions held a candidate
that was already correct. The reliability bins show why: 101 assignments
scored at 0.749 confidence were 100% correct. Root cause is a 15-20 day
settlement delay that zeroes the date-proximity component of `score_edge`
even when UTR and amount match exactly, landing a genuinely correct match at
around 0.90 confidence -- just under the 0.95 `auto_match_threshold`. That
raises an obvious question: if the threshold were lower, would those
genuinely-correct-but-under-confident matches get auto-booked without ever
also auto-booking a wrong one?

THE METHODOLOGICAL CONSTRAINT this script exists to respect: the Track 04
brief says "Do not tune thresholds on the same corpus used to claim final
performance" and "You may inspect performance [on calibration], but don't
continuously tune on the final test dataset." So this sweep runs against the
`calibration` split ONLY -- never `test`, never `data/generated`. It writes
into an isolated temp workspace, exactly like eval_holdout.py and
noise_sweep.py, and never touches `data/generated`, `data/splits`, or
`results/`.

THE NON-NEGOTIABLE this study enforces in its own recommendation logic: this
project's zero-false-positive guarantee is not up for trade. A threshold that
gains recall by booking one wrong match is not a candidate, however good the
aggregate numbers look at that threshold. The recommendation section below
answers, in order: (1) what is the lowest threshold at which precision stays
exactly 100% and false positives stay at 0, (2) how much recall and
abstention-precision that threshold would gain versus today's 0.95, and (3)
whether the evidence supports changing the default -- and that any change
must be confirmed on `test` via `scripts/eval_holdout.py` first.

CRITICAL: this script's deliverable is evidence and a recommendation, not a
config edit. It NEVER writes to `src/settlegraph/config.py`. Changing that
file's default based on a calibration sweep, before held-out confirmation on
`test`, is exactly the methodological error this task exists to avoid -- and
this script's own printed output says so explicitly, every time it runs.

Usage:
    python scripts/threshold_study.py [--records 1000] [--seed 42]
        [--thresholds 0.80,0.85,0.88,0.90,0.92,0.95,0.97]
        [--json path/to/out.json] [--keep]

Exit code is always 0 (this is a study, not a gate) unless the run itself
raises -- an honest "keep 0.95" conclusion is exactly as valid an outcome as
a "lower it" one.
"""

from __future__ import annotations

import argparse
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
from settlegraph.engine.calibration import compute_abstention_quality  # noqa: E402
from settlegraph.engine.evaluate import evaluate as run_evaluate  # noqa: E402
from settlegraph.engine.pipeline import run_pipeline  # noqa: E402

# This is the ONLY split this script is allowed to touch. Not exposed as a
# CLI flag on purpose -- the whole point of this study is that nobody,
# including a future caller of this script, can point it at `test` by
# passing a flag. Confirming a recommendation on `test` is a separate,
# deliberate step via scripts/eval_holdout.py, not an accidental one here.
STUDY_SPLIT = "calibration"

DEFAULT_THRESHOLDS = "0.80,0.85,0.88,0.90,0.92,0.95,0.97"

TABLE_COLUMNS: tuple[tuple[str, int], ...] = (
    ("Threshold", 10),
    ("Precision%", 11),
    ("Recall%", 9),
    ("F1%", 8),
    ("TP", 6),
    ("FP", 5),
    ("FalseBook%", 11),
    ("DangerMiss%", 12),
    ("SafeAuto%", 10),
    ("Auto", 7),
    ("Likely", 7),
    ("Excep", 7),
    ("Abstain%", 9),
    ("AbstPrec", 9),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--records", type=int, default=1000)
    parser.add_argument("--thresholds", type=str, default=DEFAULT_THRESHOLDS)
    parser.add_argument("--json", dest="json_path", type=str, default=None)
    parser.add_argument("--keep", action="store_true", help="Don't delete the temp workspace")
    return parser.parse_args(argv)


def parse_thresholds(text: str) -> list[float]:
    """Parse a comma-separated `--thresholds` argument into a sorted, deduped
    float list. Mirrors scripts/noise_sweep.py's `parse_levels`."""
    thresholds = sorted({float(part) for part in text.split(",") if part.strip() != ""})
    if not thresholds:
        raise ValueError("--thresholds must name at least one threshold")
    return thresholds


# --------------------------------------------------------------------------
# Pure logic: building rows, formatting tables, and the recommendation.
# Kept free of any file I/O so it is directly unit-testable against
# synthetic metric dicts -- see tests/test_threshold_study.py.
# --------------------------------------------------------------------------


def build_threshold_row(
    threshold: float,
    evaluation: dict[str, Any],
    assignment_counts: dict[str, Any],
    abstention: dict[str, Any],
) -> dict[str, Any]:
    """Flatten one threshold's `evaluate()` output, assignment-label counts,
    and `compute_abstention_quality()` output into the single flat dict shape
    the table and the recommendation logic both consume."""
    total_rzp_bank = assignment_counts.get("total", 0)
    likely = assignment_counts.get("likely_match", 0)
    exceptions = assignment_counts.get("exception", 0)
    abstain_rate = abstention.get(
        "abstention_rate", ((likely + exceptions) / total_rzp_bank) if total_rzp_bank else 0.0
    )
    return {
        "threshold": threshold,
        "precision": evaluation.get("precision", 0.0),
        "recall": evaluation.get("recall", 0.0),
        "f1": evaluation.get("f1", 0.0),
        "true_positives": evaluation.get("true_positives", 0),
        "false_positives": evaluation.get("false_positives", 0),
        "false_auto_book_rate": evaluation.get("false_auto_book_rate", 0.0),
        "dangerous_miss_rate": evaluation.get("dangerous_miss_rate", 0.0),
        "safe_auto_resolution_rate": evaluation.get("safe_auto_resolution_rate", 0.0),
        "auto_count": assignment_counts.get("auto_match", 0),
        "likely_count": likely,
        "exception_count": exceptions,
        "abstention_rate": abstain_rate,
        "abstention_precision": abstention.get("abstention_precision", 0.0),
    }


def format_threshold_table(rows: list[dict[str, Any]]) -> str:
    """Render one row per swept threshold as a fixed-width ASCII table,
    sorted ascending by threshold. Pure and I/O-free -- unit-testable
    directly against synthetic row dicts."""
    sorted_rows = sorted(rows, key=lambda r: r["threshold"])
    header = "".join(name.rjust(width) for name, width in TABLE_COLUMNS)
    separator = "-" * len(header)
    lines = [header, separator]
    for row in sorted_rows:
        values = [
            f"{row['threshold']:.2f}",
            f"{row['precision'] * 100:.2f}",
            f"{row['recall'] * 100:.2f}",
            f"{row['f1'] * 100:.2f}",
            str(row["true_positives"]),
            str(row["false_positives"]),
            f"{row['false_auto_book_rate'] * 100:.2f}",
            f"{row['dangerous_miss_rate'] * 100:.2f}",
            f"{row['safe_auto_resolution_rate'] * 100:.2f}",
            str(row["auto_count"]),
            str(row["likely_count"]),
            str(row["exception_count"]),
            f"{row['abstention_rate'] * 100:.2f}",
            f"{row['abstention_precision']:.4f}",
        ]
        line = "".join(value.rjust(width) for value, (_, width) in zip(values, TABLE_COLUMNS))
        lines.append(line)
    return "\n".join(lines)


def recommend_threshold(rows: list[dict[str, Any]], current_threshold: float) -> dict[str, Any]:
    """Pure recommendation logic, answering the three questions this study
    exists to answer, in priority order:

    1. What is the LOWEST swept threshold at which precision is exactly
       100% AND false positives are exactly 0? The zero-false-positive
       constraint is non-negotiable -- a threshold is only a candidate at
       all if it clears both, regardless of how good its recall looks.
    2. How much recall and abstention-precision would that threshold gain
       versus `current_threshold` (only computed when `current_threshold`
       itself was one of the swept rows -- otherwise there is nothing to
       diff against, and the gain fields come back `None`)?
    3. Whether the evidence actually supports changing the default: true
       only when a threshold strictly lower than `current_threshold` both
       clears the zero-FP bar AND produces a measurable positive gain (more
       recall or better abstention precision) -- a lower threshold that is
       merely "just as safe" but buys nothing is not a reason to touch a
       working default, so a zero (or negative) gain keeps the current
       threshold exactly like a threshold that fails the safety bar does.
       Finding that `current_threshold` is itself already the lowest safe
       threshold, or that no lower threshold buys anything, is a legitimate
       "keep it" result, not a failure of this function.

    Deliberately I/O-free and dict-in/dict-out so it is directly unit
    testable against synthetic metric rows, independent of ever running the
    real pipeline.
    """
    if not rows:
        raise ValueError("no rows to analyze")

    sorted_rows = sorted(rows, key=lambda r: r["threshold"])
    zero_fp_rows = [r for r in sorted_rows if r["precision"] >= 1.0 and r["false_positives"] == 0]
    current_row = next(
        (r for r in sorted_rows if abs(r["threshold"] - current_threshold) < 1e-9), None
    )

    lowest_safe_row = zero_fp_rows[0] if zero_fp_rows else None

    recall_gain = None
    abstention_precision_gain = None
    if lowest_safe_row is not None and current_row is not None:
        recall_gain = round(lowest_safe_row["recall"] - current_row["recall"], 4)
        abstention_precision_gain = round(
            lowest_safe_row["abstention_precision"] - current_row["abstention_precision"], 4
        )

    change_supported_by_evidence = (
        lowest_safe_row is not None
        and current_row is not None
        and lowest_safe_row["threshold"] < current_row["threshold"]
    )

    if lowest_safe_row is None:
        reason = (
            "no swept threshold held precision at exactly 100% with zero false positives -- "
            "no threshold in this sweep is a safe candidate, including the current default if it "
            "was swept"
        )
    elif current_row is None:
        reason = (
            f"the current default ({current_threshold}) was not one of the swept thresholds, so "
            "no gain/keep comparison against it is available -- re-run with --thresholds including "
            "it"
        )
    elif change_supported_by_evidence:
        reason = (
            f"threshold {lowest_safe_row['threshold']} is lower than the current default "
            f"({current_row['threshold']}) and still holds precision at 100% with zero false "
            "positives across this calibration sweep"
        )
    else:
        reason = (
            f"the current default ({current_row['threshold']}) is already the lowest swept "
            "threshold that holds precision at 100% with zero false positives -- no lower "
            "threshold in this sweep is safe"
        )

    return {
        "lowest_safe_threshold": lowest_safe_row["threshold"] if lowest_safe_row else None,
        "lowest_safe_row": lowest_safe_row,
        "current_threshold": current_threshold,
        "current_row": current_row,
        "recall_gain": recall_gain,
        "abstention_precision_gain": abstention_precision_gain,
        "change_supported_by_evidence": change_supported_by_evidence,
        "reason": reason,
    }


def format_recommendation_section(recommendation: dict[str, Any]) -> str:
    """Pure formatting of `recommend_threshold`'s output as the
    RECOMMENDATION section, answering the three questions in priority order.
    Unit-testable on its own against a synthetic recommendation dict."""
    lines = ["=" * 70, "RECOMMENDATION", "=" * 70]

    lowest = recommendation["lowest_safe_threshold"]
    current = recommendation["current_threshold"]

    lines.append("1. Lowest threshold with precision == 100% and false positives == 0:")
    if lowest is None:
        lines.append("   NONE -- no swept threshold cleared the zero-false-positive bar.")
    else:
        lines.append(f"   {lowest} (current default: {current})")

    lines.append("")
    lines.append("2. Gain versus the current default:")
    if recommendation["recall_gain"] is None:
        lines.append("   Not computable -- the current default was not among the swept rows.")
    else:
        lines.append(
            f"   Recall:               {recommendation['recall_gain']:+.4f} "
            f"({recommendation['recall_gain'] * 100:+.2f}pp)"
        )
        lines.append(f"   Abstention precision: {recommendation['abstention_precision_gain']:+.4f}")

    lines.append("")
    lines.append("3. Does the evidence support changing the default?")
    if recommendation["change_supported_by_evidence"]:
        lines.append(f"   YES, on this calibration evidence: {recommendation['reason']}.")
    else:
        lines.append(f"   NO: {recommendation['reason']}.")
    lines.append(
        "   This recommendation was derived entirely on the 'calibration' split. Per the "
        "Track 04 brief's methodology rule, it must be CONFIRMED ON 'test' via "
        "scripts/eval_holdout.py BEFORE any default in src/settlegraph/config.py changes. "
        "This script never edits config.py -- it produces evidence and a recommendation, "
        "nothing else."
    )
    lines.append("=" * 70)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# I/O: generating data and running the pipeline at each threshold.
# --------------------------------------------------------------------------


def run_one_threshold(
    threshold: float, split_dir: Path, gt_path: Path, workspace: Path
) -> dict[str, Any]:
    """Run the real pipeline against the calibration split with
    `auto_match_threshold` set to `threshold`, score it with `evaluate()` and
    `compute_abstention_quality()`, and reduce the result to one flat
    comparison-table row.

    Each threshold gets its own output directory so one run's
    `assignments.csv` never overwrites another's before it's been scored.
    """
    output_dir = workspace / f"results_{threshold:g}"
    config = PipelineConfig(generated_data_directory=split_dir, auto_match_threshold=threshold)
    summary = run_pipeline(data_dir=split_dir, output_dir=output_dir, config=config)

    evaluation = summary.get("evaluation")
    if evaluation is None:
        # Ground truth missing at pipeline time -- fall back to a direct
        # evaluate() call against the assignments this run just wrote, so a
        # caller pointed at a split dir without ground_truth.csv doesn't
        # silently get an all-zero row.
        evaluation = run_evaluate(output_dir / "assignments.csv", gt_path)

    abstention = compute_abstention_quality(output_dir / "assignments.csv", gt_path)

    return build_threshold_row(threshold, evaluation, summary["assignments"], abstention)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    thresholds = parse_thresholds(args.thresholds)
    current_threshold = PipelineConfig().auto_match_threshold

    workspace = REPO_ROOT / ".threshold-study-tmp"
    if workspace.exists():
        shutil.rmtree(workspace)

    data_dir = workspace / "data_full"
    splits_dir = workspace / "splits"

    print(
        f"[1/3] Generating {args.records:,} records + splits (seed={args.seed})... "
        f"this study runs on the '{STUDY_SPLIT}' split ONLY -- never 'test', never "
        "data/generated."
    )
    t0 = time.perf_counter()
    SyntheticDataGenerator(seed=args.seed, output_dir=data_dir).write(
        args.records, split_output_dir=splits_dir
    )
    print(f"  Generated in {time.perf_counter() - t0:.1f}s")

    split_dir = splits_dir / STUDY_SPLIT
    gt_path = split_dir / "ground_truth.csv"
    if not gt_path.exists():
        print(f"[FAIL] Split directory {split_dir} has no ground_truth.csv -- nothing to sweep.")
        if not args.keep:
            shutil.rmtree(workspace, ignore_errors=True)
        return 1

    print(
        f"\n[2/3] Sweeping auto_match_threshold across {thresholds} on the "
        f"'{STUDY_SPLIT}' split (current default: {current_threshold})..."
    )
    rows: list[dict[str, Any]] = []
    for i, threshold in enumerate(thresholds, start=1):
        print(f"  [{i}/{len(thresholds)}] threshold={threshold}...")
        t0 = time.perf_counter()
        row = run_one_threshold(threshold, split_dir, gt_path, workspace)
        elapsed = time.perf_counter() - t0
        print(
            f"    done in {elapsed:.1f}s -- precision={row['precision'] * 100:.2f}% "
            f"recall={row['recall'] * 100:.2f}% FP={row['false_positives']}"
        )
        rows.append(row)

    print("\n[3/3] Building table and recommendation...")
    print()
    print(format_threshold_table(rows))
    print()

    recommendation = recommend_threshold(rows, current_threshold)
    print(format_recommendation_section(recommendation))

    if args.json_path:
        import json

        out_path = Path(args.json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "records": args.records,
                    "seed": args.seed,
                    "split": STUDY_SPLIT,
                    "thresholds": thresholds,
                    "current_default": current_threshold,
                    "rows": rows,
                    "recommendation": recommendation,
                },
                fh,
                indent=2,
            )
        print(f"\nWrote machine-readable results to {out_path}")

    if not args.keep:
        shutil.rmtree(workspace, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
