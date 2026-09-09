from fastapi.testclient import TestClient

from api.index import app


def test_health_endpoint_reports_service_status() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "SettleGraph API is running",
    }
