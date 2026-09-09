from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="SettleGraph API")
_MAX_QUESTION_LENGTH = 2_000


def _results_dir() -> Path:
    configured = Path(os.environ.get("SETTLEGRAPH_RESULTS_DIR", "results"))
    if configured.exists() or not os.environ.get("VERCEL"):
        return configured
    return Path(__file__).resolve().parent / "demo_data"


def _data_dir() -> Path:
    return Path(os.environ.get("SETTLEGRAPH_DATA_DIR", "data/generated"))


def _read_json(filename: str, *, default: object = None) -> object:
    path = _results_dir() / filename
    if not path.exists():
        if default is not None:
            return default
        return JSONResponse(
            status_code=404,
            content={"error": f"{filename} not found. Run reconciliation first."},
        )
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/")
def home() -> HTMLResponse:
    html_path = (
        Path(__file__).resolve().parent.parent / "src" / "settlegraph" / "web" / "index.html"
    )
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard asset not found.")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/healthz")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "message": "SettleGraph API is running",
    }


@app.get("/api/health")
def api_health() -> dict[str, str]:
    return health()


@app.get("/api/summary")
def summary() -> object:
    return _read_json("summary.json")


@app.get("/api/revenue-assurance")
def revenue_assurance() -> object:
    return _read_json("revenue_assurance.json")


@app.get("/api/evaluation")
def evaluation() -> object:
    return _read_json("evaluation.json")


@app.get("/api/exceptions")
def exceptions() -> object:
    return _read_json("exceptions.json", default=[])


@app.get("/api/assignments")
def assignments(label: str | None = Query(default=None)) -> list[dict[str, str]]:
    path = _results_dir() / "assignments.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if label:
        return [row for row in rows if row.get("label") == label][:500]
    auto = [row for row in rows if row.get("label") == "AUTO_MATCH"]
    likely = [row for row in rows if row.get("label") == "LIKELY_MATCH"]
    exception_rows = [row for row in rows if row.get("label") == "EXCEPTION"]
    ai_resolved = [row for row in rows if row.get("label") == "AI_RESOLVED_MATCH"]
    return auto[:150] + likely + exception_rows + ai_resolved


@app.get("/api/audit-report")
def audit_report() -> HTMLResponse:
    path = _results_dir() / "AUDIT_REPORT.md"
    if not path.exists():
        return HTMLResponse(
            "# SettleGraph demo snapshot\n\n"
            "This Vercel deployment is a read-only snapshot of the seeded batch. "
            "Run the stateful Docker or CLI deployment for a fresh reconciliation.",
            media_type="text/markdown",
        )
    return HTMLResponse(path.read_text(encoding="utf-8"), media_type="text/markdown")


@app.get("/api/calibration")
def calibration() -> object:
    if os.environ.get("VERCEL") and not _data_dir().exists():
        return _read_json("calibration.json")
    assignments_path = _results_dir() / "assignments.csv"
    ground_truth_path = _data_dir() / "ground_truth.csv"
    if not assignments_path.exists() or not ground_truth_path.exists():
        raise HTTPException(status_code=404, detail="assignments.csv or ground_truth.csv not found")
    from settlegraph.engine.calibration import compute_abstention_quality, compute_calibration

    return {
        "calibration": compute_calibration(assignments_path, ground_truth_path),
        "abstention_quality": compute_abstention_quality(assignments_path, ground_truth_path),
    }


@app.get("/api/history")
def history() -> list[object]:
    path = _results_dir() / "history.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@app.get("/api/simulate-failures")
def simulate_failures() -> list[dict]:
    from settlegraph.engine.simulator import simulate_all_failures

    return [result.to_dict() for result in simulate_all_failures()]


@app.get("/api/baselines")
def baselines() -> object:
    data_path = _data_dir()
    results_path = _results_dir()
    ground_truth = data_path / "ground_truth.csv"
    evaluation_path = results_path / "evaluation.json"
    if not ground_truth.exists() or not (data_path / "razorpay_settlements.csv").exists():
        return JSONResponse(
            status_code=404,
            content={"error": f"Source data or ground truth not found under {data_path}."},
        )
    if not evaluation_path.exists():
        return JSONResponse(
            status_code=404,
            content={"error": f"SettleGraph evaluation.json not found under {results_path}."},
        )

    from settlegraph.engine.baseline import (
        run_amount_date_baseline,
        run_fuzzy_baseline,
        run_naive_baseline,
    )
    from settlegraph.engine.evaluate import evaluate as run_evaluate
    from settlegraph.engine.ingest import load_all
    from settlegraph.engine.normalize import normalize_all

    try:
        rzp, bank, merchant = load_all(data_path)
        normalized = normalize_all(rzp, bank, merchant)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load source data: {exc}") from exc

    runners = {
        "baseline_a_exact_id": ("Baseline A: Exact ID Match", run_naive_baseline),
        "baseline_b_amount_date": ("Baseline B: Amount + Date Window", run_amount_date_baseline),
        "baseline_c_fuzzy": ("Baseline C: Fuzzy Heuristic", run_fuzzy_baseline),
    }
    output: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="settlegraph-baselines-") as temporary_dir:
        for key, (label, runner) in runners.items():
            rows = runner(*normalized)
            path = Path(temporary_dir) / f"{key}.csv"
            fieldnames = list(rows[0].keys()) if rows else []
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            output[key] = {"label": label, "metrics": run_evaluate(path, ground_truth)}
    output["settlegraph"] = {
        "label": "SettleGraph (Probabilistic + Shielded)",
        "metrics": json.loads(evaluation_path.read_text(encoding="utf-8")),
    }
    return {"baselines": output}


@app.get("/api/replay")
def replay() -> object:
    stored = _results_dir() / "assignments.csv"
    if not stored.exists():
        return JSONResponse(
            status_code=404,
            content={"error": "assignments.csv not found. Run reconciliation first."},
        )

    from settlegraph.config import PipelineConfig
    from settlegraph.engine.calibration import compute_replay_consistency
    from settlegraph.engine.pipeline import run_pipeline

    with tempfile.TemporaryDirectory(prefix="settlegraph-replay-") as temporary_dir:
        scratch = Path(temporary_dir)
        run_pipeline(
            data_dir=_data_dir(),
            output_dir=scratch,
            config=PipelineConfig(generated_data_directory=_data_dir()),
        )
        return compute_replay_consistency(stored, scratch / "assignments.csv")


@app.post("/api/ask")
def ask(payload: dict[str, str] = Body(default_factory=dict)) -> dict[str, str]:
    question = (payload.get("question") or payload.get("query") or payload.get("q") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="No question provided")
    if len(question) > _MAX_QUESTION_LENGTH:
        raise HTTPException(
            status_code=413,
            detail=f"Question exceeds the {_MAX_QUESTION_LENGTH}-character limit",
        )

    from settlegraph.server import SettleGraphAPIHandler

    handler = SettleGraphAPIHandler.__new__(SettleGraphAPIHandler)
    handler.server = type("ReadOnlyServer", (), {"results_dir": _results_dir()})()
    answer = handler._deterministic_answer(question)
    return {
        "status": "success",
        "question": question,
        "query": question,
        "answer": answer,
        "engine": "settlegraph_deterministic_controller",
        "mode": "deterministic",
    }


@app.post("/api/run-reconciliation")
def run_reconciliation() -> None:
    raise HTTPException(
        status_code=405,
        detail="The Vercel ASGI deployment is read-only. Run reconciliation with the Docker or CLI deployment.",
    )
