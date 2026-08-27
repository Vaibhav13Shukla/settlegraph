"""Lightweight, zero-dependency HTTP server and REST API for SettleGraph interactive dashboard."""

from __future__ import annotations

import csv
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from settlegraph.config import PipelineConfig
from settlegraph.engine.pipeline import run_pipeline
from settlegraph.engine.simulator import simulate_all_failures


class SettleGraphAPIHandler(BaseHTTPRequestHandler):
    """Custom HTTP handler serving the web UI and JSON REST endpoints."""

    @property
    def data_dir(self) -> Path:
        return getattr(self.server, "data_dir", Path("data/generated"))

    @property
    def results_dir(self) -> Path:
        return getattr(self.server, "results_dir", Path("results"))

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _send_text(
        self, text: str, content_type: str = "text/html; charset=utf-8", status: int = 200
    ) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            html_file = Path(__file__).parent / "web" / "index.html"
            if html_file.exists():
                self._send_text(html_file.read_text(encoding="utf-8"))
            else:
                self._send_text("<h1>SettleGraph Dashboard</h1><p>Web UI file not found.</p>")
            return

        # Health check endpoints for production orchestrators & container probes
        if path in ("/healthz", "/api/health", "/health"):
            summary_path = self.results_dir / "summary.json"
            self._send_json(
                {
                    "status": "healthy",
                    "version": "0.1.0",
                    "service": "settlegraph-controller",
                    "pipeline_results_available": summary_path.exists(),
                }
            )
            return

        # API Endpoints
        if path == "/api/summary":
            summary_path = self.results_dir / "summary.json"
            if summary_path.exists():
                self._send_json(json.loads(summary_path.read_text(encoding="utf-8")))
            else:
                self._send_json({"error": "Summary not found. Run pipeline first."}, status=404)
            return

        if path == "/api/revenue-assurance":
            rev_path = self.results_dir / "revenue_assurance.json"
            if rev_path.exists():
                self._send_json(json.loads(rev_path.read_text(encoding="utf-8")))
            else:
                self._send_json({"error": "Revenue assurance report not found."}, status=404)
            return

        if path == "/api/assignments":
            assign_path = self.results_dir / "assignments.csv"
            if assign_path.exists():
                with assign_path.open("r", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    rows = list(reader)
                self._send_json(rows[:200])  # Cap at 200 for fast UI rendering
            else:
                self._send_json([])
            return

        if path == "/api/exceptions":
            exc_path = self.results_dir / "exceptions.json"
            if exc_path.exists():
                self._send_json(json.loads(exc_path.read_text(encoding="utf-8")))
            else:
                self._send_json([])
            return

        if path == "/api/evaluation":
            eval_path = self.results_dir / "evaluation.json"
            if eval_path.exists():
                self._send_json(json.loads(eval_path.read_text(encoding="utf-8")))
            else:
                self._send_json({})
            return

        if path == "/api/audit-report":
            audit_path = self.results_dir / "AUDIT_REPORT.md"
            if audit_path.exists():
                self._send_text(
                    audit_path.read_text(encoding="utf-8"),
                    content_type="text/markdown; charset=utf-8",
                )
            else:
                self._send_text(
                    "# Audit report not generated yet.", content_type="text/markdown; charset=utf-8"
                )
            return

        if path == "/api/simulate-failures":
            results = simulate_all_failures()
            self._send_json([r.to_dict() for r in results])
            return

        self._send_json({"error": "Endpoint not found"}, status=404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/run-reconciliation":
            try:
                config = PipelineConfig(generated_data_directory=self.data_dir)
                summary = run_pipeline(
                    data_dir=self.data_dir, output_dir=self.results_dir, config=config
                )
                self._send_json({"status": "success", "summary": summary})
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, status=500)
            return

        self._send_json({"error": "Invalid POST endpoint"}, status=404)


def start_server(
    port: int = 8080,
    host: str = "127.0.0.1",
    data_dir: str = "data/generated",
    results_dir: str = "results",
) -> None:
    """Start the SettleGraph HTTP dashboard server."""

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReusableHTTPServer((host, port), SettleGraphAPIHandler)
    server.data_dir = Path(data_dir)
    server.results_dir = Path(results_dir)

    print(f"\n[OK] SettleGraph Interactive Dashboard running at http://{host}:{port}")
    print("     Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SettleGraph server...")
        server.server_close()
