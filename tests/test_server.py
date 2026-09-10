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


def _mutation_permitted(client_host: str, allow_remote: bool) -> bool:
    handler = SettleGraphAPIHandler.__new__(SettleGraphAPIHandler)
    handler.server = SimpleNamespace(allow_remote_mutations=allow_remote)
    handler.client_address = (client_host, 54321)
    return handler._mutation_permitted()


def test_only_loopback_callers_may_trigger_a_pipeline_run() -> None:
    """`POST /api/run-reconciliation` re-runs the pipeline and overwrites
    `results/`, with no authentication. The Dockerfile serves on `0.0.0.0`,
    so on any shared network that was an unauthenticated write endpoint
    reachable by anyone who could route to the port. Rated MEDIUM in
    `docs/RED_TEAM.md`.

    A peer check rather than invented auth: a demo tool should not ship a
    fake credential system, and "the request came from this machine" is the
    actual property that makes the local dashboard safe.
    """
    for loopback in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"):
        assert _mutation_permitted(loopback, allow_remote=False) is True

    for remote in ("10.0.0.5", "192.168.1.20", "172.17.0.1", "203.0.113.7"):
        assert _mutation_permitted(remote, allow_remote=False) is False, (
            f"{remote} must not be able to trigger a pipeline run by default"
        )


def test_allow_remote_run_is_an_explicit_opt_in() -> None:
    """An operator who genuinely wants remote triggering passes the flag and
    owns that decision knowingly -- the escape hatch exists, it is just not
    the default."""
    assert _mutation_permitted("10.0.0.5", allow_remote=True) is True


def test_responses_do_not_carry_a_wildcard_cors_header() -> None:
    """The dashboard is served same-origin by this very handler, so
    `Access-Control-Allow-Origin: *` bought nothing and let any website read
    a merchant's reconciliation JSON from the browser of anyone running the
    dashboard."""
    source = Path("src/settlegraph/server.py").read_text(encoding="utf-8")
    assert "Access-Control-Allow-Origin" not in source


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


def test_dashboard_reports_the_measured_replay_rate_and_scopes_evaluation_claims() -> None:
    """The dashboard must not turn a partial replay into a 100% pass or turn a
    bounded evaluation result into a universal guarantee."""
    content = Path("src/settlegraph/web/index.html").read_text(encoding="utf-8")
    assert "Verified ' +" in content
    assert "Verified 100%" not in content
    assert "evaluated batch" in content
    assert "SettleGraph guarantees zero false matches" not in content


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
    committed-workflow batch in this workspace's (gitignored) `data/generated`
    / `results` dirs.

    This test previously asserted ``c["false_auto_book_rate"] > 0.2`` -- that
    the fuzzy baseline (C) corrupted >20% of the ledger. That assertion was
    retired when source identifiers were made independent and opaque (ADR
    0011). The old ~27.7% figure was an artifact of the previous sequential
    UTR format (``RZP{index:012d}``): those strings resemble each other, so
    ``difflib.SequenceMatcher`` scored unrelated payments as near-matches.
    Real UTRs are random 16-char tokens that do not resemble each other, so
    fuzzy string matching is no longer trivially dangerous on this data --
    and honesty means reporting that rather than tuning the data until the
    number comes back. The genuine, per-scenario danger demonstrations live
    in ``datagen/adversarial.py`` where each has an explicit expected safety
    property.

    What remains true and worth asserting: SettleGraph is at least as safe as
    every naive baseline (equal-or-higher precision, zero false auto-books),
    and a baseline can still win on raw recall by refusing to abstain --
    which is the point, not a defect. Skipped when no local batch is present.
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
    c = baselines["baseline_c_fuzzy"]["metrics"]
    sg = baselines["settlegraph"]["metrics"]

    # A baseline may find more true positives (it never abstains) ...
    assert b["true_positives"] >= sg["true_positives"]
    # ... but SettleGraph is never less precise than any baseline, and never
    # auto-books a false match.
    assert sg["precision"] >= b["precision"]
    assert sg["precision"] >= c["precision"]
    assert sg["false_auto_book_rate"] == 0.0


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


def test_ask_endpoint_rejects_oversized_questions(tmp_path: Path) -> None:
    handler = SettleGraphAPIHandler.__new__(SettleGraphAPIHandler)
    handler.server = SimpleNamespace(results_dir=tmp_path / "results")
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    handler._handle_ask("x" * 2_001)

    assert handler.send_response.call_args[0][0] == 413
    assert "character limit" in json.loads(handler.wfile.getvalue())["error"]
