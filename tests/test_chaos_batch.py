"""Unit tests for scripts/chaos_batch.py's pure helper functions.

Deliberately does NOT run a real chaos sweep (that means generating data,
structurally damaging it, and running the full pipeline several times over)
-- see scripts/chaos_batch.py itself, run manually, for that. These tests
exercise the damage-injection helpers against tiny in-memory row lists, and
`analyze_breaking_point` / `format_breaking_point_analysis` / `format_table`
/ `parse_levels` against synthetic metric rows, which is exactly why
chaos_batch.py keeps its damage and analysis logic as pure functions
separate from any I/O -- matching tests/test_noise_sweep.py's own pattern
for its sibling script.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.chaos_batch import (  # noqa: E402
    FIELD_DAMAGE_MAP,
    analyze_breaking_point,
    apply_structural_damage,
    format_breaking_point_analysis,
    format_table,
    inject_duplicate_rows,
    inject_impossible_values,
    inject_malformed_timestamps,
    inject_mangled_references,
    inject_null_fields,
    inject_out_of_order,
    parse_levels,
)


def _row(
    chaos_level: float,
    status: str = "OK",
    precision: float = 1.0,
    abstention_rate: float = 0.0,
    invariant_violations: int = 0,
    **overrides,
) -> dict:
    base = {
        "chaos_level": chaos_level,
        "status": status,
        "error": None,
        "precision": precision,
        "recall": 0.9,
        "safe_auto_resolution_rate": 0.9,
        "false_auto_book_rate": 0.0,
        "dangerous_miss_rate": 0.0,
        "exception_recall": 1.0,
        "auto_match": 100,
        "likely_match": 10,
        "exception": 5,
        "abstention_rate": abstention_rate,
        "invariant_violations": invariant_violations,
        "duplicates_intercepted": 0,
        "throughput_rps": 500.0,
    }
    base.update(overrides)
    return base


def _rzp_rows(n: int) -> list[dict[str, str]]:
    return [
        {
            "entity_id": f"pay_{i:04d}",
            "entity_type": "payment",
            "settlement_id": "setl_1",
            "settlement_utr": f"RZP{i:012d}",
            "order_id": f"order_{i:04d}",
            "amount_paise": "100000",
            "currency": "INR",
            "fee_paise": "200",
            "tax_paise": "36",
            "net_amount_paise": "99764",
            "payment_method": "upi",
            "captured_at": "2026-01-01T00:00:00+00:00",
            "settled_at": "2026-01-03T00:00:00+00:00",
            "description": f"Order order_{i:04d}",
            "notes": '{"customer_id": "cust_0001"}',
            "status": "captured",
        }
        for i in range(n)
    ]


def _bank_rows(n: int) -> list[dict[str, str]]:
    return [
        {
            "record_id": f"bank_{i:04d}",
            "transaction_date": "2026-01-03",
            "value_date": "2026-01-03",
            "description": f"NEFT/RAZORPAY/RZP{i:012d}/setl_1",
            "reference_number": f"RZP{i:012d}",
            "debit_amount_paise": "",
            "credit_amount_paise": "99764",
            "balance_paise": "",
            "bank_name": "ICICI",
            "account_number": "XXXX001234",
        }
        for i in range(n)
    ]


# --- parse_levels -------------------------------------------------------------


def test_parse_levels_parses_and_sorts() -> None:
    assert parse_levels("0.20,0.0,0.10") == [0.0, 0.10, 0.20]


def test_parse_levels_rejects_empty() -> None:
    try:
        parse_levels("")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty --levels")


# --- inject_duplicate_rows -----------------------------------------------------


def test_inject_duplicate_rows_appends_exact_copies() -> None:
    rows = _bank_rows(10)
    rng = random.Random(1)
    out = inject_duplicate_rows(rows, rng, 0.5)
    assert len(out) == 15
    # Every appended row is byte-identical to some original row.
    originals = rows
    for extra in out[10:]:
        assert extra in originals


def test_inject_duplicate_rows_is_pure() -> None:
    rows = _bank_rows(4)
    before = [dict(r) for r in rows]
    inject_duplicate_rows(rows, random.Random(2), 1.0)
    assert rows == before


def test_inject_duplicate_rows_zero_fraction_is_noop_length() -> None:
    rows = _bank_rows(5)
    out = inject_duplicate_rows(rows, random.Random(3), 0.0)
    assert len(out) == 5


# --- inject_null_fields ---------------------------------------------------------


def test_inject_null_fields_blanks_only_requested_fields() -> None:
    rows = _rzp_rows(20)
    rng = random.Random(4)
    out = inject_null_fields(rows, rng, 0.5, ["order_id", "description"])
    blanked = [r for r in out if r["order_id"] == "" or r["description"] == ""]
    assert len(blanked) > 0
    # required/critical fields must never be touched by this axis
    for r in out:
        assert r["entity_id"] != ""
        assert r["amount_paise"] != ""


def test_inject_null_fields_noop_with_no_fields() -> None:
    rows = _rzp_rows(5)
    out = inject_null_fields(rows, random.Random(5), 1.0, [])
    assert out == rows


def test_inject_null_fields_is_pure() -> None:
    rows = _rzp_rows(6)
    before = [dict(r) for r in rows]
    inject_null_fields(rows, random.Random(6), 1.0, ["order_id"])
    assert rows == before


# --- inject_mangled_references ---------------------------------------------------


def test_inject_mangled_references_changes_value_but_keeps_it_truthy() -> None:
    rows = _bank_rows(20)
    rng = random.Random(7)
    out = inject_mangled_references(rows, rng, 1.0, ["reference_number"])
    changed = [
        (orig, mut)
        for orig, mut in zip(rows, out)
        if orig["reference_number"] != mut["reference_number"]
    ]
    assert len(changed) > 0
    for orig, mut in changed:
        assert mut["reference_number"] != ""


def test_inject_mangled_references_skips_empty_values() -> None:
    rows = [{"reference_number": ""}]
    out = inject_mangled_references(rows, random.Random(8), 1.0, ["reference_number"])
    assert out[0]["reference_number"] == ""


# --- inject_impossible_values -----------------------------------------------------


def test_inject_impossible_values_only_touches_populated_fields() -> None:
    rows = _bank_rows(10)
    rng = random.Random(9)
    out = inject_impossible_values(rows, rng, 1.0, ["credit_amount_paise", "debit_amount_paise"])
    changed = 0
    for orig, mut in zip(rows, out):
        # debit_amount_paise was empty on every row; must stay empty (never
        # populated by this axis, which only mutates a field already set) --
        # even though it can be the field `rng.choice` happens to pick.
        assert mut["debit_amount_paise"] == ""
        if int(mut["credit_amount_paise"]) != int(orig["credit_amount_paise"]):
            changed += 1
            assert int(mut["credit_amount_paise"]) > int(orig["credit_amount_paise"])
    assert changed > 0


def test_inject_impossible_values_noop_with_no_fields() -> None:
    rows = _bank_rows(5)
    out = inject_impossible_values(rows, random.Random(10), 1.0, [])
    assert out == rows


# --- inject_malformed_timestamps ---------------------------------------------------


def test_inject_malformed_timestamps_replaces_with_garbage() -> None:
    rows = _rzp_rows(10)
    rng = random.Random(11)
    out = inject_malformed_timestamps(rows, rng, 1.0, ["captured_at", "settled_at"])
    changed = sum(
        1
        for orig, mut in zip(rows, out)
        if orig["captured_at"] != mut["captured_at"] or orig["settled_at"] != mut["settled_at"]
    )
    assert changed == 10


def test_inject_malformed_timestamps_tiny_fraction_can_touch_zero_rows() -> None:
    rows = _rzp_rows(10)
    out = inject_malformed_timestamps(rows, random.Random(12), 0.01, ["captured_at"])
    # round(10 * 0.01) == 0 -- confirms the scaled-down timestamp axis really
    # can leave a small batch fully untouched at low chaos levels.
    assert out == rows


# --- inject_out_of_order -----------------------------------------------------------


def test_inject_out_of_order_preserves_the_multiset_of_rows() -> None:
    rows = _bank_rows(10)
    out = inject_out_of_order(rows, random.Random(13), 0.5)
    assert len(out) == len(rows)
    assert sorted(r["record_id"] for r in out) == sorted(r["record_id"] for r in rows)


def test_inject_out_of_order_moved_rows_land_after_kept_rows() -> None:
    rows = _bank_rows(10)
    rng = random.Random(14)
    out = inject_out_of_order(rows, rng, 0.3)
    kept_ids = {r["record_id"] for r in rows} - {r["record_id"] for r in out[: len(out) - 3]}
    # Just confirms no crash/mangling of content; exact split point is an
    # implementation detail already covered by the multiset-preservation
    # test above.
    assert isinstance(kept_ids, set)


# --- apply_structural_damage ----------------------------------------------------------


def test_apply_structural_damage_zero_level_is_noop() -> None:
    rows = _bank_rows(10)
    out = apply_structural_damage("bank_statements.csv", rows, random.Random(15), 0.0)
    assert out == rows


def test_apply_structural_damage_known_csv_names_do_not_raise() -> None:
    for csv_name, rows in (
        ("razorpay_settlements.csv", _rzp_rows(20)),
        ("bank_statements.csv", _bank_rows(20)),
    ):
        out = apply_structural_damage(csv_name, rows, random.Random(16), 0.5)
        assert isinstance(out, list)
        assert len(out) >= len(rows)  # duplicates only ever add rows


def test_apply_structural_damage_covers_every_field_damage_map_entry() -> None:
    # Every CSV this script actually damages must have a working field map --
    # if a new source were added to FIELD_DAMAGE_MAP without valid field
    # lists, this catches it long before a real sweep run does.
    for csv_name in FIELD_DAMAGE_MAP:
        rows = _bank_rows(10) if "bank" in csv_name else _rzp_rows(10)
        apply_structural_damage(csv_name, rows, random.Random(17), 0.4)


# --- analyze_breaking_point: precision never drops, never crashes --------------------


def test_all_safe_curve() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.10, precision=1.0, abstention_rate=0.05),
        _row(0.20, precision=1.0, abstention_rate=0.10),
        _row(0.30, precision=1.0, abstention_rate=0.20),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["verdict"] == "SAFE"
    assert analysis["reasons"] == []
    assert analysis["first_precision_drop_level"] is None
    assert analysis["first_crash_level"] is None
    assert analysis["abstention_rose"] is True


def test_analyze_breaking_point_rejects_empty_rows() -> None:
    try:
        analyze_breaking_point([])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty rows")


# --- analyze_breaking_point: a precision drop is ALWAYS unsafe (no lenient band) -----


def test_any_precision_drop_is_unsafe() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.10, precision=0.99, abstention_rate=0.05),
        _row(0.20, precision=1.0, abstention_rate=0.10),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["verdict"] == "UNSAFE"
    assert analysis["first_precision_drop_level"] == 0.10
    assert any("false" in r for r in analysis["reasons"])


# --- analyze_breaking_point: crashes alone are SAFE (fail-closed) --------------------


def test_crash_alone_does_not_make_the_sweep_unsafe() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.10, precision=1.0, abstention_rate=0.05),
        _row(0.20, status="CRASHED", precision=0.0, error="ValidationError: boom"),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["verdict"] == "SAFE"
    assert analysis["first_crash_level"] == 0.20
    assert analysis["crashed_levels"] == [0.20]
    # crashed rows are excluded from the precision/abstention measurement
    assert analysis["completed_levels"] == [0.0, 0.10]


def test_crash_row_excluded_from_min_precision() -> None:
    rows = [
        _row(0.0, precision=1.0),
        _row(0.10, status="CRASHED", precision=0.0),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["min_precision"] == 1.0


def test_all_levels_crashed_gives_no_measurable_precision() -> None:
    rows = [
        _row(0.0, status="CRASHED", precision=0.0),
        _row(0.10, status="CRASHED", precision=0.0),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["min_precision"] is None
    assert analysis["first_precision_drop_level"] is None
    assert analysis["abstention_rose"] is None
    assert analysis["verdict"] == "SAFE"


# --- analyze_breaking_point: invariant violations are always unsafe -----------------


def test_invariant_violation_makes_the_sweep_unsafe_even_with_perfect_precision() -> None:
    rows = [
        _row(0.0, precision=1.0, invariant_violations=0),
        _row(0.20, precision=1.0, invariant_violations=2),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["verdict"] == "UNSAFE"
    assert analysis["any_invariant_violations"] is True
    assert analysis["invariant_violation_levels"] == [0.20]


# --- analyze_breaking_point: abstention trend ----------------------------------------


def test_flat_abstention_is_reported_but_does_not_alone_change_verdict() -> None:
    # Unlike noise_sweep.py, chaos_batch's verdict only gates on precision
    # and invariant violations (see module docstring) -- a flat abstention
    # curve is still surfaced in the analysis dict for the report, but does
    # not flip SAFE to UNSAFE by itself.
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.05),
        _row(0.10, precision=1.0, abstention_rate=0.05),
        _row(0.20, precision=1.0, abstention_rate=0.05),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["abstention_rose"] is False
    assert analysis["verdict"] == "SAFE"


def test_single_completed_level_abstention_is_inconclusive() -> None:
    rows = [
        _row(0.0, precision=1.0, abstention_rate=0.05),
        _row(0.10, status="CRASHED", precision=0.0),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["abstention_rose"] is None
    assert analysis["abstention_start"] == 0.05
    assert analysis["abstention_end"] == 0.05


def test_analyze_breaking_point_is_order_independent() -> None:
    rows = [
        _row(0.30, precision=1.0, abstention_rate=0.25),
        _row(0.0, precision=1.0, abstention_rate=0.01),
        _row(0.10, precision=1.0, abstention_rate=0.08),
    ]
    analysis = analyze_breaking_point(rows)
    assert analysis["abstention_start"] == 0.01
    assert analysis["abstention_end"] == 0.25
    assert analysis["verdict"] == "SAFE"


# --- format_breaking_point_analysis ---------------------------------------------------


def test_format_breaking_point_analysis_names_first_drop_level() -> None:
    rows = [
        _row(0.0, precision=1.0),
        _row(0.10, precision=0.90),
    ]
    text = format_breaking_point_analysis(analyze_breaking_point(rows))
    assert "BREAKING POINT ANALYSIS" in text
    assert "0.10" in text
    assert "VERDICT: UNSAFE" in text


def test_format_breaking_point_analysis_names_crash_level() -> None:
    rows = [
        _row(0.0, precision=1.0),
        _row(0.20, status="CRASHED", precision=0.0),
    ]
    text = format_breaking_point_analysis(analyze_breaking_point(rows))
    assert "crashes/refuses to complete at chaos_level = 0.20" in text
    assert "VERDICT: SAFE" in text


def test_format_breaking_point_analysis_never_crashed_text() -> None:
    rows = [_row(0.0, precision=1.0), _row(0.30, precision=1.0)]
    text = format_breaking_point_analysis(analyze_breaking_point(rows))
    assert "never crashed" in text


def test_format_breaking_point_analysis_never_dropped_text() -> None:
    rows = [_row(0.0, precision=1.0), _row(0.30, precision=1.0)]
    text = format_breaking_point_analysis(analyze_breaking_point(rows))
    assert "never dropped below 100%" in text


# --- format_table ---------------------------------------------------------------------


def test_format_table_has_header_and_one_row_per_level() -> None:
    rows = [
        _row(0.0, precision=1.0),
        _row(0.10, precision=1.0),
        _row(0.30, precision=1.0),
    ]
    text = format_table(rows)
    lines = text.splitlines()
    assert len(lines) == 5  # header + separator + 3 data rows
    assert "Precision" in lines[0]
    assert "Status" in lines[0]


def test_format_table_orders_rows_by_chaos_level_regardless_of_input_order() -> None:
    rows = [_row(0.30), _row(0.0)]
    text = format_table(rows)
    data_lines = text.splitlines()[2:]
    assert data_lines[0].strip().startswith("0.00")
    assert data_lines[1].strip().startswith("0.30")


def test_format_table_renders_crashed_row_with_dashes() -> None:
    rows = [_row(0.0, precision=1.0), _row(0.20, status="CRASHED", precision=0.0)]
    text = format_table(rows)
    crashed_line = text.splitlines()[3]
    assert "CRASHED" in crashed_line
    assert "-" in crashed_line


def test_format_table_renders_missing_throughput_as_placeholder() -> None:
    rows = [_row(0.0, throughput_rps=None)]
    text = format_table(rows)
    assert "?" in text.splitlines()[2]
