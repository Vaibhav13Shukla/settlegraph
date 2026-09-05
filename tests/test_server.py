"""Tests for the SettleGraph HTTP server and API endpoints."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from datagen.generator import SyntheticDataGenerator
from settlegraph.config import PipelineConfig
from settlegraph.engine.pipeline import run_pipeline
from settlegraph.server import SettleGraphAPIHandler


def _invoke_get(path: str, data_dir: Path, results_dir: Path) -> tuple[int, bytes]:
    """Exercise `SettleGraphAPIHandler.do_GET` directly, without binding a
    real socket/port.

    `BaseHTTPRequestHandler.__init__` normally requires a live connection,
    so this bypasses it (`__new__`) and wires up only what `do_GET` and its
    helpers actually touch: `self.path`, `self.server` (backing the
    `data_dir`/`results_dir` properties), and a `BytesIO` standing in for
    `self.wfile`. `send_response`/`send_header`/`end_headers` are mocked
    since they'd otherwise try to write HTTP status/header lines to a real
    socket.
    """
    handler = SettleGraphAPIHandler.__new__(SettleGraphAPIHandler)
    handler.path = path
    handler.server = SimpleNamespace(data_dir=data_dir, results_dir=results_dir)
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.do_GET()
    status = handler.send_response.call_args[0][0]
    return status, handler.wfile.getvalue()


def test_api_handler_summary(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    summary_file = results_dir / "summary.json"
    summary_file.write_text(
        json.dumps({"status": "ok", "records": {"razorpay": 100}}), encoding="utf-8"
    )

    # Verify summary file exists and can be loaded
    data = json.loads(summary_file.read_text(encoding="utf-8"))
    assert data["status"] == "ok"
    assert data["records"]["razorpay"] == 100


def test_api_handler_web_ui_exists() -> None:
    web_file = Path("src/settlegraph/web/index.html")
    assert web_file.exists()
    content = web_file.read_text(encoding="utf-8")
    assert "SettleGraph" in content
    assert "Executive Cockpit" in content


def test_api_handler_web_ui_has_decision_drilldown_elements() -> None:
    """The dashboard's new drill-down tab and baseline comparison table must
    actually be present in the served HTML -- the element ids the JS wires
    up against, not just prose."""
    web_file = Path("src/settlegraph/web/index.html")
    content = web_file.read_text(encoding="utf-8")
    for element_id in (
        "view-decisions",
        "tab-decisions",
        "decision-auto",
        "decision-abstained",
        "decision-exception",
        "decision-failclosed",
        "baseline-table-body",
    ):
        assert f'id="{element_id}"' in content, f"missing #{element_id}"
    assert "/api/baselines" in content


def test_baselines_endpoint_404_when_source_data_missing(tmp_path: Path) -> None:
    status, body = _invoke_get(
        "/api/baselines",
        data_dir=tmp_path / "no_such_data",
        results_dir=tmp_path / "no_such_results",
    )
    assert status == 404
    data = json.loads(body)
    assert "error" in data


def test_baselines_endpoint_404_when_settlegraph_evaluation_missing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    SyntheticDataGenerator(seed=1, anomaly_rate=0.15, output_dir=data_dir).write(60)
    # No evaluation.json written into results_dir -- SettleGraph hasn't run yet.

    status, body = _invoke_get("/api/baselines", data_dir=data_dir, results_dir=results_dir)
    assert status == 404
    data = json.loads(body)
    assert "error" in data


def test_baselines_endpoint_returns_all_four_methods_with_real_metrics(tmp_path: Path) -> None:
    """Full round trip on a freshly generated batch: three baselines
    computed on demand plus SettleGraph's own stored evaluation, each with
    the metrics fields the dashboard table reads."""
    data_dir = tmp_path / "data"
    results_dir = tmp_path / "results"

    SyntheticDataGenerator(seed=7, anomaly_rate=0.15, output_dir=data_dir).write(80)
    config = PipelineConfig(generated_data_directory=data_dir)
    run_pipeline(data_dir=data_dir, output_dir=results_dir, config=config)
    assert (results_dir / "evaluation.json").exists()

    status, body = _invoke_get("/api/baselines", data_dir=data_dir, results_dir=results_dir)
    assert status == 200
    data = json.loads(body)
    baselines = data["baselines"]
    assert set(baselines) == {
        "baseline_a_exact_id",
        "baseline_b_amount_date",
        "baseline_c_fuzzy",
        "settlegraph",
    }
    for key, entry in baselines.items():
        assert "label" in entry
        metrics = entry["metrics"]
        for field in (
            "precision",
            "recall",
            "true_positives",
            "false_positives",
            "false_auto_book_rate",
        ):
            assert field in metrics, f"{key} missing {field}"


def test_baselines_endpoint_is_honest_about_the_real_batch_in_this_workspace() -> None:
    """Track 04 brief's honesty requirement, checked against the actual
    committed-workflow batch already sitting in this workspace's (gitignored)
    `data/generated` / `results` dirs: Baseline B genuinely finds more true
    positives than SettleGraph at the same precision, and Baseline C's fuzzy
    heuristic corrupts a material fraction of the ledger. Skipped, not
    failed, when that local batch isn't present (e.g. a fresh checkout) --
    those directories are gitignored by design (see .gitignore) and are
    exercised for real by the CLI-driven tests instead.
    """
    data_dir = Path("data/generated")
    results_dir = Path("results")
    if (
        not (data_dir / "ground_truth.csv").exists()
        or not (results_dir / "evaluation.json").exists()
    ):
        pytest.skip("no locally generated batch present in data/generated + results")

    status, body = _invoke_get("/api/baselines", data_dir=data_dir, results_dir=results_dir)
    assert status == 200
    baselines = json.loads(body)["baselines"]

    b = baselines["baseline_b_amount_date"]["metrics"]
    sg = baselines["settlegraph"]["metrics"]
    c = baselines["baseline_c_fuzzy"]["metrics"]

    assert b["true_positives"] >= sg["true_positives"]
    assert b["precision"] == sg["precision"]
    assert c["false_auto_book_rate"] > 0.2


def test_api_assignments_filtering_and_sampling(tmp_path: Path):
    """Verify /api/assignments returns balanced rows and supports ?label= filter."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    assign_csv = results_dir / "assignments.csv"
    with assign_csv.open("w", encoding="utf-8") as fh:
        fh.write("source_a,source_a_id,source_b,source_b_id,confidence,label\n")
        # 160 AUTO_MATCH rows
        for i in range(160):
            fh.write(f"razorpay,pay_{i},bank,bank_{i},0.98,AUTO_MATCH\n")
        # 3 LIKELY_MATCH rows
        for i in range(3):
            fh.write(f"razorpay,pay_likely_{i},bank,bank_likely_{i},0.85,LIKELY_MATCH\n")
        # 2 EXCEPTION rows
        for i in range(2):
            fh.write(f"razorpay,pay_exc_{i},bank,bank_exc_{i},0.40,EXCEPTION\n")

    # Balanced sample test: caps AUTO_MATCH at 150, keeps all LIKELY_MATCH and EXCEPTION
    status, body = _invoke_get(
        "/api/assignments", data_dir=tmp_path / "data", results_dir=results_dir
    )
    assert status == 200
    rows = json.loads(body)
    assert len(rows) == 150 + 3 + 2
    assert sum(1 for r in rows if r["label"] == "LIKELY_MATCH") == 3
    assert sum(1 for r in rows if r["label"] == "EXCEPTION") == 2

    # Query param filter test
    status, body = _invoke_get(
        "/api/assignments?label=LIKELY_MATCH", data_dir=tmp_path / "data", results_dir=results_dir
    )
    assert status == 200
    filtered = json.loads(body)
    assert len(filtered) == 3
    assert all(r["label"] == "LIKELY_MATCH" for r in filtered)


