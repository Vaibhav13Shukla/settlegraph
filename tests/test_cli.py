"""Tests for SettleGraph CLI commands."""

from __future__ import annotations

from typer.testing import CliRunner

from settlegraph.cli import app

runner = CliRunner()


def test_cli_generate_and_run() -> None:
    # 1. Generate
    gen_result = runner.invoke(app, ["generate", "--total-records", "100", "--seed", "42"])
    assert gen_result.exit_code == 0
    assert "[OK]" in gen_result.stdout

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
