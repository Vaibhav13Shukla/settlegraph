from datagen.generator import SyntheticDataGenerator


def test_generator_creates_auditable_truth_and_source_views(tmp_path) -> None:
    output = tmp_path / "generated"
    generator = SyntheticDataGenerator(seed=42, anomaly_rate=0.15, output_dir=output)
    razorpay, bank, merchant, truth = generator.generate(1000)
    assert len(razorpay) == len(merchant) == len(truth) == 1000
    # Bank can exceed 1000: a `split_settlement` anomaly renders one payment
    # as two separate bank credits, which is why this is `>=` rather than
    # `==`. Uniqueness matters more than count here.
    assert len(bank) >= 1000
    assert len({b.record_id for b in bank}) == len(bank)
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
