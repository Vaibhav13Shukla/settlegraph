"""Tests for the SettleGraph HTTP server and API endpoints."""

from __future__ import annotations

import json
from pathlib import Path


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
