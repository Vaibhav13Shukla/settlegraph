"""Plain-language merchant digest: turn summary.json / revenue_assurance.json
/ tax_reconciliation.json / route_reconciliation.json into the paragraph a
finance controller would actually read, instead of five JSON files they'd
have to open themselves.

Deterministic string templating, not LLM-generated -- on purpose. A digest
that summarizes numbers that are already computed and verified doesn't need
a model in the loop, and this way it's always available with zero
dependencies and zero risk of the summary drifting from the numbers it's
describing (the one thing this whole project has been careful never to let
happen).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_digest(results_dir: Path) -> str:
    """Assemble the digest. Sections that don't have data (e.g. tax-line
    reconciliation wasn't run for this batch) are omitted rather than shown
    empty or fabricated."""
    summary = _load(results_dir / "summary.json")
    if summary is None:
        return "No results found -- run `settlegraph run` first."

    rev = summary.get("revenue_assurance", {})
    fin = rev.get("financial_summary", {})
    cash = rev.get("forward_cash_position", {})
    ev = summary.get("evaluation", {})
    assignments = summary.get("assignments", {})
    drift = summary.get("drift", {})

    lines: list[str] = []
    lines.append("# This Batch, In Plain Language")
    lines.append("")

    total_records = sum(summary.get("records", {}).values())
    lines.append(
        f"Processed **{total_records:,}** records across Razorpay, bank, and merchant sources. "
        f"**{assignments.get('auto_match', 0):,}** matched automatically, "
        f"**{assignments.get('ai_resolved_match', 0):,}** more resolved by the AI reasoner "
        f"(only after clearing the same checks every automatic match has to clear), and "
        f"**{assignments.get('exception', 0):,}** are sitting in the queue waiting on you."
    )
    lines.append("")

    if ev:
        lines.append(
            f"Of what got matched, **{ev.get('precision', 0) * 100:.1f}%** was actually correct "
            f"(checked against hidden ground truth) -- {ev.get('false_positives', 0)} wrong matches. "
            f"That's the number that matters most here: a wrong match costs real money to unwind, "
            f"an exception just costs someone a few minutes to review."
        )
        lines.append("")

    if fin:
        lines.append(
            f"₹{fin.get('reconciled_settled_inr', 0):,.2f} is confirmed reconciled. "
            f"₹{fin.get('unexplained_exposure_inr', 0):,.2f} is still unexplained -- "
            f"that's the number to actually look at, broken down by reason in the audit report."
        )
        lines.append("")

    if cash:
        lines.append(
            f"**Forward cash position: ₹{cash.get('forward_cash_position_inr', 0):,.2f}** "
            f"(reconciled balance + pending receivables, minus known outflows)."
        )
        lines.append("")

    if drift.get("drift_detected"):
        lines.append(
            "⚠️ **A shift in the score distribution was detected within this batch.** "
            "Worth a look before trusting this run's numbers as representative."
        )
        lines.append("")

    tax = _load(results_dir / "tax_reconciliation.json")
    if tax:
        s = tax["summary"]
        lines.append(
            f"**GST on Razorpay's fees:** {s['match_rate'] * 100:.1f}% of settlement batches "
            f"matched their invoice cleanly. ₹{s['findings_exposure_inr']:,.2f} in findings "
            f"(missing invoices, rate mismatches, or possible duplicate input-credit claims)."
        )
        lines.append("")

    route = _load(results_dir / "route_reconciliation.json")
    if route:
        s = route["summary"]
        if s["total_marketplace_payments"] > 0:
            lines.append(
                f"**Route marketplace splits:** {s['verification_rate'] * 100:.1f}% verified. "
                f"₹{s['shortfall_exposure_inr']:,.2f} in shortfalls (money that may never have "
                f"reached a vendor) and ₹{s['overpayment_exposure_inr']:,.2f} in overpayments."
            )
            lines.append("")

    lines.append("---")
    lines.append(
        "_Every number above is measured against this batch's actual data, not asserted. "
        "See AUDIT_REPORT.md for the full breakdown and the exception triage table._"
    )

    return "\n".join(lines)


def run_digest(results_dir: Path) -> str:
    text = build_digest(results_dir)
    (results_dir / "DIGEST.md").write_text(text, encoding="utf-8")
    return text
