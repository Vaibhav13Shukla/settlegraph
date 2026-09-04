"""Stress test: generate a much larger adversarial batch and break it on purpose.

Not a pytest test -- this is deliberately a standalone script, the same
pattern as scripts/run_e2e.py, because it's meant to be run manually and read
by a human (or dropped straight into a DEVLOG/README table), not executed on
every CI push. It writes into an isolated temp directory, never touching the
real data/generated the CLI's `run`/`serve` commands use.

Usage:
    python scripts/stress_test.py [--records 20000] [--seed 7]

Exit code is 0 only if the zero-false-positive / zero-invariant-violation
guarantee held at scale. A recall drop is reported, not treated as failure --
this repo's whole position is that an honest exception beats a confident
false match, so a stress run that produces more exceptions under harder
adversity is doing exactly what it should.
"""

from __future__ import annotations

import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--anomaly-rate", type=float, default=0.15)
    parser.add_argument("--keep", action="store_true", help="Don't delete the temp workspace")
    args = parser.parse_args()

    workspace = REPO_ROOT / ".stress-tmp"
    data_dir = workspace / "data"
    results_dir = workspace / "results"
    if workspace.exists():
        shutil.rmtree(workspace)

    print(
        f"[1/3] Generating {args.records:,} adversarial financial realities "
        f"(anomaly_rate={args.anomaly_rate}, seed={args.seed})..."
    )
    t0 = time.perf_counter()
    SyntheticDataGenerator(
        seed=args.seed, anomaly_rate=args.anomaly_rate, output_dir=data_dir
    ).write(args.records)
    gen_elapsed = time.perf_counter() - t0
    print(f"  Generated in {gen_elapsed:.1f}s")

    print("[2/3] Running the full reconciliation pipeline at scale...")
    config = PipelineConfig(generated_data_directory=data_dir)
    summary = run_pipeline(data_dir=data_dir, output_dir=results_dir, config=config)

    print("[3/3] Checking the invariants that must hold regardless of scale...")
    ev = summary.get("evaluation", {})
    precision = ev.get("precision", 0.0)
    false_positives = ev.get("false_positives", -1)
    violations = summary.get("invariant_violations", -1)
    throughput = summary.get("throughput", {})

    ok = precision == 1.0 and false_positives == 0 and violations == 0

    print()
    print("=" * 60)
    print(f"  Records processed:      {throughput.get('records_processed', '?'):,}")
    print(f"  Elapsed:                {throughput.get('elapsed_seconds', '?')}s")
    print(f"  Throughput:             {throughput.get('records_per_second', '?')} records/sec")
    print(f"  Precision:              {precision * 100:.2f}%  (must be 100.00%)")
    print(f"  False positives:        {false_positives}  (must be 0)")
    print(f"  Invariant violations:   {violations}  (must be 0)")
    print(f"  Recall:                 {ev.get('recall', 0.0) * 100:.2f}%  (reported, not gated)")
    print(
        f"  Exception rate:         {ev.get('exception_rate', 0.0) * 100:.2f}%  (reported, not gated)"
    )
    print("=" * 60)
    print()

    if ok:
        print("[PASS] Zero-error invariant held at scale.")
    else:
        print("[FAIL] The zero-false-positive / zero-invariant-violation guarantee broke at scale.")

    if not args.keep:
        shutil.rmtree(workspace, ignore_errors=True)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
