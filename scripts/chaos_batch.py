"""THE BREAKING POINT BATCH: the harsher cousin of scripts/noise_sweep.py.

Not a pytest test -- same pattern as scripts/noise_sweep.py and
scripts/stress_test.py: a standalone script meant to be run manually and read
by a human (or dropped straight into a DEVLOG/README table). It writes into
an isolated temp workspace, never touching the real data/generated or
results/ directories the CLI's `run`/`serve` commands use.

Where noise_sweep.py varies exactly ONE dial (`anomaly_rate`) on otherwise-
normal data, this script fixes anomaly_rate HIGH and then does something
noise_sweep never does: it post-processes the already-written CSVs to
structurally damage them along axes `SyntheticDataGenerator`'s anomaly
injection does not control at all --

    * duplicated whole rows (a re-imported batch file)
    * randomly nulled non-critical (schema-optional) fields
    * malformed / unparseable timestamps in a small fraction of rows
    * truncated / case-shifted / leading-zero-mangled reference numbers
    * physically impossible amounts, in fields the schema does not bound
    * rows pulled out of chronological order and appended at the end

Escalating "chaos level" (0%, 10%, 20%, ... of rows structurally damaged) is
swept exactly like noise_sweep sweeps `anomaly_rate`, and at each level this
script asks the two questions the project brief poses explicitly:

    1. How long does precision remain acceptable? (a false auto-book is the
       one failure this system is built to never produce -- see
       AGENTS.md's "Precision = 100.0%" invariant.)
    2. Does the system degrade gracefully or crash?

The schema (`settlegraph.models`) is Pydantic-validated on ingest. This
harness is the reason that validation is now row-level: on its first run
`engine/ingest.py` loaded every CSV via an all-or-nothing list comprehension,
so a single unparseable row anywhere in a source file aborted the ENTIRE
ingest for that source, and the sweep hard-crashed from 20% damage onward.
That is fixed -- `_load_with_quarantine` validates each row independently and
diverts the failures into `results/quarantine.json` -- and the `Quar` column
below reports how many rows each level quarantined, so the sweep carries its
own evidence that bad rows were isolated rather than silently dropped.

A crash is still a first-class outcome (`status: "CRASHED"`) rather than a
hidden failure of the sweep, and still reachable: quarantine only catches
`ValidationError`, so a wholly missing source file, a non-UTF8 export, or a
`csv.Error` will still fail the batch closed. This script's own position,
matching stress_test.py and noise_sweep.py: a system that fails closed
(refuses a batch it cannot make sense of) is behaving correctly. A system
that keeps confidently auto-booking anyway, or that lets precision slip
below 100%, is not -- and that is the only thing this script's exit code
actually gates on.

Usage:
    python scripts/chaos_batch.py [--records 500] [--seed 42]
        [--anomaly-rate 0.30] [--levels 0.0,0.10,0.20,0.30,0.40,0.50]
        [--json path/to/out.json] [--keep]

Exit code is 0 only if the sweep is SAFE: precision never dropped below 100%
and no invariant violation was observed at any tested chaos level. A crash is
NOT, by itself, treated as a failure -- fail-closed is acceptable behavior.
Only a false auto-book (precision < 100%) or an invariant violation slipping
through is a real finding worth a non-zero exit, the same way stress_test.py
and noise_sweep.py report a broken safety property rather than a script bug.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
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

DEFAULT_LEVELS = "0.0,0.10,0.20,0.30,0.40,0.50"

# A high, fixed baseline of the generator's OWN anomaly kinds (missing UTR,
# split settlement, out-of-order arrival, etc. -- see datagen/generator.py's
# `_inject_anomalies`) underneath every chaos level, so this sweep is always
# measuring "how much WORSE does it get" on top of an already-hard batch, not
# "how does chaos compare to a clean one." 0.30 matches noise_sweep.py's own
# upper tested bound.
DEFAULT_ANOMALY_RATE = 0.30

# Threshold naming below which a below-100% precision reading is still
# reported (never hidden) but does not, alone, change the sweep's verdict --
# mirrors noise_sweep.py's PRECISION_COLLAPSE_THRESHOLD in spirit, except
# this harness's brief is explicit that ANY false auto-book is the failure
# that matters, so unlike noise_sweep there is no lenient collapse band here:
# precision < 100% at any level is enough to call the sweep UNSAFE. See
# `analyze_breaking_point`.
ABSTENTION_RISE_MIN_DELTA = 0.01

# Unparseable timestamps are the axis most likely to reject a row outright,
# so they stay scaled well below the other, survivable axes. The original
# reason was that ingest had zero row-level fault tolerance and one bad
# timestamp aborted the whole source (see module docstring); that is fixed,
# and the scale is kept for a second reason that outlived the first: a row
# this axis damages is quarantined rather than reconciled, so running it at
# full strength would quietly turn the sweep into a measure of how much data
# we discard, drowning out the matching behavior at every nonzero chaos level
# that the other axes exist to probe.
TIMESTAMP_AXIS_SCALE = 0.01

# ~1 crore rupees in a single line item -- schema-legal (no upper bound on
# any paise field in bank_statements.csv or merchant_ledger.csv) but
# physically absurd for a single settlement credit or ledger sale.
IMPOSSIBLE_AMOUNT_PAISE = 999_999_999_999

MANGLE_KINDS: tuple[str, ...] = ("truncate", "case_shift", "leading_zero")

MALFORMED_TIMESTAMP_VALUES: tuple[str, ...] = (
    "not-a-date",
    "32/13/2026",
    "2026-02-30T99:99:99",
    "0000-00-00",
    "yesterday-ish",
)

# Which fields, per ingested CSV, each damage axis is allowed to touch.
# Deliberately hand-picked against the real Pydantic schemas in
# src/settlegraph/models/ rather than "every column": the "nullable" and
# "reference" lists are schema-Optional fields so those two axes are
# survivable by construction, and razorpay's "amount" list is intentionally
# EMPTY -- mutating amount_paise/fee_paise/tax_paise/net_amount_paise there
# breaks RazorpaySettlementRecord.net_must_balance unconditionally, which
# would make that axis a guaranteed crash at every nonzero chaos level and
# tell us nothing new (bank_statements.csv and merchant_ledger.csv already
# cover the "schema permits an absurd amount" case honestly -- see
# IMPOSSIBLE_AMOUNT_PAISE above).
FIELD_DAMAGE_MAP: dict[str, dict[str, list[str]]] = {
    "razorpay_settlements.csv": {
        "nullable": ["settlement_utr", "order_id", "payment_method", "description", "notes"],
        "reference": ["settlement_utr"],
        "timestamp": ["captured_at", "settled_at"],
        "amount": [],
    },
    "bank_statements.csv": {
        "nullable": ["value_date", "reference_number", "balance_paise"],
        "reference": ["reference_number"],
        "timestamp": ["transaction_date", "value_date"],
        "amount": ["credit_amount_paise", "debit_amount_paise"],
    },
    "merchant_ledger.csv": {
        "nullable": [
            "order_id",
            "invoice_number",
            "customer_id",
            "payment_status",
            "payment_gateway_id",
            "notes",
        ],
        "reference": ["invoice_number", "payment_gateway_id"],
        "timestamp": ["created_at"],
        "amount": ["amount_paise"],
    },
}

TABLE_COLUMNS: tuple[tuple[str, int], ...] = (
    ("Chaos", 7),
    ("Status", 9),
    ("Precision%", 11),
    ("Recall%", 9),
    ("SafeAuto%", 10),
    ("FalseBook%", 11),
    ("DangerMiss%", 12),
    ("Auto", 7),
    ("Likely", 8),
    ("Excep", 7),
    ("Abstain%", 9),
    ("InvViol", 8),
    ("Quar", 6),
    ("DupInt", 7),
    ("Rec/s", 9),
)


def parse_levels(text: str) -> list[float]:
    """Parse a comma-separated `--levels` argument into a sorted float list."""
    levels = [float(part) for part in text.split(",") if part.strip() != ""]
    if not levels:
        raise ValueError("--levels must name at least one chaos level")
    return sorted(levels)


# --- Pure damage-injection helpers -------------------------------------------
#
# Every function below takes a `list[dict[str, str]]` (the shape
# `csv.DictReader` produces), a seeded `random.Random`, and a fraction, and
# returns a NEW list -- never mutating its input -- so each one is directly
# unit-testable against tiny in-memory row lists without ever touching disk,
# the generator, or the pipeline. `apply_structural_damage` composes them;
# `damage_generated_batch` is the only I/O-touching function in this section.


def inject_duplicate_rows(
    rows: list[dict[str, str]], rng: random.Random, fraction: float
) -> list[dict[str, str]]:
    """Append exact duplicates of a fraction of rows -- a re-imported batch
    file. Safe by construction: IdempotencyShield fingerprints full record
    content, so a byte-identical duplicate row should be intercepted before
    it ever reaches scoring, not silently double-booked."""
    if not rows or fraction <= 0:
        return [dict(r) for r in rows]
    n = min(len(rows), round(len(rows) * fraction))
    if n <= 0:
        return [dict(r) for r in rows]
    idxs = rng.sample(range(len(rows)), n)
    duplicated = [dict(rows[i]) for i in idxs]
    return [dict(r) for r in rows] + duplicated


def inject_null_fields(
    rows: list[dict[str, str]], rng: random.Random, fraction: float, nullable_fields: list[str]
) -> list[dict[str, str]]:
    """Blank one randomly-chosen schema-Optional field on a fraction of rows.

    Restricted to fields typed `X | None` in the Pydantic models (see
    FIELD_DAMAGE_MAP's docstring) so this axis tests "does the system cope
    with missing optional data" rather than accidentally re-deriving the
    malformed-timestamp axis's crash by blanking a required field."""
    if not rows or fraction <= 0 or not nullable_fields:
        return [dict(r) for r in rows]
    n = min(len(rows), round(len(rows) * fraction))
    out = [dict(r) for r in rows]
    for i in rng.sample(range(len(out)), n):
        field = rng.choice(nullable_fields)
        if field in out[i]:
            out[i][field] = ""
    return out


def _mangle_reference_value(value: str, kind: str, rng: random.Random) -> str:
    if kind == "truncate":
        cut = max(1, len(value) // 2)
        return value[:cut]
    if kind == "case_shift":
        return value.swapcase()
    if kind == "leading_zero":
        return "0" * rng.randint(1, 4) + value
    return value


def inject_mangled_references(
    rows: list[dict[str, str]], rng: random.Random, fraction: float, reference_fields: list[str]
) -> list[dict[str, str]]:
    """Truncate, case-shift, or leading-zero-mangle a reference-shaped field
    (UTR, invoice number, gateway id) on a fraction of rows.

    Always produces a still-syntactically-valid string, so this axis should
    never crash ingest -- it tests whether identity matching stays SAFE (no
    false match) when a reference is present but corrupted, matching the
    documented "safe but blind" finding in datagen/adversarial.py's
    unicode_and_case_utr_drift."""
    if not rows or fraction <= 0 or not reference_fields:
        return [dict(r) for r in rows]
    n = min(len(rows), round(len(rows) * fraction))
    out = [dict(r) for r in rows]
    for i in rng.sample(range(len(out)), n):
        field = rng.choice(reference_fields)
        value = out[i].get(field)
        if not value:
            continue
        kind = rng.choice(MANGLE_KINDS)
        out[i][field] = _mangle_reference_value(value, kind, rng)
    return out


def inject_impossible_values(
    rows: list[dict[str, str]], rng: random.Random, fraction: float, amount_fields: list[str]
) -> list[dict[str, str]]:
    """Replace an already-populated amount field on a fraction of rows with
    a physically impossible (but schema-legal -- no upper bound is declared
    on any paise field) magnitude. Only touches a field that already carries
    a value on that row, so it can never turn Bank's exactly-one-of
    debit/credit invariant into zero-of or two-of."""
    if not rows or fraction <= 0 or not amount_fields:
        return [dict(r) for r in rows]
    n = min(len(rows), round(len(rows) * fraction))
    out = [dict(r) for r in rows]
    for i in rng.sample(range(len(out)), n):
        field = rng.choice(amount_fields)
        if out[i].get(field):
            out[i][field] = str(IMPOSSIBLE_AMOUNT_PAISE + rng.randint(0, 999))
    return out


def inject_malformed_timestamps(
    rows: list[dict[str, str]], rng: random.Random, fraction: float, timestamp_fields: list[str]
) -> list[dict[str, str]]:
    """Overwrite a date/datetime field on a fraction of rows with an
    unparseable string. See TIMESTAMP_AXIS_SCALE's docstring for why callers
    pass in a fraction already scaled far below the other axes -- Pydantic
    cannot coerce any of MALFORMED_TIMESTAMP_VALUES, and engine/ingest.py's
    list-comprehension ingest means even one such row aborts the whole
    source file's ingest."""
    if not rows or fraction <= 0 or not timestamp_fields:
        return [dict(r) for r in rows]
    n = min(len(rows), round(len(rows) * fraction))
    out = [dict(r) for r in rows]
    for i in rng.sample(range(len(out)), n):
        field = rng.choice(timestamp_fields)
        out[i][field] = rng.choice(MALFORMED_TIMESTAMP_VALUES)
    return out


def inject_out_of_order(
    rows: list[dict[str, str]], rng: random.Random, fraction: float
) -> list[dict[str, str]]:
    """Pull a fraction of rows out of their original position and append
    them at the end in shuffled order -- late-arriving events landing at the
    tail of a batch file rather than in sequence. Since SettleGraph is a
    batch matcher, not a streaming one, this axis is expected to be close to
    a no-op on match outcomes; that expectation is itself worth confirming
    rather than assuming."""
    if not rows or fraction <= 0:
        return [dict(r) for r in rows]
    n = min(len(rows), round(len(rows) * fraction))
    if n <= 0:
        return [dict(r) for r in rows]
    move_idxs = set(rng.sample(range(len(rows)), n))
    kept = [dict(r) for i, r in enumerate(rows) if i not in move_idxs]
    moved = [dict(rows[i]) for i in sorted(move_idxs)]
    rng.shuffle(moved)
    return kept + moved


def apply_structural_damage(
    csv_name: str, rows: list[dict[str, str]], rng: random.Random, chaos_level: float
) -> list[dict[str, str]]:
    """Compose every damage axis for one CSV's already-parsed rows, at one
    chaos level. Pure and I/O-free: takes and returns plain row lists so it
    is directly unit-testable against synthetic rows, independent of ever
    writing a real batch to disk."""
    if chaos_level <= 0 or not rows:
        return [dict(r) for r in rows]
    field_map = FIELD_DAMAGE_MAP[csv_name]
    damaged = inject_null_fields(rows, rng, chaos_level, field_map["nullable"])
    damaged = inject_mangled_references(damaged, rng, chaos_level, field_map["reference"])
    damaged = inject_impossible_values(damaged, rng, chaos_level, field_map["amount"])
    damaged = inject_out_of_order(damaged, rng, chaos_level)
    damaged = inject_malformed_timestamps(
        damaged, rng, chaos_level * TIMESTAMP_AXIS_SCALE, field_map["timestamp"]
    )
    # Duplicates last, from whatever the batch looks like after the other
    # axes ran -- a re-imported batch file would duplicate whatever was
    # actually in the file, damage and all.
    damaged = inject_duplicate_rows(damaged, rng, chaos_level)
    return damaged


# --- I/O: reading/writing CSVs and running one sweep level -------------------


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv_rows(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def damage_generated_batch(data_dir: Path, rng: random.Random, chaos_level: float) -> None:
    """Post-process the three CSVs `engine/ingest.py` actually loads
    (razorpay_settlements.csv, bank_statements.csv, merchant_ledger.csv),
    injecting structural damage in place.

    ground_truth.csv is deliberately left untouched: it is the oracle this
    sweep's precision/recall are measured against, not input data the
    pipeline ingests, and it is not Pydantic-validated on load (see
    engine/evaluate.py) -- damaging it would corrupt the measurement, not
    exercise anything the pipeline needs to survive.
    """
    if chaos_level <= 0:
        return
    for csv_name in FIELD_DAMAGE_MAP:
        path = data_dir / csv_name
        if not path.exists():
            continue
        rows = read_csv_rows(path)
        damaged = apply_structural_damage(csv_name, rows, rng, chaos_level)
        write_csv_rows(path, damaged)


def run_one_level(
    records: int, seed: int, chaos_level: float, anomaly_rate: float, workspace_root: Path
) -> dict:
    """Generate one chaos level's dataset into an isolated subdirectory of
    `workspace_root`, structurally damage it, run the full real pipeline
    against it, and reduce the resulting summary dict to one flat metrics
    row -- or, if generation/damage/ingest/the pipeline raises, catch it and
    return a `status: "CRASHED"` row instead of propagating.

    That broad `except Exception` is deliberate, not sloppy: this harness's
    entire point is that a hard failure at some chaos level is a first-class,
    expected OUTCOME to record and keep sweeping past, not a bug in this
    script to let abort the whole run -- see the module docstring.
    """
    level_dir = workspace_root / f"level_{chaos_level:g}"
    data_dir = level_dir / "data"
    results_dir = level_dir / "results"

    row: dict = {"chaos_level": chaos_level, "status": "OK", "error": None}
    try:
        SyntheticDataGenerator(seed=seed, anomaly_rate=anomaly_rate, output_dir=data_dir).write(
            records
        )
        damage_rng = random.Random(seed * 100_003 + round(chaos_level * 10_000))
        damage_generated_batch(data_dir, damage_rng, chaos_level)

        config = PipelineConfig(generated_data_directory=data_dir)
        summary = run_pipeline(data_dir=data_dir, output_dir=results_dir, config=config)
    except Exception as exc:
        row["status"] = "CRASHED"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    evaluation = summary.get("evaluation", {})
    assignments = summary.get("assignments", {})
    throughput = summary.get("throughput", {})

    total_assignments = assignments.get("total", 0)
    likely_match = assignments.get("likely_match", 0)
    abstention_rate = (likely_match / total_assignments) if total_assignments else 0.0

    row.update(
        {
            "precision": evaluation.get("precision", 0.0),
            "recall": evaluation.get("recall", 0.0),
            "safe_auto_resolution_rate": evaluation.get("safe_auto_resolution_rate", 0.0),
            "false_auto_book_rate": evaluation.get("false_auto_book_rate", 0.0),
            "dangerous_miss_rate": evaluation.get("dangerous_miss_rate", 0.0),
            "exception_recall": evaluation.get("exception_recall", 0.0),
            "auto_match": assignments.get("auto_match", 0),
            "likely_match": likely_match,
            "exception": assignments.get("exception", 0),
            "abstention_rate": abstention_rate,
            "invariant_violations": summary.get("invariant_violations", 0),
            "quarantined_records": summary.get("quarantined_records", 0),
            "duplicates_intercepted": summary.get("duplicates_intercepted", 0),
            "throughput_rps": throughput.get("records_per_second"),
        }
    )
    return row


# --- Pure formatting/analysis -------------------------------------------------


def format_table(rows: list[dict]) -> str:
    """Render one row per chaos level as a fixed-width ASCII table, matching
    noise_sweep.py's style. Pure and I/O-free: takes/returns plain
    dicts/strings so it is directly unit-testable against synthetic rows."""
    sorted_rows = sorted(rows, key=lambda r: r["chaos_level"])
    header = "".join(name.rjust(width) for name, width in TABLE_COLUMNS)
    separator = "-" * len(header)
    lines = [header, separator]
    for row in sorted_rows:
        if row["status"] == "CRASHED":
            values = [f"{row['chaos_level']:.2f}", "CRASHED"] + ["-"] * (len(TABLE_COLUMNS) - 2)
        else:
            throughput = row.get("throughput_rps")
            values = [
                f"{row['chaos_level']:.2f}",
                "OK",
                f"{row['precision'] * 100:.2f}",
                f"{row['recall'] * 100:.2f}",
                f"{row['safe_auto_resolution_rate'] * 100:.2f}",
                f"{row['false_auto_book_rate'] * 100:.2f}",
                f"{row['dangerous_miss_rate'] * 100:.2f}",
                str(row["auto_match"]),
                str(row["likely_match"]),
                str(row["exception"]),
                f"{row['abstention_rate'] * 100:.2f}",
                str(row["invariant_violations"]),
                str(row.get("quarantined_records", 0)),
                str(row["duplicates_intercepted"]),
                f"{throughput:.1f}" if throughput is not None else "?",
            ]
        line = "".join(value.rjust(width) for value, (_, width) in zip(values, TABLE_COLUMNS))
        lines.append(line)
    return "\n".join(lines)


def analyze_breaking_point(rows: list[dict]) -> dict:
    """Pure analysis of a chaos sweep's rows.

    Answers, in the priority order the project brief asks for:
      1. The chaos level at which precision FIRST drops below 100% among
         levels that completed (a false auto-book -- the failure that
         matters). Unlike noise_sweep.py there is no lenient "collapse"
         band: this brief is explicit that ANY false auto-book is the real
         breaking point, so the verdict is UNSAFE the moment this happens
         even once, at even one level.
      2. The chaos level at which the pipeline FIRST crashes/refuses to
         complete. A crash, by itself, does not affect the verdict --
         fail-closed is acceptable behavior for a system under load it
         cannot make sense of; see the module docstring.
      3. Whether abstention (LIKELY_MATCH / total assignments) rose across
         the levels that completed, the same healthy-direction check
         noise_sweep.py performs -- computed only over completed levels,
         since a crashed level has no assignments to measure.

    Deliberately I/O-free: takes and returns plain dicts/lists so it is
    directly unit-testable against synthetic metric rows, independent of
    ever running the real pipeline.
    """
    if not rows:
        raise ValueError("no rows to analyze")

    sorted_rows = sorted(rows, key=lambda r: r["chaos_level"])
    completed = [r for r in sorted_rows if r["status"] != "CRASHED"]
    crashed = [r for r in sorted_rows if r["status"] == "CRASHED"]

    first_precision_drop_level = next(
        (r["chaos_level"] for r in completed if r["precision"] < 1.0), None
    )
    min_precision = min((r["precision"] for r in completed), default=None)
    precision_ever_dropped = first_precision_drop_level is not None

    first_crash_level = crashed[0]["chaos_level"] if crashed else None
    crashed_levels = [r["chaos_level"] for r in crashed]

    invariant_violation_levels = [
        r["chaos_level"] for r in completed if r["invariant_violations"] > 0
    ]
    any_invariant_violations = bool(invariant_violation_levels)

    abstention_start = abstention_end = None
    abstention_rose: bool | None = None
    if len(completed) > 1:
        abstentions = [r["abstention_rate"] for r in completed]
        abstention_start = abstentions[0]
        abstention_end = abstentions[-1]
        deltas = [b - a for a, b in zip(abstentions, abstentions[1:])]
        increases = sum(1 for d in deltas if d > 1e-9)
        decreases = sum(1 for d in deltas if d < -1e-9)
        abstention_rose = (
            abstention_end - abstention_start
        ) >= ABSTENTION_RISE_MIN_DELTA and decreases <= increases
    elif len(completed) == 1:
        abstention_start = abstention_end = completed[0]["abstention_rate"]

    reasons: list[str] = []
    if precision_ever_dropped:
        reasons.append(
            "precision first dropped below 100% at chaos_level="
            f"{first_precision_drop_level:.2f} "
            f"(minimum observed precision: {min_precision * 100:.2f}%) -- a false "
            "auto-book, the one failure this system claims it will never produce"
        )
    if any_invariant_violations:
        reasons.append(
            f"invariant violation(s) detected at chaos level(s) {invariant_violation_levels} "
            "-- see enforce_invariant_gate in engine/pipeline.py"
        )

    return {
        "first_precision_drop_level": first_precision_drop_level,
        "min_precision": min_precision,
        "precision_ever_dropped": precision_ever_dropped,
        "first_crash_level": first_crash_level,
        "crashed_levels": crashed_levels,
        "any_invariant_violations": any_invariant_violations,
        "invariant_violation_levels": invariant_violation_levels,
        "abstention_start": abstention_start,
        "abstention_end": abstention_end,
        "abstention_rose": abstention_rose,
        "completed_levels": [r["chaos_level"] for r in completed],
        "verdict": "UNSAFE" if reasons else "SAFE",
        "reasons": reasons,
    }


def format_breaking_point_analysis(analysis: dict) -> str:
    """Render `analyze_breaking_point`'s output as the plain-language
    BREAKING POINT ANALYSIS section, in the priority order the project brief
    asks for. Pure formatting, unit-testable on its own against a synthetic
    analysis dict."""
    lines = ["=" * 70, "BREAKING POINT ANALYSIS", "=" * 70]

    if analysis["first_precision_drop_level"] is None:
        if analysis["min_precision"] is None:
            lines.append("1. Precision: no chaos level completed far enough to measure it.")
        else:
            lines.append(
                "1. Precision never dropped below 100% at any level that completed "
                f"(minimum observed: {analysis['min_precision'] * 100:.2f}%)."
            )
    else:
        lines.append(
            "1. Precision first drops below 100% at chaos_level = "
            f"{analysis['first_precision_drop_level']:.2f} "
            f"(minimum observed precision: {analysis['min_precision'] * 100:.2f}%). "
            "This is the real breaking point: a false auto-book."
        )

    if analysis["first_crash_level"] is None:
        lines.append("2. The pipeline completed at every tested chaos level; it never crashed.")
    else:
        lines.append(
            "2. The pipeline first crashes/refuses to complete at chaos_level = "
            f"{analysis['first_crash_level']:.2f} (crashed at: {analysis['crashed_levels']})."
        )

    if analysis["abstention_rose"] is None:
        lines.append(
            "3. Abstention trend: inconclusive -- fewer than two chaos levels completed "
            "without crashing."
        )
    elif analysis["abstention_rose"]:
        lines.append(
            "3. Abstention (LIKELY_MATCH) rate rose as chaos increased across completed "
            f"levels: {analysis['abstention_start'] * 100:.2f}% -> "
            f"{analysis['abstention_end'] * 100:.2f}% (healthy: the system backs off "
            "rather than confidently auto-matching bad data)."
        )
    else:
        lines.append(
            "3. Abstention (LIKELY_MATCH) rate did NOT meaningfully rise as chaos "
            f"increased: {analysis['abstention_start'] * 100:.2f}% -> "
            f"{analysis['abstention_end'] * 100:.2f}% (unhealthy: the system kept "
            "confidently auto-matching instead of backing off)."
        )

    lines.append("")
    if analysis["verdict"] == "SAFE":
        lines.append(
            "VERDICT: SAFE -- precision never fell below 100% and no invariant violation "
            "was observed at any tested chaos level. Where crashes occurred, that is "
            "fail-closed behavior (a refused batch), not a false auto-book -- an "
            "acceptable outcome for a system that is honest about its limits."
        )
    else:
        lines.append("VERDICT: UNSAFE -- the one guarantee that matters broke:")
        for reason in analysis["reasons"]:
            lines.append(f"  - {reason}")
    lines.append("=" * 70)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--levels", type=str, default=DEFAULT_LEVELS)
    parser.add_argument("--records", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--anomaly-rate", type=float, default=DEFAULT_ANOMALY_RATE)
    parser.add_argument("--json", dest="json_path", type=str, default=None)
    parser.add_argument("--keep", action="store_true", help="Don't delete the temp workspace")
    args = parser.parse_args()

    levels = parse_levels(args.levels)

    workspace = REPO_ROOT / ".chaos-batch-tmp"
    if workspace.exists():
        shutil.rmtree(workspace)

    rows: list[dict] = []
    for i, level in enumerate(levels, start=1):
        print(
            f"[{i}/{len(levels)}] chaos_level={level:g} "
            f"(records={args.records:,}, seed={args.seed}, anomaly_rate={args.anomaly_rate:g})..."
        )
        t0 = time.perf_counter()
        row = run_one_level(args.records, args.seed, level, args.anomaly_rate, workspace)
        elapsed = time.perf_counter() - t0
        if row["status"] == "CRASHED":
            print(f"  CRASHED in {elapsed:.1f}s -- {row['error']}")
        else:
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
                    "anomaly_rate": args.anomaly_rate,
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

    return 0 if analysis["verdict"] == "SAFE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
