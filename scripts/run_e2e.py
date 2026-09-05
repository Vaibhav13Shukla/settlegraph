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

    # The exact gates come first, because they are the ones that mean
    # something. A threshold gate can only catch a regression bigger than its
    # own slack; an equality gate on "zero false positives" catches the first
    # one. The recall floor used to sit at 0.75 against a measured 83.6%,
    # which would not have failed on a 7pp collapse -- decorative, and named
    # as such in docs/RED_TEAM.md (L-3). Tightened to 0.78: still clear of
    # the 81.8% observed on a different held-out seed, so it will not flap,
    # but it now actually bounds something.
    if precision < 1.0 or fp != 0:
        print(f"[FAIL] Invariant violation: Precision={precision}, False Positives={fp}")
        return 1

    dangerous_miss_rate = ev.get("dangerous_miss_rate", 0.0)
    if dangerous_miss_rate != 0.0:
        print(f"[FAIL] Dangerous miss rate must be zero: {dangerous_miss_rate}")
        return 1

    violations = summary.get("invariant_violations", -1)
    if violations != 0:
        print(f"[FAIL] Invariant violations detected: {violations}")
        return 1

    # The other two reconciliation legs. Until these were scored, 1,283 of
    # 2,106 automatic decisions per batch went unchecked (see
    # engine/evaluate.py::evaluate_secondary_legs). A false positive on any
    # leg is a corrupted ledger entry, so all three are gated identically.
    for leg_name in ("razorpay_merchant_leg", "bank_merchant_leg"):
        leg = ev.get(leg_name, {})
        leg_fp = leg.get("false_positives", 0)
        if leg_fp != 0:
            print(f"[FAIL] {leg_name} produced {leg_fp} false positive(s); must be zero")
            return 1

    if recall < 0.78:
        print(f"[FAIL] Throughput below threshold: Recall={recall}")
        return 1

    print(
        f"  [PASS] Precision: {precision * 100:.1f}%, Recall: {recall * 100:.1f}%, "
        f"False Positives: {fp}, Dangerous Misses: {dangerous_miss_rate * 100:.1f}%, "
        f"Invariant Violations: {violations}"
    )
    print(
        f"  [PASS] Secondary legs: rzp<->merchant fp="
        f"{ev.get('razorpay_merchant_leg', {}).get('false_positives', 0)}, "
        f"bank<->merchant fp={ev.get('bank_merchant_leg', {}).get('false_positives', 0)}"
    )

    failures = simulate_all_failures()
    print(f"[4/4] Executing {len(failures)}-scenario live failure injection containment check...")
    for f in failures:
        if not f.passed:
            print(f"  [FAIL] Scenario {f.scenario_id} ({f.name}) was NOT safely contained!")
            return 1
        print(f"  [PASS] {f.scenario_id}: {f.name} -> Contained")

    print("\n[OK] All production end-to-end checks PASSED cleanly.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
