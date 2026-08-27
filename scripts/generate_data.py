import argparse
from pathlib import Path

from datagen.generator import SyntheticDataGenerator


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate world-first SettleGraph data.")
    parser.add_argument("--total-records", type=int, default=1000)
    parser.add_argument("--anomaly-rate", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--split-output-dir", type=Path, default=Path("data/splits"))
    args = parser.parse_args()
    output = SyntheticDataGenerator(args.seed, args.anomaly_rate, args.output_dir).write(
        args.total_records,
        split_output_dir=args.split_output_dir,
    )
    print(f"Generated {args.total_records} realities into {output}")
    print(f"Wrote dataset splits into {args.split_output_dir}")


if __name__ == "__main__":
    main()
