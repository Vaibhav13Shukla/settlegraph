"""Run-history persistence: one row per pipeline run, so cross-run drift
detection has real history to look at instead of only within-batch data.

Plain JSON Lines, not SQLite. Ponytail audit (Day 5): every operation this
module actually performs -- append a row, list recent rows sorted by time,
read one metric as a chronological series -- is a few lines over a flat
file; SQLite added a schema, a connection lifecycle, and a query-
construction surface for that. It also broke with every other output this
project produces (assignments.csv, exceptions.json, revenue_assurance.json,
...): something you can `cat`/`tail`/`grep` without a client. For a project
whose whole ethos is an inspectable audit trail, that's not a minor
convenience, it's the same property the rest of the outputs were chosen for.
The PDF brief names "SQLite / PostgreSQL" as the suggested storage layer;
this uses neither, because neither was needed for what the feature actually
does -- and the file that replaces it is still a real, durable, queryable
persistence layer, just not a binary one.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_METRIC_FIELDS = frozenset(
    {"precision", "recall", "f1", "exception_rate", "false_match_rate", "records_per_second"}
)


def _read_rows(history_path: Path) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    rows = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def record_run(history_path: Path, summary: dict[str, Any], run_id: str | None = None) -> str:
    """Append one pipeline run's summary. Returns the run_id.

    Pass `run_id` when the caller needs the id to exist *before* this call
    -- e.g. to embed it in summary.json itself before writing that file.
    Generates one if not given.
    """
    run_id = run_id or str(uuid.uuid4())
    evaluation = summary.get("evaluation") or {}
    throughput = summary.get("throughput") or {}
    drift = summary.get("drift") or {}
    records = summary.get("records") or {}

    row = {
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "total_records": sum(records.values()) if records else None,
        "precision": evaluation.get("precision"),
        "recall": evaluation.get("recall"),
        "f1": evaluation.get("f1"),
        "exception_rate": evaluation.get("exception_rate"),
        "false_match_rate": evaluation.get("false_match_rate"),
        "ai_assisted_matches": evaluation.get("ai_assisted_matches", 0),
        "records_per_second": throughput.get("records_per_second"),
        "drift_detected": bool(drift.get("drift_detected")),
    }

    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    return run_id


def list_runs(history_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Newest-first, capped at `limit`."""
    rows = _read_rows(history_path)
    rows.sort(key=lambda r: r.get("recorded_at", ""), reverse=True)
    return rows[:limit]


def get_metric_series(history_path: Path, metric: str, limit: int = 100) -> list[float]:
    """Oldest-first series of one metric across recorded runs, for feeding
    into a drift detector -- ADWIN (or anything sequential) needs the real
    chronological order, not `list_runs`'s newest-first display order."""
    if metric not in _METRIC_FIELDS:
        raise ValueError(f"Unsupported metric for a drift series: {metric}")
    rows = _read_rows(history_path)
    rows.sort(key=lambda r: r.get("recorded_at", ""))
    return [r[metric] for r in rows if r.get(metric) is not None][:limit]


def check_cross_run_drift(history_path: Path, metric: str = "exception_rate") -> dict[str, Any]:
    """Real cross-run drift, unlike `pipeline.py`'s within-batch-only ADWIN
    check -- this reads actual history across separate `settlegraph run`
    invocations. Needs a real trend to say anything: fewer than 10 recorded
    runs returns `insufficient_history` honestly rather than a confident
    verdict built on noise.
    """
    from settlegraph.engine.drift import ADWINDetector

    series = get_metric_series(history_path, metric)
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

    # `detector.drift_detected` only reflects the *last* `add_element`
    # call -- it's reset to False at the top of every call, so checking it
    # after the loop answers "did the final point trigger a split," not
    # "did drift ever occur across this series." `drift_history`
    # accumulates across the whole feed and never gets cleared except by
    # `reset()`, so that's the honest cumulative signal.
    return {
        "metric": metric,
        "runs_available": len(series),
        "status": "evaluated",
        "drift_detected": len(detector.drift_history) > 0,
        "drift_history": detector.drift_history,
        "current_mean": round(detector.current_mean, 4),
    }
