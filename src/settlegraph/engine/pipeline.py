"""Pipeline orchestrator: end-to-end reconciliation flow."""

from __future__ import annotations

import time
from pathlib import Path

from settlegraph.config import PipelineConfig
from settlegraph.engine.assign import classify_unmatched, global_assign
from settlegraph.engine.atomic_io import write_csv, write_json
from settlegraph.engine.drift import ADWINDetector
from settlegraph.engine.evaluate import evaluate as run_evaluate
from settlegraph.engine.exceptions import ExceptionReport
from settlegraph.engine.idempotency import IdempotencyShield
from settlegraph.engine.ingest import load_all_with_quarantine
from settlegraph.engine.match import build_candidate_graph
from settlegraph.engine.normalize import normalize_all
from settlegraph.engine.score import score_all
from settlegraph.engine.verify import verify_settlegraph_invariants
from settlegraph.models import NormalizedRecord

_INVARIANT_GATED_LABELS = {"AUTO_MATCH"}


def deduplicate_normalized_records(
    rzp: list[NormalizedRecord],
    bank: list[NormalizedRecord],
    merchant: list[NormalizedRecord],
) -> tuple[list[NormalizedRecord], list[NormalizedRecord], list[NormalizedRecord], list[dict]]:
    """Run every normalized source through `IdempotencyShield` before the
    candidate graph ever sees it.

    ADR 0004 ("Cryptographic SHA-256 Idempotency Shield") claims this
    "guarantees zero duplicate ledger entries under network retry storms."
    Until now that was true of `IdempotencyShield` in isolation -- it was
    fully built and unit-tested (`tests/test_idempotency.py`,
    `tests/test_e2e.py::test_idempotency_shield_stream_e2e`,
    `simulator.py`'s FAIL_01 scenario) but `run_pipeline` never called it, so
    a retried settlement webhook landing twice in the real ingest CSVs flowed
    straight through scoring, matching, and revenue-assurance totals
    ungated. Found by code review.

    One shield instance across all three sources: `compute_fingerprint`
    includes `record.source`, so a razorpay and a bank record can never
    collide on fingerprint, and sharing the instance costs nothing while
    keeping one duplicate log instead of three.
    """
    shield = IdempotencyShield()
    rzp_unique, _ = shield.filter_duplicates(rzp)
    bank_unique, _ = shield.filter_duplicates(bank)
    merch_unique, _ = shield.filter_duplicates(merchant)
    return rzp_unique, bank_unique, merch_unique, shield.intercepted_duplicates


def enforce_invariant_gate(
    assignments: list[dict],
    norm_map: dict[str, NormalizedRecord],
    config: PipelineConfig,
) -> tuple[list[dict], list[ExceptionReport], int]:
    """Demote any AUTO_MATCH that fails `verify_settlegraph_invariants` to
    EXCEPTION, in place, and return a diagnostic report naming the actual
    violation for each one demoted.

    Before this existed, `verify_settlegraph_invariants` ran on every
    AUTO_MATCH and its violation count was reported in
    `summary["invariant_violations"]`, but nothing acted on that count -- the
    assignment kept its AUTO_MATCH label regardless of the result. That
    directly contradicted `report.py`'s own audit-report line ("any violation
    demotes to exception queue") and AGENTS.md's non-negotiable "Precision =
    100.0%" invariant: scoring never looks at bank-credit direction, so a
    debit line (a refund payout, say) sharing a UTR/amount/date window with a
    real settlement could clear the auto-match threshold and still fail
    `verify_direction_invariant`. Found by code review, not by the test suite
    -- the existing e2e test only asserts aggregate precision on generated
    data that happens not to hit this path.

    A dedicated `INVARIANT_VIOLATION` category is used rather than routing
    through `investigate_exception` -- that function has no concept of an
    invariant failure and would misattribute a high-confidence, invariant-
    failing match as "confidence did not clear the auto-match threshold",
    which is false and would mislead whoever reads the exception queue next.

    Scoped to razorpay<->bank pairs, matching `verify_settlegraph_invariants`
    itself (see its own docstring) -- a razorpay<->merchant AUTO_MATCH passes
    through unexamined.
    """
    reports: list[ExceptionReport] = []
    violation_count = 0
    for a in assignments:
        if a["label"] not in _INVARIANT_GATED_LABELS:
            continue
        a_norm = norm_map.get(a["source_a_id"])
        b_norm = norm_map.get(a["source_b_id"])
        if not (a_norm and b_norm and {a_norm.source, b_norm.source} == {"razorpay", "bank"}):
            continue
        rzp = a_norm if a_norm.source == "razorpay" else b_norm
        bank = b_norm if b_norm.source == "bank" else a_norm

        violations = verify_settlegraph_invariants(rzp, bank, config)
        if not violations:
            continue

        violation_count += len(violations)
        original_label = a["label"]
        a["label"] = "EXCEPTION"
        reports.append(
            ExceptionReport(
                record_id=rzp.record_id,
                source=rzp.source,
                category="INVARIANT_VIOLATION",
                severity="HIGH",
                root_cause=(
                    f"Cleared confidence scoring ({a['confidence']}, originally {original_label}) "
                    f"but failed deterministic invariant verification: {violations[0]}"
                ),
                unexplained_amount_paise=rzp.amount_paise,
                suggested_action=(
                    "Do not auto-book. A high confidence score does not override a failed "
                    "accounting invariant -- investigate manually."
                ),
                evidence={
                    "violations": [str(v) for v in violations],
                    "counterpart_record_id": bank.record_id,
                    "original_label": original_label,
                },
            )
        )
    return assignments, reports, violation_count


