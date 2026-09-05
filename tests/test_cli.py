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


def test_cli_replay_proves_decisions_reproduce(tmp_path, monkeypatch) -> None:
    """ "Every decision can be replayed" is a claim this project makes in its
    README, architecture doc and audit report. This is the executable proof:
    generate a batch, reconcile it, then re-derive every decision from the
    same inputs and require them to match.

    A financial decision that changes between runs on identical evidence
    cannot be audited, so `replay` exits non-zero when stability is not
    100% -- asserting the exit code here is asserting the guarantee.
    """
    monkeypatch.chdir(tmp_path)

    assert runner.invoke(app, ["generate", "--total-records", "100", "--seed", "42"]).exit_code == 0
    assert runner.invoke(app, ["run"]).exit_code == 0

    result = runner.invoke(app, ["replay"])

    assert result.exit_code == 0, result.stdout
    assert "100.00%" in result.stdout
    assert "Changed: 0" in result.stdout


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "SettleGraph" in result.stdout
    assert "generate" in result.stdout
    assert "run" in result.stdout
    assert "benchmark" in result.stdout
    assert "simulate" in result.stdout
    assert "serve" in result.stdout
    assert "replay" in result.stdout
