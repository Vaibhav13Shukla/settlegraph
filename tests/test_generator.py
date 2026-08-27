from datagen.generator import SyntheticDataGenerator


def test_generator_creates_auditable_truth_and_source_views(tmp_path) -> None:
    output = tmp_path / "generated"
    generator = SyntheticDataGenerator(seed=42, anomaly_rate=0.15, output_dir=output)
    razorpay, bank, merchant, truth = generator.generate(1000)
    assert len(razorpay) == len(bank) == len(merchant) == len(truth) == 1000
    assert all(record.amount_paise == int(record.amount_paise) for record in razorpay)
    assert all(record.true_bank_record_ids for record in truth)
    generator.write(1000)
    assert (output / "razorpay_settlements.csv").exists()
    assert (output / "ground_truth.csv").exists()


def test_generator_is_deterministic(tmp_path) -> None:
    first = SyntheticDataGenerator(seed=7, output_dir=tmp_path / "one")
    second = SyntheticDataGenerator(seed=7, output_dir=tmp_path / "two")
    first.write(50)
    second.write(50)
    assert (tmp_path / "one" / "razorpay_settlements.csv").read_bytes() == (
        tmp_path / "two" / "razorpay_settlements.csv"
    ).read_bytes()
