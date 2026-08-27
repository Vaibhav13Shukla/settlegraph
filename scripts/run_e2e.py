import sys
from pathlib import Path

# Add repo root and src to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from datagen.generator import SyntheticDataGenerator  # noqa: E402
from settlegraph.config import PipelineConfig  # noqa: E402
from settlegraph.engine.pipeline import run_pipeline  # noqa: E402
from settlegraph.engine.simulator import simulate_all_failures  # noqa: E402


def main() -> int:
    print("[1/4] Generating synthetic financial dataset (1,000 realities)...")
    data_dir = Path("data/generated")
    results_dir = Path("results")

    gen = SyntheticDataGenerator(seed=42, anomaly_rate=0.15, output_dir=data_dir)
    gen.write(1000)

    print("[2/4] Executing SettleGraph reconciliation pipeline...")
    config = PipelineConfig(
        generated_data_directory=data_dir,
        auto_match_threshold=0.95,
        exception_threshold=0.70,
        amount_tolerance_paise=100,
        date_tolerance_days=3,
    )
    summary = run_pipeline(data_dir=data_dir, output_dir=results_dir, config=config)

    print("[3/4] Validating production invariants & evaluation metrics...")
    ev = summary.get("evaluation", {})
    precision = ev.get("precision", 0.0)
    recall = ev.get("recall", 0.0)
    fp = ev.get("false_positives", -1)

    if precision < 1.0 or fp != 0:
        print(f"[FAIL] Invariant violation: Precision={precision}, False Positives={fp}")
        return 1
    if recall < 0.75:
        print(f"[FAIL] Throughput below threshold: Recall={recall}")
        return 1

    print(
        f"  [PASS] Precision: {precision * 100:.1f}%, Recall: {recall * 100:.1f}%, False Positives: {fp}"
    )

    print("[4/4] Executing 5-scenario live failure injection containment check...")
    failures = simulate_all_failures()
    for f in failures:
        if not f.passed:
            print(f"  [FAIL] Scenario {f.scenario_id} ({f.name}) was NOT safely contained!")
            return 1
        print(f"  [PASS] {f.scenario_id}: {f.name} -> Contained")

    print("\n[OK] All production end-to-end checks PASSED cleanly.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
