"""Tests for SettleGraph CLI commands."""

from __future__ import annotations

from typer.testing import CliRunner

from settlegraph.cli import app

runner = CliRunner()


def test_cli_generate_and_run(tmp_path, monkeypatch) -> None:
    # `generate` has no --output-dir flag and writes to the hardcoded
    # default ("data/generated") -- without isolating the working directory,
    # this test silently overwrites whatever real demo dataset the repo
    # currently has loaded there. That's exactly what it was doing before
    # this fix: any `pytest` run reset `data/generated` to a 100-record
    # throwaway fixture with no warning.
    monkeypatch.chdir(tmp_path)

    # 1. Generate
    gen_result = runner.invoke(app, ["generate", "--total-records", "100", "--seed", "42"])
    assert gen_result.exit_code == 0
    assert "[OK]" in gen_result.stdout
    assert (tmp_path / "data" / "generated" / "razorpay_settlements.csv").exists()

    # 2. Simulate
    sim_result = runner.invoke(app, ["simulate"])
    assert sim_result.exit_code == 0
    assert "[PASS] CONTAINED" in sim_result.stdout


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "SettleGraph" in result.stdout
    assert "generate" in result.stdout
    assert "run" in result.stdout
    assert "benchmark" in result.stdout
    assert "simulate" in result.stdout
    assert "serve" in result.stdout
