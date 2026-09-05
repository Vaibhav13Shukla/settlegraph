"""Dataset split discipline.

The Buildathon brief is explicit that thresholds must not be tuned on the
same corpus used to claim final performance, and that a held-out set has to
be genuinely held out. `SyntheticDataGenerator._write_splits` has produced
train/calibration/test/adversarial directories since Day 2, but nothing has
ever asserted that the splits are actually disjoint -- and a split harness
that silently leaks is worse than no split harness at all, because it
produces a number everyone trusts and nobody has checked.

These tests are the check. The leakage assertions are the point; the rest is
characterization so the shape can't drift unnoticed.
"""

from __future__ import annotations

import csv
from pathlib import Path

from datagen.generator import SyntheticDataGenerator

SPLIT_NAMES = ("train", "calibration", "test", "adversarial")


def _read_ids(path: Path, column: str) -> list[str]:
    with path.open("r", encoding="utf-8") as fh:
        return [row[column] for row in csv.DictReader(fh)]


def _generate_splits(tmp_path: Path, records: int = 200, seed: int = 42) -> Path:
    split_dir = tmp_path / "splits"
    generator = SyntheticDataGenerator(seed=seed, anomaly_rate=0.15, output_dir=tmp_path / "gen")
    generator.write(records, split_output_dir=split_dir)
    return split_dir


def test_every_split_directory_is_written(tmp_path: Path) -> None:
    split_dir = _generate_splits(tmp_path)
    for name in SPLIT_NAMES:
        for filename in (
            "razorpay_settlements.csv",
            "bank_statements.csv",
            "merchant_ledger.csv",
            "ground_truth.csv",
        ):
            assert (split_dir / name / filename).exists(), f"{name}/{filename} missing"


def test_no_payment_appears_in_two_splits(tmp_path: Path) -> None:
    """The leakage check. A payment id in both `calibration` and `test`
    means any threshold tuned on calibration was tuned on test data too,
    and every held-out number downstream is contaminated."""
    split_dir = _generate_splits(tmp_path)

    seen: dict[str, str] = {}
    collisions: list[tuple[str, str, str]] = []
    for name in SPLIT_NAMES:
        for entity_id in _read_ids(split_dir / name / "razorpay_settlements.csv", "entity_id"):
            if entity_id in seen:
                collisions.append((entity_id, seen[entity_id], name))
            seen[entity_id] = name

    assert collisions == [], f"payment ids leaked across splits: {collisions[:5]}"


def test_splits_partition_the_whole_batch_without_loss(tmp_path: Path) -> None:
    """Disjoint is necessary but not sufficient -- splits that drop records
    on the floor would also be disjoint. Every generated payment must land
    in exactly one split."""
    records = 200
    split_dir = _generate_splits(tmp_path, records=records)

    total = 0
    for name in SPLIT_NAMES:
        total += len(_read_ids(split_dir / name / "razorpay_settlements.csv", "entity_id"))

    assert total == records


def test_ground_truth_travels_with_its_own_split(tmp_path: Path) -> None:
    """Each split's ground_truth.csv must describe that split's payments and
    no others -- otherwise an evaluation run against one split is silently
    scoring against another's answer key."""
    split_dir = _generate_splits(tmp_path)

    for name in SPLIT_NAMES:
        payment_ids = set(_read_ids(split_dir / name / "razorpay_settlements.csv", "entity_id"))
        truth_ids = set(_read_ids(split_dir / name / "ground_truth.csv", "razorpay_record_id"))
        assert truth_ids == payment_ids, (
            f"{name}: ground truth covers {len(truth_ids)} payments but the split has "
            f"{len(payment_ids)}"
        )


def test_split_membership_is_deterministic_for_a_seed(tmp_path: Path) -> None:
    """Reproducibility: the same seed must produce the same partition, or a
    'held-out' claim means nothing between two runs."""
    first = _generate_splits(tmp_path / "a", records=200, seed=7)
    second = _generate_splits(tmp_path / "b", records=200, seed=7)

    for name in SPLIT_NAMES:
        assert _read_ids(first / name / "razorpay_settlements.csv", "entity_id") == _read_ids(
            second / name / "razorpay_settlements.csv", "entity_id"
        )


def test_a_different_seed_produces_a_different_partition(tmp_path: Path) -> None:
    """The complement of the previous test: if every seed produced the same
    partition, the 'hidden' split would be hidden in name only."""
    first = _generate_splits(tmp_path / "a", records=200, seed=7)
    second = _generate_splits(tmp_path / "b", records=200, seed=8)

    assert _read_ids(first / "test" / "razorpay_settlements.csv", "entity_id") != _read_ids(
        second / "test" / "razorpay_settlements.csv", "entity_id"
    )