def run_pipeline(
    data_dir: Path | str = "data/generated",
    output_dir: Path | str = "results",
    config: PipelineConfig | None = None,
) -> dict:
    """Run the full reconciliation pipeline.

    Flow: ingest -> normalize -> candidate graph -> score -> assign -> verify -> evaluate
    """
    if config is None:
        config = PipelineConfig()

    started_at = time.perf_counter()
    data_path = Path(data_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Phase 1: Ingest. `load_all_with_quarantine` (not the plain `load_all`
    # every other caller uses) so a row that fails Pydantic validation --
    # a bad timestamp, a failed net_must_balance invariant, whatever -- is
    # quarantined instead of either aborting the whole source file's ingest
    # or, worse, being silently dropped. See engine/ingest.py's module
    # docstring for the measured chaos_batch.py evidence this replaces.
    # Quarantine must be LOUD: reported in the console log, written in full
    # to results/quarantine.json, and counted in summary.json so a batch
    # with quarantined rows can never look identical to a clean one.
    print("[1/7] Ingesting sources...")
    rzp, bank, merchant, quarantined_rows = load_all_with_quarantine(data_path)
    print(f"  Loaded {len(rzp)} Razorpay, {len(bank)} bank, {len(merchant)} merchant records")
    if quarantined_rows:
        print(f"  Quarantined {len(quarantined_rows)} unparseable row(s) -- see quarantine.json")
        write_json(output_path / "quarantine.json", [q.to_dict() for q in quarantined_rows])

    # Phase 2: Normalize
    print("[2/7] Normalizing records...")
    rzp_norm, bank_norm, merch_norm = normalize_all(rzp, bank, merchant)
    print(f"  Normalized {len(rzp_norm)} rzp, {len(bank_norm)} bank, {len(merch_norm)} merchant")

    # Phase 2.5: Idempotency shield -- filter duplicate ingestion events
    # (retried webhooks, re-sent batch files) before anything downstream
    # can see them. See `deduplicate_normalized_records`'s own docstring for
    # why this wasn't previously wired in despite ADR 0004's claim.
    rzp_norm, bank_norm, merch_norm, duplicate_events = deduplicate_normalized_records(
        rzp_norm, bank_norm, merch_norm
    )
    if duplicate_events:
        print(f"  Intercepted {len(duplicate_events)} duplicate record(s) -- see duplicates.json")
        write_json(output_path / "duplicates.json", duplicate_events)

    # Phase 3: Build candidate graph
    print("[3/7] Building candidate graph...")
    candidates = build_candidate_graph(rzp_norm, bank_norm, merch_norm, config)
    print(f"  Generated {len(candidates)} candidate links")

    # Phase 4: Score edges
    print("[4/7] Scoring edges...")
    scored = score_all(candidates)
    high_conf = sum(1 for _, _, s in scored if s >= config.auto_match_threshold)
    print(f"  {high_conf} edges above match threshold ({config.auto_match_threshold})")

    # Concept-drift check over the confidence-score stream (ADWIN, Bifet &
    # Gavaldà 2007 -- engine/drift.py was fully implemented and never wired
    # into anything). Run in candidate-generation order, which follows
    # ingestion order: a detected split point flags a batch where the score
    # distribution shifted partway through -- e.g. a settlement-cycle
    # cutover or a data-quality regression -- rather than staying uniform
    # across the whole run. Scoped honestly: this is a within-batch
    # distributional check, not cross-run production monitoring, which
    # would need the detector's window persisted between pipeline
    # invocations and isn't built.
    #
    # `add_element` recomputes every candidate split point's mean from
    # scratch (O(window) per call), so calling it once per scored edge is
    # O(total_edges * window) -- measured: a 5,000-record batch (~17,000
    # scored edges) took 100s wall-clock, and profiling pointed almost all
    # of it here, not at candidate generation. That is real "stress test it
    # until it breaks" material. The detector's own contract and tests are
    # untouched; only how densely the pipeline feeds it changes -- a bounded
    # stride keeps the number of `add_element` calls constant regardless of
    # batch size, and a smaller window is still enough signal for a
    # within-batch shift.
    drift_detector = ADWINDetector(max_window_size=200)
    sample_stride = max(1, len(scored) // 2000)
    for i, (_, _, s) in enumerate(scored):
        if i % sample_stride == 0:
            drift_detector.add_element(s)

    # Phase 5: Global assignment
    print("[5/7] Running global assignment...")
    assignments = global_assign(scored, config)
    auto_matches = sum(1 for a in assignments if a["label"] == "AUTO_MATCH")
    exceptions = sum(1 for a in assignments if a["label"] == "EXCEPTION")
    likely = sum(1 for a in assignments if a["label"] == "LIKELY_MATCH")
    print(f"  {auto_matches} AUTO_MATCH, {likely} LIKELY_MATCH, {exceptions} EXCEPTION")

    # Phase 6: Invariant verification. Any AUTO_MATCH that fails a
    # deterministic invariant (amount, date, credit direction) is demoted to
    # EXCEPTION here, in place -- scoring alone is not the auto-match gate,
    # this is. See `enforce_invariant_gate`'s own docstring for why this
    # wasn't previously true despite the audit report claiming it was.
    print("[6/7] Verifying invariants...")
    norm_map: dict[str, NormalizedRecord] = {
        r.record_id: r for r in rzp_norm + bank_norm + merch_norm
    }
    assignments, invariant_exception_reports, violations = enforce_invariant_gate(
        assignments, norm_map, config
    )
    demoted_ids = {r.record_id for r in invariant_exception_reports}
    demoted_note = ""
    if demoted_ids:
        auto_matches = sum(1 for a in assignments if a["label"] == "AUTO_MATCH")
        exceptions = sum(1 for a in assignments if a["label"] == "EXCEPTION")
        demoted_note = f" ({len(demoted_ids)} AUTO_MATCH demoted to EXCEPTION)"
    print(f"  {violations} invariant violations detected{demoted_note}")

    # Phase 7: Unmatched records
    unmatched = classify_unmatched(
        {r.record_id for r in rzp_norm},
        {r.record_id for r in bank_norm},
        {r.record_id for r in merch_norm},
        assignments,
    )
    print(f"  {len(unmatched)} unmatched records")

    # Diagnose exceptions
    from settlegraph.engine.exceptions import generate_exception_reports
    from settlegraph.engine.report import compute_revenue_assurance, generate_markdown_audit_report

    # Gate-demoted records already have their own diagnostic report (naming
    # the actual invariant failure) from `enforce_invariant_gate` -- excluded
    # here so `investigate_exception` doesn't also generate a second, generic
    # report for the same record_id that would misattribute the cause as low
    # confidence.
    non_auto = [
        a
        for a in assignments
        if a["label"] != "AUTO_MATCH"
        and a["source_a_id"] not in demoted_ids
        and a["source_b_id"] not in demoted_ids
    ]
    exception_reports = generate_exception_reports(unmatched, non_auto, scored, norm_map)
    exception_reports.extend(invariant_exception_reports)
    print(f"  Diagnosed {len(exception_reports)} exception root causes")

    # Phase 7.5: AI-assisted resolution (optional, off by default). Gated
    # behind config.llm_provider so the default pipeline run -- and the
    # entire test suite -- never touches the network or needs a key.
    ai_resolved_count = 0
    if config.llm_provider == "claude":
        from settlegraph.engine.ai_reasoner import merge_ai_assignments, resolve_exceptions_with_ai

        print("[7.5/7] Attempting AI-assisted resolution of unresolved exceptions...")
        ai_assignments, exception_reports, ai_log = resolve_exceptions_with_ai(
            exception_reports, scored, norm_map, bank_norm, config
        )
        ai_resolved_count = len(ai_assignments)
        assignments = merge_ai_assignments(assignments, ai_assignments)
        print(
            f"  AI-resolved: {ai_resolved_count} exception(s) promoted after "
            "passing invariant verification"
        )
        write_json(output_path / "ai_resolutions.json", ai_log)

    # Revenue assurance
    rev_assurance = compute_revenue_assurance(
        rzp_norm, bank_norm, merch_norm, assignments, exception_reports
    )

    # Write outputs
    print("[7/7] Writing results...")

    # Assignments CSV
    if assignments:
        fields = list(assignments[0].keys())
        write_csv(output_path / "assignments.csv", fields, assignments)

    # Unmatched CSV
    if unmatched:
        write_csv(
            output_path / "unmatched.csv",
            ["source", "record_id", "status"],
            unmatched,
        )

    # Exceptions JSON
    write_json(output_path / "exceptions.json", [r.to_dict() for r in exception_reports])

    # Revenue Assurance JSON
    write_json(output_path / "revenue_assurance.json", rev_assurance)

    # Summary JSON
    summary = {
        "records": {
            "razorpay": len(rzp_norm),
            "bank": len(bank_norm),
            "merchant": len(merch_norm),
        },
        "quarantined_records": len(quarantined_rows),
        "duplicates_intercepted": len(duplicate_events),
        "candidates": len(candidates),
        "assignments": {
            "auto_match": auto_matches,
            "likely_match": likely,
            "exception": exceptions,
            "ai_resolved_match": ai_resolved_count,
            "total": len(assignments),
        },
        "unmatched": len(unmatched),
        "invariant_violations": violations,
        "revenue_assurance": rev_assurance,
        "drift": {
            # `drift_detector.drift_detected` only reflects the *last*
            # `add_element` call (it resets to False at the top of every
            # call); `drift_history` is what actually accumulates across
            # the whole feed. See engine/history_store.py's
            # check_cross_run_drift for the same fix and DEVLOG Day 5 for
            # how this was found.
            "drift_detected": len(drift_detector.drift_history) > 0,
            "drift_events": drift_detector.drift_history,
        },
    }

    # Evaluate if ground truth exists
    gt_path = data_path / "ground_truth.csv"
    results = None
    if gt_path.exists():
        print("\n[Evaluation] Comparing against ground truth...")
        results = run_evaluate(output_path / "assignments.csv", gt_path)
        summary["evaluation"] = results
        print(f"  Precision: {results['precision']}")
        print(f"  Recall:    {results['recall']}")
        print(f"  F1 Score:  {results['f1']}")

        write_json(output_path / "evaluation.json", results)

    # Satellite reconciliation surfaces (tax-line, Route splits) -- run
    # automatically as part of the one existing pipeline entry point rather
    # than requiring separate CLI commands, whenever `generate` wrote their
    # feeds. This is the same rail every other output already uses: read
    # from data_path, write into output_path, and let
    # generate_markdown_audit_report (which already reads straight from
    # output_path for the exception triage table) pick them up the same
    # way. `settlegraph tax-match` / `route-reconcile` still exist for
    # re-running just one surface without a full pipeline run.
    if (data_path / "gst_invoices.csv").exists():
        from settlegraph.engine.tax_matcher import run_tax_reconciliation

        print("\n[Tax-Line] Reconciling GST on Razorpay fees...")
        summary["tax_reconciliation"] = run_tax_reconciliation(data_path, output_path)
        print(f"  Match rate: {summary['tax_reconciliation']['match_rate'] * 100:.1f}%")

    if (data_path / "route_payouts.csv").exists():
        from settlegraph.engine.route_reconciliation import run_route_reconciliation

        print("\n[Route] Reconciling marketplace payout splits...")
        summary["route_reconciliation"] = run_route_reconciliation(data_path, output_path, config)
        print(f"  Verified: {summary['route_reconciliation']['verification_rate'] * 100:.1f}%")

    # Generate Audit Report Markdown
    generate_markdown_audit_report(summary, rev_assurance, results, output_path)

    elapsed_seconds = time.perf_counter() - started_at
    total_input_records = len(rzp_norm) + len(bank_norm) + len(merch_norm)
    summary["throughput"] = {
        "elapsed_seconds": round(elapsed_seconds, 4),
        "records_processed": total_input_records,
        "records_per_second": round(total_input_records / elapsed_seconds, 1)
        if elapsed_seconds > 0
        else None,
    }

    # Generate the run id before summary.json is written, not after --
    # otherwise the persisted file never carries its own history.jsonl row
    # key, and a reader of summary.json alone (the dashboard, the digest,
    # qa_agent's get_summary tool) has no way to correlate this batch with
    # its run-history row. Found by code review.
    run_id: str | None = None
    if config.history_enabled:
        import uuid

        run_id = str(uuid.uuid4())
        summary["run_id"] = run_id

    write_json(output_path / "summary.json", summary)

    # Plain-language digest -- last, since it summarizes everything above
    # and reads summary.json back from disk the same way the audit report
    # reads exceptions.json/tax_reconciliation.json/etc.: it needs the file
    # to actually be written first, not just held in the in-memory `summary`
    # dict a moment earlier.
    from settlegraph.engine.digest import run_digest

    run_digest(output_path)

    if config.history_enabled:
        from settlegraph.engine.history_store import record_run

        record_run(output_path / "history.jsonl", summary, run_id=run_id)

    print(f"  Results written to {output_path}")
    print(
        f"  Throughput: {total_input_records} records in {elapsed_seconds:.2f}s "
        f"({summary['throughput']['records_per_second']} rec/s)"
    )

    return summary
