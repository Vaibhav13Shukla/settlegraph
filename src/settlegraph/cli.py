"""CLI interface for SettleGraph reconciliation controller."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from datagen.generator import SyntheticDataGenerator
from settlegraph.config import PipelineConfig
from settlegraph.engine.baseline import (
    run_amount_date_baseline,
    run_fuzzy_baseline,
    run_naive_baseline,
)
from settlegraph.engine.evaluate import evaluate as run_evaluate
from settlegraph.engine.ingest import load_all, load_razorpay
from settlegraph.engine.normalize import normalize_all
from settlegraph.engine.pipeline import run_pipeline

# Windows' legacy console codepage (cp1252) cannot encode the rupee sign or
# most of what an LLM might put in a free-text answer. Found live: `ask`
# crashed mid-demo the first time a real answer contained "₹". Every
# command in this CLI prints financial output, and the failure mode here is
# a crash, not a mojibake character, so this is a hard requirement rather
# than a nice-to-have -- reconfigure unconditionally rather than only in the
# one command that happened to trigger it first.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - exotic stream types
            pass

app = typer.Typer(help="SettleGraph: Evidence-First Settlement Reconciliation & Revenue Assurance")
console = Console()


@app.command()
def generate(
    total_records: int = 1000,
    anomaly_rate: float = 0.15,
    seed: int = 42,
    with_gst_and_route: bool = True,
) -> None:
    """Generate source views and evaluator-only hidden ground truth.

    Also writes gst_invoices.csv and route_payouts.csv by default -- the
    satellite feeds `settlegraph tax-match` and `settlegraph route-reconcile`
    read. Pass --no-with-gst-and-route to skip them.
    """
    generator = SyntheticDataGenerator(seed=seed, anomaly_rate=anomaly_rate)
    output = generator.write(total_records)
    console.print(
        f"[bold green][OK] Generated {total_records} financial realities in {output}[/bold green]"
    )
    if with_gst_and_route:
        razorpay = load_razorpay(output / "razorpay_settlements.csv")
        generator.write_gst_and_route(razorpay)
        console.print("[bold green][OK] Wrote gst_invoices.csv and route_payouts.csv[/bold green]")


@app.command()
def run(
    data_dir: str = "data/generated",
    output_dir: str = "results",
    threshold: float = 0.95,
    exception_threshold: float = 0.70,
    date_tolerance: int = 3,
    amount_tolerance: int = 100,
) -> None:
    """Run the full reconciliation pipeline end-to-end."""
    config = PipelineConfig(
        auto_match_threshold=threshold,
        exception_threshold=exception_threshold,
        date_tolerance_days=date_tolerance,
        amount_tolerance_paise=amount_tolerance,
        generated_data_directory=Path(data_dir),
    )
    summary = run_pipeline(data_dir=data_dir, output_dir=output_dir, config=config)

    console.print("\n[bold cyan]=== Pipeline Summary ===[/bold cyan]")
    console.print(f"  Razorpay records: {summary['records']['razorpay']}")
    console.print(f"  Bank records:     {summary['records']['bank']}")
    console.print(f"  Merchant records: {summary['records']['merchant']}")
    console.print(f"  Candidates:       {summary['candidates']}")
    console.print(f"  AUTO_MATCH:       [green]{summary['assignments']['auto_match']}[/green]")
    console.print(f"  LIKELY_MATCH:     [yellow]{summary['assignments']['likely_match']}[/yellow]")
    console.print(f"  EXCEPTION:        [red]{summary['assignments']['exception']}[/red]")
    console.print(f"  Unmatched:        {summary['unmatched']}")
    console.print(f"  Invariant fails:  {summary['invariant_violations']}")

    if "evaluation" in summary:
        ev = summary["evaluation"]
        console.print("\n[bold green]=== Ground Truth Evaluation ===[/bold green]")
        console.print(f"  Precision: [bold]{ev['precision'] * 100:.1f}%[/bold]")
        console.print(f"  Recall:    [bold]{ev['recall'] * 100:.1f}%[/bold]")
        console.print(f"  F1 Score:  [bold]{ev['f1']:.4f}[/bold]")


@app.command()
def benchmark(data_dir: str = "data/generated") -> None:
    """Benchmark SettleGraph against a Naive Deterministic Rule baseline."""
    data_path = Path(data_dir)
    gt_path = data_path / "ground_truth.csv"

    if not gt_path.exists():
        console.print("[red]Ground truth not found. Please run 'generate' first.[/red]")
        return

    # Ingest & Normalize
    rzp, bank, merchant = load_all(data_path)
    rzp_norm, bank_norm, merch_norm = normalize_all(rzp, bank, merchant)

    # Three baselines, not one. A single exact-match strawman would let
    # SettleGraph look good for the wrong reason; the interesting comparison
    # is against approaches that are *plausible*, because that is what a
    # merchant would reach for first. Baseline B in particular beats
    # SettleGraph on recall on the current batch -- that result is reported,
    # not buried (see docs/EVALUATION.md).
    tmp_dir = Path(".pytest-tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    baselines = [
        ("A: Exact ID", run_naive_baseline(rzp_norm, bank_norm, merch_norm)),
        ("B: Amount + Date", run_amount_date_baseline(rzp_norm, bank_norm, merch_norm)),
        ("C: Fuzzy Heuristic", run_fuzzy_baseline(rzp_norm, bank_norm, merch_norm)),
    ]

    baseline_evals: list[tuple[str, dict]] = []
    for name, assignments in baselines:
        path = tmp_dir / f"baseline_{name[0].lower()}_assignments.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(assignments[0].keys()))
            writer.writeheader()
            writer.writerows(assignments)
        baseline_evals.append((name, run_evaluate(path, gt_path)))

    # Run SettleGraph itself
    config = PipelineConfig(generated_data_directory=data_path)
    sg_summary = run_pipeline(
        data_dir=data_path, output_dir=".pytest-tmp/sg_results", config=config
    )
    sg_eval = sg_summary.get("evaluation", {})

    table = Table(title="SettleGraph vs. Three Baselines (hidden ground truth)")
    table.add_column("Metric", style="cyan", no_wrap=True)
    for name, _ in baseline_evals:
        table.add_column(name, style="magenta")
    table.add_column("SettleGraph", style="green")

    def _row(label: str, key: str, fmt) -> None:
        cells = [fmt(e.get(key, 0)) for _, e in baseline_evals]
        table.add_row(label, *cells, f"[bold]{fmt(sg_eval.get(key, 0))}[/bold]")

    _row("Precision", "precision", lambda v: f"{v * 100:.1f}%")
    _row("Recall", "recall", lambda v: f"{v * 100:.1f}%")
    _row("F1", "f1", lambda v: f"{v:.4f}")
    _row("True Positives", "true_positives", str)
    _row("False Positives", "false_positives", str)
    _row("False Auto-Book Rate", "false_auto_book_rate", lambda v: f"{v * 100:.2f}%")
    _row("Dangerous Miss Rate", "dangerous_miss_rate", lambda v: f"{v * 100:.1f}%")

    console.print("")
    console.print(table)
    console.print(
        "\n[dim]False Auto-Book Rate is the column that matters: a baseline can win on "
        "recall and still be the wrong system to run, because a false auto-match "
        "corrupts the ledger while an abstention only costs review time.[/dim]"
    )

    # The historical naive-vs-SettleGraph note below works off Baseline A.
    naive_eval = baseline_evals[0][1]
    recall_diff = (sg_eval.get("recall", 0) - naive_eval["recall"]) * 100

    # A naive matcher with zero safety margin can score higher on raw
    # recall than a system that deliberately holds a suspicious-but-correct
    # match for review instead of auto-approving it -- found via this exact
    # command (see DEVLOG Day 5). When that's happening, say so with the
    # number that actually explains it, rather than let a bare "naive wins"
    # table stand unexplained.
    if recall_diff < 0:
        from settlegraph.engine.evaluate import count_correctly_flagged_for_review

        held = count_correctly_flagged_for_review(
            Path(".pytest-tmp/sg_results") / "assignments.csv", gt_path
        )
        console.print(
            f"\n[yellow]Note:[/yellow] SettleGraph's raw recall trails naive here. "
            f"{held['correct']} of its LIKELY_MATCH assignments are pointing at the "
            f"[bold]correct[/bold] counterpart -- held for review rather than "
            f"auto-approved (below the {config.auto_match_threshold} confidence bar, usually "
            f"because of an unexplained multi-day settlement gap). "
            f"Naive has no such bar: it either matches blindly on exact fields or drops the "
            f"record with no signal either way. A higher raw-recall number here would mean "
            f"weaker review discipline, not a better result."
        )


@app.command()
def simulate() -> None:
    """Execute live failure injection test suite across 7 critical scenarios."""
    from settlegraph.engine.simulator import simulate_all_failures

    console.print("\n[bold cyan]=== Executing Failure Injection Scenarios ===[/bold cyan]\n")
    results = simulate_all_failures()

    table = Table(title="Failure Injection Containment Results")
    table.add_column("Scenario ID", style="cyan", no_wrap=True)
    table.add_column("Failure Mode", style="white")
    table.add_column("System Response", style="yellow")
    table.add_column("Status", style="green")

    for r in results:
        status_badge = (
            "[bold green][PASS] CONTAINED[/bold green]"
            if r.passed
            else "[bold red][FAIL] FAILED[/bold red]"
        )
        table.add_row(r.scenario_id, r.name, r.system_response, status_badge)

    console.print(table)

    # The per-row badge above already reads r.passed correctly; this banner
    # used to print an unconditional success message regardless of it --
    # a demo/CI script that only reads this line, or a human skimming past
    # the table, would see "All N ... contained" even if one scenario's own
    # row said FAILED. Found by code review. A failure-injection suite must
    # never assert a pass it didn't measure.
    failed = [r for r in results if not r.passed]
    if failed:
        console.print(
            f"\n[bold red][FAIL] {len(failed)} of {len(results)} failure scenario(s) "
            f"did NOT contain safely: {', '.join(r.scenario_id for r in failed)}.[/bold red]\n"
        )
        raise typer.Exit(code=1)
    console.print(
        f"\n[bold green][PASS] All {len(results)} failure scenarios safely contained with zero false ledger entries.[/bold green]\n"
    )


@app.command()
def ask(
    question: str,
    results_dir: str = "results",
) -> None:
    """Ask the Settlement Q&A Agent a question about a completed batch.

    Read-only: the agent can look up assignments, exceptions, and revenue
    assurance for this batch, and nothing else -- it cannot re-run the
    pipeline or edit any record. Needs `pip install -e ".[llm]"` and either
    ANTHROPIC_API_KEY or an already-authenticated `claude` CLI session.
    """
    import asyncio

    from settlegraph.qa_agent import ask as ask_agent

    if not (Path(results_dir) / "summary.json").exists():
        console.print(
            f"[red]No results found in {results_dir}/. Run 'settlegraph run' first.[/red]"
        )
        raise typer.Exit(code=1)

    console.print(f"[dim]Asking: {question}[/dim]\n")
    try:
        answer = asyncio.run(ask_agent(question, results_dir=results_dir))
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(answer or "[dim](no answer text returned)[/dim]")


@app.command()
def tax_match(data_dir: str = "data/generated", output_dir: str = "results") -> None:
    """Reconcile GST invoiced on Razorpay's MDR fee against the expected 18% --
    a separate reconciliation surface from settlement matching (see
    engine/tax_matcher.py).

    `settlegraph run` already does this automatically whenever
    gst_invoices.csv exists -- use this command to re-run just this surface
    without a full pipeline run."""
    from settlegraph.engine.tax_matcher import run_tax_reconciliation

    summary = run_tax_reconciliation(Path(data_dir), Path(output_dir))
    console.print("\n[bold cyan]=== Tax-Line Reconciliation ===[/bold cyan]")
    console.print(f"  Total tax lines:  {summary['total_tax_lines']}")
    console.print(f"  Match rate:       [bold]{summary['match_rate'] * 100:.1f}%[/bold]")
    console.print(f"  Findings exposure: [bold]INR {summary['findings_exposure_inr']:,.2f}[/bold]")
    for status, data in sorted(summary["by_status"].items()):
        console.print(f"    {status}: {data['count']} (INR {data['exposure_paise'] / 100:,.2f})")


@app.command()
def route_reconcile(data_dir: str = "data/generated", output_dir: str = "results") -> None:
    """Verify Route marketplace payout legs sum to what each split payment
    should have distributed (see engine/route_reconciliation.py).

    `settlegraph run` already does this automatically whenever
    route_payouts.csv exists -- use this command to re-run just this
    surface without a full pipeline run."""
    from settlegraph.engine.route_reconciliation import run_route_reconciliation

    summary = run_route_reconciliation(Path(data_dir), Path(output_dir))
    console.print("\n[bold cyan]=== Route Split Reconciliation ===[/bold cyan]")
    console.print(f"  Marketplace payments: {summary['total_marketplace_payments']}")
    console.print(f"  Split verified:       [bold]{summary['verification_rate'] * 100:.1f}%[/bold]")
    console.print(
        f"  Shortfalls:  {summary['payout_shortfalls']} (INR {summary['shortfall_exposure_inr']:,.2f})"
    )
    console.print(
        f"  Overpayments: {summary['payout_overpayments']} (INR {summary['overpayment_exposure_inr']:,.2f})"
    )


@app.command()
def history(
    results_dir: str = "results",
    metric: str = "exception_rate",
    limit: int = 20,
) -> None:
    """Show recorded run history and check for real cross-run drift --
    unlike the pipeline's own within-batch-only ADWIN check, this reads
    actual separate `settlegraph run` invocations from history.jsonl (a
    plain, appendable, greppable file -- see engine/history_store.py for
    why this isn't SQLite)."""
    from settlegraph.engine.history_store import check_cross_run_drift, list_runs

    history_path = Path(results_dir) / "history.jsonl"
    runs = list_runs(history_path, limit=limit)
    if not runs:
        console.print(
            f"[red]No run history at {history_path}. Run 'settlegraph run' at least once first.[/red]"
        )
        return

    table = Table(title=f"Run History ({len(runs)} of up to {limit} shown, newest first)")
    table.add_column("Recorded At", style="cyan")
    table.add_column("Records", justify="right")
    table.add_column("Precision", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("Exception Rate", justify="right")
    table.add_column("AI-Resolved", justify="right")
    for r in runs:
        table.add_row(
            str(r["recorded_at"])[:19],
            str(r["total_records"] or "--"),
            f"{(r['precision'] or 0) * 100:.1f}%",
            f"{(r['recall'] or 0) * 100:.1f}%",
            f"{(r['exception_rate'] or 0) * 100:.2f}%",
            str(r["ai_assisted_matches"] or 0),
        )
    console.print(table)

    drift = check_cross_run_drift(history_path, metric=metric)
    console.print(f"\n[bold cyan]Cross-run drift on '{metric}':[/bold cyan] {drift['status']}")
    if drift["status"] == "insufficient_history":
        console.print(
            f"  Only {drift['runs_available']} run(s) recorded -- need at least 10 for a real signal."
        )
    else:
        status = (
            "[bold red]YES[/bold red]"
            if drift["drift_detected"]
            else "[bold green]None[/bold green]"
        )
        console.print(f"  Drift detected: {status} (current mean: {drift['current_mean']})")


@app.command()
def replay(
    results_dir: str = "results",
    data_dir: str = "data/generated",
) -> None:
    """Re-derive every decision from the same inputs and prove it reproduces.

    "Every decision can be replayed" is a claim this project makes in its
    README, its architecture doc and its audit report. This is the command
    that makes it checkable rather than asserted: it re-runs the full
    deterministic pipeline over the same source feeds into a scratch
    directory, then diffs the freshly-derived assignments against the ones
    already on disk, decision by decision.

    A finance controller asking "why was this reconciled, and would you get
    the same answer again" gets an actual answer. Any decision that does not
    reproduce is printed with both versions -- an unstable financial decision
    is a defect, not a curiosity, so this exits non-zero when stability is
    not 100%.
    """
    import shutil
    import tempfile

    from settlegraph.engine.calibration import compute_replay_consistency

    stored = Path(results_dir) / "assignments.csv"
    if not stored.exists():
        console.print(f"[red]No assignments at {stored}. Run 'settlegraph run' first.[/red]")
        raise typer.Exit(code=1)

    scratch = Path(tempfile.mkdtemp(prefix="settlegraph-replay-"))
    try:
        console.print(f"[dim]Re-deriving decisions from {data_dir} ...[/dim]\n")
        run_pipeline(
            data_dir=data_dir,
            output_dir=scratch,
            config=PipelineConfig(generated_data_directory=Path(data_dir)),
        )
        result = compute_replay_consistency(stored, scratch / "assignments.csv")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    console.print("\n[bold cyan]=== Replay Consistency ===[/bold cyan]")
    console.print(f"  Decisions compared: {result['total_decisions']}")
    console.print(f"  Reproduced identically: [green]{result['stable_decisions']}[/green]")
    console.print(f"  Changed: [red]{result['changed_decisions']}[/red]")
    console.print(f"  Stability rate: [bold]{result['stability_rate'] * 100:.2f}%[/bold]")

    changed = result.get("changed_examples") or []
    if changed:
        console.print("\n[bold red]Decisions that did not reproduce:[/bold red]")
        for row in changed:
            console.print(f"  {row}")
        console.print(
            "\n[red]A financial decision that changes between runs on identical "
            "evidence cannot be audited. Investigate before trusting this batch.[/red]"
        )
        raise typer.Exit(code=1)

    console.print(
        "\n[green]Every decision re-derived identically from the same evidence.[/green]\n"
    )


@app.command()
def digest(results_dir: str = "results") -> None:
    """Print (and write DIGEST.md) a plain-language summary of this batch --
    deterministic, not LLM-generated, so it's always available and can
    never drift from the numbers it describes.

    `settlegraph run` already writes DIGEST.md automatically -- use this to
    reprint it, or regenerate it after re-running just tax-match or
    route-reconcile on their own."""
    from settlegraph.engine.digest import run_digest

    text = run_digest(Path(results_dir))
    console.print(text)


@app.command()
def serve(
    port: int = 8080,
    host: str = "127.0.0.1",
    data_dir: str = "data/generated",
    results_dir: str = "results",
) -> None:
    """Start the interactive web dashboard server."""
    from settlegraph.server import start_server

    start_server(port=port, host=host, data_dir=data_dir, results_dir=results_dir)


if __name__ == "__main__":
    app()