def test_history_endpoint(tmp_path: Path):
    """Verify /api/history parses history.jsonl correctly."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    history_file = results_dir / "history.jsonl"
    sample_entry = {"run_id": "test-123", "reconciled_rate": 0.95}
    history_file.write_text(json.dumps(sample_entry) + "\n", encoding="utf-8")

    status, body = _invoke_get("/api/history", data_dir=tmp_path / "data", results_dir=results_dir)
    assert status == 200
    data = json.loads(body)
    assert len(data) == 1
    assert data[0]["run_id"] == "test-123"


def test_calibration_endpoint_404_when_missing(tmp_path: Path):
    """Verify /api/calibration returns 404 when input data is missing."""
    status, body = _invoke_get(
        "/api/calibration", data_dir=tmp_path / "data", results_dir=tmp_path / "results"
    )
    assert status == 404


def test_ask_endpoint(tmp_path: Path):
    """Verify /api/ask returns structured answers to financial queries."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "summary.json").write_text(
        json.dumps(
            {
                "assignments": {"auto_match": 100, "likely_match": 10},
                "invariant_failures": 0,
            }
        ),
        encoding="utf-8",
    )
    (results_dir / "evaluation.json").write_text(
        json.dumps({"precision": 1.0, "recall": 0.9, "false_positives": 0}),
        encoding="utf-8",
    )

    status, body = _invoke_get(
        "/api/ask?q=What%20is%20the%20precision?",
        data_dir=tmp_path / "data",
        results_dir=results_dir,
    )
    assert status == 200
    data = json.loads(body)
    assert "100.0% precision" in data["answer"]
