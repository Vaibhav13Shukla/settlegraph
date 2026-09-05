"""Progressive-noise stress harness: find this system's breaking point.

Not a pytest test -- same pattern as scripts/stress_test.py: a standalone
script meant to be run manually and read by a human (or dropped straight
into a DEVLOG/README table). It writes into an isolated temp workspace,
never touching the real data/generated or results/ directories the CLI's
`run`/`serve` commands use.

Where scripts/stress_test.py asks "does the zero-false-positive guarantee
survive at scale?", this script asks a different question: "what happens as
the input data gets progressively noisier?" It sweeps
`SyntheticDataGenerator`'s `anomaly_rate` dial across several levels, runs
the full real pipeline at each level, and checks the one safety property
that actually matters for a system that claims to be trustworthy under
pressure:

    MORE NOISE -> LESS CONFIDENT AUTO-MATCHING -> MORE ABSTENTION
    -> PRECISION STAYS HIGH

If precision collapses, or if the system keeps auto-matching at the same
rate while abstention stays flat as noise climbs, that is a FAILURE of the
core trust property this project is built around -- and this script says so
loudly rather than treating a good-looking headline number as the whole
story.

Usage:
    python scripts/noise_sweep.py [--records 1000] [--seed 42]
        [--levels 0.0,0.05,0.10,0.15,0.20,0.30] [--json path/to/out.json]

Exit code is 0 only if the sweep's curve is HEALTHY (precision held and
abstention rose as noise increased). An UNHEALTHY curve is a real finding,
not a script bug -- it's reported with exit code 1 the same way
stress_test.py reports a broken invariant.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from datagen.generator import SyntheticDataGenerator  # noqa: E402
from settlegraph.config import PipelineConfig  # noqa: E402
from settlegraph.engine.pipeline import run_pipeline  # noqa: E402

DEFAULT_LEVELS = "0.0,0.05,0.10,0.15,0.20,0.30"

# Below this precision, the "precision stays high" half of the safety
# property is considered broken outright, not just nudged. 0.95 rather than
# 1.00 because a single percentage point of drift under adversarial noise is
# not the same failure mode as the system actively getting fooled at scale;
# the pipeline's own zero-false-positive invariant (see stress_test.py) is
# the stricter, separate guarantee that a healthy run is expected to hold
# regardless -- this threshold exists only to name a *collapse*, not any
# drop at all. The exact first-drop-below-100% level is reported separately
# below regardless of this threshold, so nothing is hidden by picking a
# lenient cutoff here.
PRECISION_COLLAPSE_THRESHOLD = 0.95

# Minimum absolute rise in abstention rate (LIKELY_MATCH / total
# assignments) between the lowest and highest tested noise level for the
# "abstention rose" half of the safety property to be considered satisfied.
# 1 percentage point is small enough that any real backing-off behavior
# clears it, and large enough that pure floating-point/labeling noise
# between two otherwise-identical runs can't accidentally pass.
ABSTENTION_RISE_MIN_DELTA = 0.01

TABLE_COLUMNS: tuple[tuple[str, int], ...] = (
    ("Noise", 7),
    ("Precision%", 11),
    ("Recall%", 9),
    ("F1%", 8),
    ("SafeAuto%", 10),
    ("FalseBook%", 11),
    ("DangerMiss%", 12),
    ("ExcRecall%", 11),
    ("Auto", 7),
    ("Likely", 8),
    ("Excep", 7),
    ("Abstain%", 9),
    ("Rec/s", 9),
)


def parse_levels(text: str) -> list[float]:
    """Parse a comma-separated `--levels` argument into a sorted float list."""
    levels = [float(part) for part in text.split(",") if part.strip() != ""]
    if not levels:
        raise ValueError("--levels must name at least one noise level")
    return sorted(levels)


def format_table(rows: list[dict]) -> str:
    """Render one row per noise level as a fixed-width ASCII table.

    Pure and I/O-free by design so it can be unit tested directly against
    synthetic rows, without generating data or running the pipeline.
    """
    sorted_rows = sorted(rows, key=lambda r: r["anomaly_rate"])
    header = "".join(name.rjust(width) for name, width in TABLE_COLUMNS)
    separator = "-" * len(header)
    lines = [header, separator]
    for row in sorted_rows:
        throughput = row.get("throughput_rps")
        values = [
            f"{row['anomaly_rate']:.2f}",
            f"{row['precision'] * 100:.2f}",
            f"{row['recall'] * 100:.2f}",
            f"{row['f1'] * 100:.2f}",
            f"{row['safe_auto_resolution_rate'] * 100:.2f}",
            f"{row['false_auto_book_rate'] * 100:.2f}",
            f"{row['dangerous_miss_rate'] * 100:.2f}",
            f"{row['exception_recall'] * 100:.2f}",
            str(row["auto_match"]),
            str(row["likely_match"]),
            str(row["exception"]),
            f"{row['abstention_rate'] * 100:.2f}",
            f"{throughput:.1f}" if throughput is not None else "?",
        ]
        line = "".join(value.rjust(width) for value, (_, width) in zip(values, TABLE_COLUMNS))
        lines.append(line)
    return "\n".join(lines)


def analyze_breaking_point(rows: list[dict]) -> dict:
    """Pure analysis of a noise sweep's rows against the healthy-curve property.

    Healthy: precision does not collapse (stays >= PRECISION_COLLAPSE_THRESHOLD
    at every tested noise level) AND abstention (LIKELY_MATCH / total
    assignments) rises meaningfully -- and more often than it falls -- as
    the noise level climbs from lowest to highest.

    Unhealthy otherwise: either precision degraded past the collapse
    threshold somewhere in the sweep, or the system kept auto-matching at
    roughly a flat rate while the underlying data quality got worse, which
    is exactly the dangerous failure mode this harness exists to catch.

    Deliberately I/O-free: takes and returns plain dicts/lists so it is
    directly unit-testable against synthetic metric rows, independent of
    ever running the real pipeline.
    """
    if not rows:
        raise ValueError("no rows to analyze")

    sorted_rows = sorted(rows, key=lambda r: r["anomaly_rate"])
    noise_levels = [r["anomaly_rate"] for r in sorted_rows]
    precisions = [r["precision"] for r in sorted_rows]
    abstentions = [r["abstention_rate"] for r in sorted_rows]

    first_precision_drop_level = next(
        (level for level, p in zip(noise_levels, precisions) if p < 1.0), None
    )
    min_precision = min(precisions)
    precision_collapsed = min_precision < PRECISION_COLLAPSE_THRESHOLD

    abstention_start = abstentions[0]
    abstention_end = abstentions[-1]
    deltas = [b - a for a, b in zip(abstentions, abstentions[1:])]
    increases = sum(1 for d in deltas if d > 1e-9)
    decreases = sum(1 for d in deltas if d < -1e-9)
    abstention_rose = (
        len(sorted_rows) > 1
        and (abstention_end - abstention_start) >= ABSTENTION_RISE_MIN_DELTA
        and decreases <= increases
    )

    reasons: list[str] = []
    if precision_collapsed:
        reasons.append(
            f"precision collapsed to {min_precision * 100:.2f}% "
            f"(below the {PRECISION_COLLAPSE_THRESHOLD * 100:.0f}% collapse threshold)"
        )
    if not abstention_rose:
        reasons.append(
            "abstention rate did not meaningfully rise as noise increased "
            f"({abstention_start * 100:.2f}% -> {abstention_end * 100:.2f}%); "
            "the system kept confidently auto-matching instead of backing off"
        )

    return {
        "first_precision_drop_level": first_precision_drop_level,
        "min_precision": min_precision,
        "precision_collapsed": precision_collapsed,
        "abstention_start": abstention_start,
        "abstention_end": abstention_end,
        "abstention_rose": abstention_rose,
        "verdict": "UNHEALTHY" if reasons else "HEALTHY",
        "reasons": reasons,
    }


def format_breaking_point_analysis(analysis: dict) -> str:
    """Render `analyze_breaking_point`'s output as the plain-language
    BREAKING POINT ANALYSIS section. Pure formatting, unit-testable on its
    own against a synthetic analysis dict."""
    lines = ["=" * 70, "BREAKING POINT ANALYSIS", "=" * 70]

    if analysis["first_precision_drop_level"] is None:
        lines.append("Precision never dropped below 100% across the tested noise levels.")
    else:
        lines.append(
            "Precision first drops below 100% at anomaly_rate = "
            f"{analysis['first_precision_drop_level']:.2f} "
            f"(minimum observed precision: {analysis['min_precision'] * 100:.2f}%)."
        )

    if analysis["abstention_rose"]:
        lines.append(
            "Abstention (LIKELY_MATCH) rate rose as noise increased: "
            f"{analysis['abstention_start'] * 100:.2f}% -> "
            f"{analysis['abstention_end'] * 100:.2f}%."
        )
    else:
        lines.append(
            "Abstention (LIKELY_MATCH) rate did NOT meaningfully rise as noise increased: "
            f"{analysis['abstention_start'] * 100:.2f}% -> "
            f"{analysis['abstention_end'] * 100:.2f}%."
        )

    lines.append("")
    if analysis["verdict"] == "HEALTHY":
        lines.append(
            "VERDICT: HEALTHY -- precision held and abstention rose as noise climbed. "
            "The system backs off into review rather than confidently auto-matching bad data."
        )
    else:
        lines.append("VERDICT: UNHEALTHY -- the safety property broke:")
        for reason in analysis["reasons"]:
            lines.append(f"  - {reason}")
    lines.append("=" * 70)
    return "\n".join(lines)


def run_one_level(records: int, seed: int, anomaly_rate: float, workspace_root: Path) -> dict:
    """Generate one noise level's dataset into an isolated subdirectory of
    `workspace_root`, run the full real pipeline against it, and reduce the
    resulting summary dict to one flat metrics row.

    Isolated the same way scripts/stress_test.py isolates itself: a
    dedicated temp directory tree, never `data/generated` or `results/`.
    """
    level_dir = workspace_root / f"level_{anomaly_rate:g}"
    data_dir = level_dir / "data"
    results_dir = level_dir / "results"

    SyntheticDataGenerator(seed=seed, anomaly_rate=anomaly_rate, output_dir=data_dir).write(records)

    config = PipelineConfig(generated_data_directory=data_dir)
    summary = run_pipeline(data_dir=data_dir, output_dir=results_dir, config=config)

    evaluation = summary.get("evaluation", {})
    assignments = summary.get("assignments", {})
    throughput = summary.get("throughput", {})

    total_assignments = assignments.get("total", 0)
    likely_match = assignments.get("likely_match", 0)
    abstention_rate = (likely_match / total_assignments) if total_assignments else 0.0

    return {
        "anomaly_rate": anomaly_rate,
        "precision": evaluation.get("precision", 0.0),
        "recall": evaluation.get("recall", 0.0),
        "f1": evaluation.get("f1", 0.0),
        "safe_auto_resolution_rate": evaluation.get("safe_auto_resolution_rate", 0.0),
        "false_auto_book_rate": evaluation.get("false_auto_book_rate", 0.0),
        "dangerous_miss_rate": evaluation.get("dangerous_miss_rate", 0.0),
        "exception_recall": evaluation.get("exception_recall", 0.0),
        "auto_match": assignments.get("auto_match", 0),
        "likely_match": likely_match,
        "exception": assignments.get("exception", 0),
        "abstention_rate": abstention_rate,
        "throughput_rps": throughput.get("records_per_second"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--levels", type=str, default=DEFAULT_LEVELS)
    parser.add_argument("--records", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", dest="json_path", type=str, default=None)
    parser.add_argument("--keep", action="store_true", help="Don't delete the temp workspace")
    args = parser.parse_args()

    levels = parse_levels(args.levels)

    workspace = REPO_ROOT / ".noise-sweep-tmp"
    if workspace.exists():
        shutil.rmtree(workspace)

    rows: list[dict] = []
    for i, level in enumerate(levels, start=1):
        print(
            f"[{i}/{len(levels)}] anomaly_rate={level:g} "
            f"(records={args.records:,}, seed={args.seed})..."
        )
        t0 = time.perf_counter()
        row = run_one_level(args.records, args.seed, level, workspace)
        elapsed = time.perf_counter() - t0
        print(
            f"  done in {elapsed:.1f}s -- precision={row['precision'] * 100:.2f}% "
            f"abstention={row['abstention_rate'] * 100:.2f}%"
        )
        rows.append(row)

    print()
    print(format_table(rows))
    print()

    analysis = analyze_breaking_point(rows)
    print(format_breaking_point_analysis(analysis))

    if args.json_path:
        out_path = Path(args.json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(
                {
                    "records": args.records,
                    "seed": args.seed,
                    "levels": levels,
                    "rows": rows,
                    "analysis": analysis,
                },
                fh,
                indent=2,
            )
        print(f"\nWrote machine-readable results to {out_path}")

    if not args.keep:
        shutil.rmtree(workspace, ignore_errors=True)

    return 0 if analysis["verdict"] == "HEALTHY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
