from fastapi.testclient import TestClient

from api.index import app


def test_health_endpoint_reports_service_status() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "message": "SettleGraph API is running",
    }


def test_summary_endpoint_returns_404_when_batch_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SETTLEGRAPH_RESULTS_DIR", str(tmp_path / "results"))

    response = TestClient(app).get("/api/summary")

    assert response.status_code == 404
    assert response.json() == {"error": "summary.json not found. Run reconciliation first."}


def test_assignments_endpoint_returns_balanced_rows(tmp_path, monkeypatch) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "assignments.csv").write_text(
        "source_a,source_a_id,source_b,source_b_id,confidence,label\n"
        "razorpay,pay_1,bank,bank_1,0.99,AUTO_MATCH\n"
        "razorpay,pay_2,bank,bank_2,0.80,LIKELY_MATCH\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SETTLEGRAPH_RESULTS_DIR", str(results_dir))

    response = TestClient(app).get("/api/assignments")

    assert response.status_code == 200
    assert [row["label"] for row in response.json()] == ["AUTO_MATCH", "LIKELY_MATCH"]


def test_mutation_endpoint_is_disabled_on_read_only_asgi_deployment() -> None:
    response = TestClient(app).post("/api/run-reconciliation")

    assert response.status_code == 405


def test_q_and_a_rejects_oversized_input() -> None:
    response = TestClient(app).post("/api/ask", json={"query": "x" * 2_001})

    assert response.status_code == 413
    assert "character limit" in response.json()["detail"]


def test_vercel_uses_committed_demo_snapshot_without_runtime_artifacts(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SETTLEGRAPH_RESULTS_DIR", "missing-results")

    response = TestClient(app).get("/api/summary")

    assert response.status_code == 200
    assert response.json()["deployment_mode"] == "demo_snapshot"
    assert response.json()["assignments"]["auto_match"] == 2106
