"""Exception investigation engine: root-cause diagnosis for reconciliation discrepancies."""

from __future__ import annotations

from typing import Any

from settlegraph.models import NormalizedRecord


def _levenshtein(a: str, b: str) -> int:
    """Minimal edit distance between two strings. No dependency needed for this."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


class ExceptionReport:
    """Detailed diagnostic report for an un-reconciled record or exception assignment."""

    def __init__(
        self,
        record_id: str,
        source: str,
        category: str,
        severity: str,
        root_cause: str,
        unexplained_amount_paise: int,
        suggested_action: str,
        evidence: dict[str, Any],
    ):
        self.record_id = record_id
        self.source = source
        self.category = category  # e.g., "UTR_CORRUPTION", "AMOUNT_MISMATCH", "REFUND_ADJUSTMENT"
        self.severity = severity  # "HIGH", "MEDIUM", "LOW"
        self.root_cause = root_cause
        self.unexplained_amount_paise = unexplained_amount_paise
        self.suggested_action = suggested_action
        self.evidence = evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "source": self.source,
            "category": self.category,
            "severity": self.severity,
            "root_cause": self.root_cause,
            "unexplained_amount_paise": self.unexplained_amount_paise,
            "suggested_action": self.suggested_action,
            "evidence": self.evidence,
        }


def investigate_exception(
    record: NormalizedRecord,
    candidate_links: list[tuple[NormalizedRecord, float]],
    norm_map: dict[str, NormalizedRecord],
) -> ExceptionReport:
    """Diagnose the root cause why a record could not be cleanly AUTO_MATCHed.

    Categories:
    1. UTR_ANOMALY: UTR was corrupted or missing in one of the sources.
    2. FEE_TAX_DISCREPANCY: Net bank credit differs from Razorpay net settlement.
    3. TIMING_DIFFERENCE: Transaction settled outside normal banking window.
    4. PARTIAL_REFUND: Net amount reduced due to customer refund deduction.
    5. MISSING_COUNTERPART: Record exists in one ledger but is completely absent in counterpart.
    """
    if not candidate_links:
        return ExceptionReport(
            record_id=record.record_id,
            source=record.source,
            category="MISSING_COUNTERPART",
            severity="HIGH",
            root_cause=f"No plausible candidate link found across sources for {record.source} record.",
            unexplained_amount_paise=record.amount_paise,
            suggested_action="Verify transaction existence with counterparty payment gateway or bank.",
            evidence={"amount_paise": record.amount_paise, "date": str(record.transaction_date)},
        )

    best_counterpart, score = max(candidate_links, key=lambda x: x[1])

    # Check for UTR anomaly. A single corrupted trailing character was the
    # only shape this used to catch (`record.utr[:-1] == counterpart.utr[:-1]`);
    # that misses multi-character corruption and transpositions, which are
    # exactly as plausible a bank-side data-entry failure. Edit distance
    # catches both while a length-difference cap keeps it from calling two
    # genuinely different UTRs "corrupted" of each other.
    if record.utr and best_counterpart.utr and record.utr != best_counterpart.utr:
        distance = _levenshtein(record.utr, best_counterpart.utr)
        length_gap = abs(len(record.utr) - len(best_counterpart.utr))
        if distance <= 2 and length_gap <= 1:
            return ExceptionReport(
                record_id=record.record_id,
                source=record.source,
                category="UTR_CORRUPTION",
                severity="MEDIUM",
                root_cause=(
                    f"UTR mismatch due to character corruption (edit distance {distance}): "
                    f"'{record.utr}' vs '{best_counterpart.utr}'."
                ),
                unexplained_amount_paise=abs(record.amount_paise - best_counterpart.amount_paise),
                suggested_action="Review fuzzy UTR match and confirm manual linkage.",
                evidence={
                    "record_utr": record.utr,
                    "counterpart_utr": best_counterpart.utr,
                    "edit_distance": distance,
                    "score": score,
                },
            )

    if (record.utr is None) ^ (best_counterpart.utr is None):
        return ExceptionReport(
            record_id=record.record_id,
            source=record.source,
            category="MISSING_UTR",
            severity="MEDIUM",
            root_cause=f"One record is missing UTR reference while counterpart contains '{record.utr or best_counterpart.utr}'.",
            unexplained_amount_paise=abs(record.amount_paise - best_counterpart.amount_paise),
            suggested_action="Enrich bank statement reference from gateway settlement webhook.",
            evidence={"score": score},
        )

    # Check for Amount / Fee discrepancy
    diff = abs(
        (record.net_amount_paise or record.amount_paise)
        - (best_counterpart.net_amount_paise or best_counterpart.amount_paise)
    )
    if diff > 100:  # greater than 1 rupee
        # Check if difference looks like a refund deduction
        if best_counterpart.amount_paise < record.amount_paise:
            return ExceptionReport(
                record_id=record.record_id,
                source=record.source,
                category="REFUND_OR_FEE_DEDUCTION",
                severity="MEDIUM",
                root_cause=f"Net settlement credit is ₹{diff / 100:.2f} lower than expected gross amount (potential refund/chargeback deduction).",
                unexplained_amount_paise=diff,
                suggested_action="Cross-reference with Razorpay refund logs and credit note adjustments.",
                evidence={
                    "expected_amount_paise": record.net_amount_paise or record.amount_paise,
                    "observed_amount_paise": best_counterpart.net_amount_paise
                    or best_counterpart.amount_paise,
                    "difference_paise": diff,
                },
            )
        else:
            return ExceptionReport(
                record_id=record.record_id,
                source=record.source,
                category="AMOUNT_MISMATCH",
                severity="HIGH",
                root_cause=f"Discrepancy of ₹{diff / 100:.2f} between source and counterpart records.",
                unexplained_amount_paise=diff,
                suggested_action="Inspect fee/MDR schedule or banking adjustment entries.",
                evidence={"diff_paise": diff, "score": score},
            )

    # Timing difference
    date_a = record.settlement_date or record.transaction_date
    date_b = best_counterpart.settlement_date or best_counterpart.transaction_date
    if date_a and date_b and abs((date_a - date_b).days) > 3:
        return ExceptionReport(
            record_id=record.record_id,
            source=record.source,
            category="TIMING_DIFFERENCE",
            severity="LOW",
            root_cause=f"Settlement timing delay of {abs((date_a - date_b).days)} days between gateway and bank statement.",
            unexplained_amount_paise=0,
            suggested_action="Accept as timing difference in transit / bank holiday settlement cycle.",
            evidence={"date_a": str(date_a), "date_b": str(date_b)},
        )

    return ExceptionReport(
        record_id=record.record_id,
        source=record.source,
        category="AMBIGUOUS_MATCH",
        severity="MEDIUM",
        root_cause=f"Confidence score {score:.2f} did not clear the strict auto-match threshold (0.95).",
        unexplained_amount_paise=record.amount_paise,
        suggested_action="Human clerical review required to accept or reject candidate linkage.",
        evidence={"best_match_id": best_counterpart.record_id, "score": score},
    )


def generate_exception_reports(
    unmatched_records: list[dict],
    non_auto_assignments: list[dict],
    candidates: list[tuple[NormalizedRecord, NormalizedRecord, float]],
    norm_map: dict[str, NormalizedRecord],
) -> list[ExceptionReport]:
    """Generate structured diagnostic reports for all exceptions and unmatched records."""
    candidate_map: dict[str, list[tuple[NormalizedRecord, float]]] = {}
    for a, b, score in candidates:
        if a.record_id not in candidate_map:
            candidate_map[a.record_id] = []
        candidate_map[a.record_id].append((b, score))
        if b.record_id not in candidate_map:
            candidate_map[b.record_id] = []
        candidate_map[b.record_id].append((a, score))

    reports: list[ExceptionReport] = []

    # Process non-auto assignments (LIKELY_MATCH and EXCEPTION)
    for a in non_auto_assignments:
        a_norm = norm_map.get(a["source_a_id"])
        b_norm = norm_map.get(a["source_b_id"])
        if a_norm and b_norm:
            report = investigate_exception(a_norm, [(b_norm, a["confidence"])], norm_map)
            reports.append(report)

    # Process orphans
    for u in unmatched_records:
        rec = norm_map.get(u["record_id"])
        if rec:
            links = candidate_map.get(rec.record_id, [])
            report = investigate_exception(rec, links, norm_map)
            reports.append(report)

    return reports
