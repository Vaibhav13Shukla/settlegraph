"""Run-history persistence: the PDF's named "Storage Layer: SQLite /
PostgreSQL" line item, and the piece that turns concept-drift detection from
honest-within-batch-only into real cross-run monitoring.

Stdlib `sqlite3`, no new dependency -- matches this repo's own "do not
overcomplicate the architecture" stance (this page's own subtitle) and
SQLite is one of the two options the brief itself names. One row per
pipeline run, written to `<output_dir>/history.db` -- so repeated
`settlegraph run` calls against the same results directory accumulate a
real trend line, and an isolated test's tmp output dir gets its own
single-row history without polluting anything.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    total_records INTEGER,
    precision REAL,
    recall REAL,
    f1 REAL,
    exception_rate REAL,
    false_match_rate REAL,
    ai_assisted_matches INTEGER,
    records_per_second REAL,
    drift_detected INTEGER,
    summary_json TEXT NOT NULL
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    return conn


def record_run(db_path: Path, summary: dict[str, Any], run_id: str | None = None) -> str:
    """Persist one pipeline run's summary. Returns the run_id.

    Pass `run_id` when the caller needs the id to exist *before* this call
    -- e.g. to embed it in summary.json itself before writing that file,
    which is the whole reason this parameter exists (see DEVLOG Day 5:
    summary.json on disk never carried its own run_id otherwise, so a
    reader of that file alone had no way to look up its history.db row).
    Generates one if not given, so existing callers are unaffected.
    """
    run_id = run_id or str(uuid.uuid4())
    evaluation = summary.get("evaluation") or {}
    throughput = summary.get("throughput") or {}
    drift = summary.get("drift") or {}
    records = summary.get("records") or {}

    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO runs
               (run_id, recorded_at, total_records, precision, recall, f1,
                exception_rate, false_match_rate, ai_assisted_matches,
                records_per_second, drift_detected, summary_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                datetime.now(UTC).isoformat(),
                sum(records.values()) if records else None,
                evaluation.get("precision"),
                evaluation.get("recall"),
                evaluation.get("f1"),
                evaluation.get("exception_rate"),
                evaluation.get("false_match_rate"),
                evaluation.get("ai_assisted_matches", 0),
                throughput.get("records_per_second"),
                1 if drift.get("drift_detected") else 0,
                json.dumps(summary, default=str),
            ),
        )
    return run_id


def list_runs(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT run_id, recorded_at, total_records, precision, recall, f1, "
            "exception_rate, false_match_rate, ai_assisted_matches, "
            "records_per_second, drift_detected FROM runs "
            "ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_metric_series(db_path: Path, metric: str, limit: int = 100) -> list[float]:
    """Oldest-first series of one metric across recorded runs, for feeding
    into a drift detector -- ADWIN (or anything sequential) needs the real
    chronological order, not the most-recent-first order `list_runs` uses
    for display."""
    if metric not in {
        "precision",
        "recall",
        "f1",
        "exception_rate",
        "false_match_rate",
        "records_per_second",
    }:
        raise ValueError(f"Unsupported metric for a drift series: {metric}")
    if not db_path.exists():
        return []
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {metric} FROM runs WHERE {metric} IS NOT NULL ORDER BY recorded_at ASC LIMIT ?",  # noqa: S608
            (limit,),
        ).fetchall()
    return [r[0] for r in rows]


def check_cross_run_drift(db_path: Path, metric: str = "exception_rate") -> dict[str, Any]:
    """Real cross-run drift, unlike `pipeline.py`'s within-batch-only ADWIN
    check -- this reads actual history across separate `settlegraph run`
    invocations. Needs a real trend to say anything: fewer than 10 recorded
    runs returns `insufficient_history` honestly rather than a confident
    verdict built on noise.
    """
    from settlegraph.engine.drift import ADWINDetector

    series = get_metric_series(db_path, metric)
    if len(series) < 10:
        return {
            "metric": metric,
            "runs_available": len(series),
            "status": "insufficient_history",
            "drift_detected": False,
        }

    # ADWINDetector's own default (delta=0.002) is calibrated for
    # high-frequency streaming data -- thousands of points. Checked
    # directly: at that delta, even a stark 0.01 -> 0.60 jump sustained
    # over 40 recorded runs never fires; the Hoeffding bound it's built on
    # needs either far more samples or a far larger effect size than a
    # realistic run-history table will ever have to offer at this delta.
    # Shipping the default here would mean cross-run drift detection is
    # wired correctly but can't practically fire at the scale this
    # feature actually operates at. delta=0.3 fires reliably on a real
    # regime shift at ~20 runs per side while still not false-triggering
    # on flat noisy data (checked empirically, not just derived) -- a
    # deliberate choice, not the library default, and named here so it
    # isn't mistaken for one.
    detector = ADWINDetector(delta=0.3)
    for value in series:
        detector.add_element(value)

    # `detector.drift_detected` only reflects the *last* `add_element` call
    # -- it's reset to False at the top of every call, so checking it after
    # the loop answers "did the final point trigger a split," not "did
    # drift ever occur across this series." `drift_history` accumulates
    # across the whole feed and never gets cleared except by `reset()`, so
    # that's the honest cumulative signal. Found by this module's own test
    # suite deliberately trying a 15-then-15 sharp regime shift and getting
    # `False` back -- see DEVLOG Day 5.
    return {
        "metric": metric,
        "runs_available": len(series),
        "status": "evaluated",
        "drift_detected": len(detector.drift_history) > 0,
        "drift_history": detector.drift_history,
        "current_mean": round(detector.current_mean, 4),
    }
