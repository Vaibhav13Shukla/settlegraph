from datagen.generator import SyntheticDataGenerator


def test_generator_creates_auditable_truth_and_source_views(tmp_path) -> None:
    output = tmp_path / "generated"
    generator = SyntheticDataGenerator(seed=42, anomaly_rate=0.15, output_dir=output)
    razorpay, bank, merchant, truth = generator.generate(1000)
    assert len(razorpay) == len(merchant) == len(truth) == 1000
    assert len({b.record_id for b in bank}) == len(bank)
    # Bank count is no longer simply ">= 1000": `split_settlement` adds a
    # bank row per occurrence, `no_counterpart` removes one (a settlement
    # that genuinely never reached the bank -- see below). The exact count
    # is derivable from the two anomaly counts, a stronger assertion than
    # the old loose ">= 1000".
    split_count = sum(1 for r in truth if r.relationship_type == "split")
    no_counterpart_count = sum(1 for r in truth if r.relationship_type == "no_counterpart")
    assert len(bank) == 1000 - no_counterpart_count + split_count
    assert all(record.amount_paise == int(record.amount_paise) for record in razorpay)
    # Every record with a real bank counterpart must actually list one --
    # except `no_counterpart`, the one relationship_type where the ground
    # truth's honest answer is "there is nothing to find." This value has
    # existed on the model since Day 1 but was never actually generated
    # until now, which is exactly why Dangerous Miss Rate and Exception
    # Recall stayed unmeasurable (see DEVLOG).
    assert all(
        record.true_bank_record_ids
        for record in truth
        if record.relationship_type != "no_counterpart"
    )
    assert no_counterpart_count > 0, "no_counterpart must actually be generated, not just declared"
    assert all(
        record.true_bank_record_ids == []
        for record in truth
        if record.relationship_type == "no_counterpart"
    )
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
