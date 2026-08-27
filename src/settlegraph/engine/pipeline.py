"""Pipeline orchestrator: end-to-end reconciliation flow."""

from __future__ import annotations

import json
from pathlib import Path

from settlegraph.config import PipelineConfig
from settlegraph.engine.assign import classify_unmatched, global_assign
from settlegraph.engine.evaluate import evaluate as run_evaluate
from settlegraph.engine.ingest import load_all
from settlegraph.engine.match import build_candidate_graph
from settlegraph.engine.normalize import normalize_all
from settlegraph.engine.score import score_all
from settlegraph.engine.verify import verify_settlegraph_invariants
from settlegraph.models import NormalizedRecord


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

    data_path = Path(data_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Phase 1: Ingest
    print("[1/7] Ingesting sources...")
    rzp, bank, merchant = load_all(data_path)
    print(f"  Loaded {len(rzp)} Razorpay, {len(bank)} bank, {len(merchant)} merchant records")

    # Phase 2: Normalize
    print("[2/7] Normalizing records...")
    rzp_norm, bank_norm, merch_norm = normalize_all(rzp, bank, merchant)
    print(f"  Normalized {len(rzp_norm)} rzp, {len(bank_norm)} bank, {len(merch_norm)} merchant")

    # Phase 3: Build candidate graph
    print("[3/7] Building candidate graph...")
    candidates = build_candidate_graph(rzp_norm, bank_norm, merch_norm, config)
    print(f"  Generated {len(candidates)} candidate links")

    # Phase 4: Score edges
    print("[4/7] Scoring edges...")
    scored = score_all(candidates)
    high_conf = sum(1 for _, _, s in scored if s >= config.auto_match_threshold)
    print(f"  {high_conf} edges above match threshold ({config.auto_match_threshold})")

    # Phase 5: Global assignment
    print("[5/7] Running global assignment...")
    assignments = global_assign(scored, config)
    auto_matches = sum(1 for a in assignments if a["label"] == "AUTO_MATCH")
    exceptions = sum(1 for a in assignments if a["label"] == "EXCEPTION")
    likely = sum(1 for a in assignments if a["label"] == "LIKELY_MATCH")
    print(f"  {auto_matches} AUTO_MATCH, {likely} LIKELY_MATCH, {exceptions} EXCEPTION")

    # Phase 6: Invariant verification
    print("[6/7] Verifying invariants...")
    norm_map: dict[str, NormalizedRecord] = {
        r.record_id: r for r in rzp_norm + bank_norm + merch_norm
    }
    violations = 0
    for a in assignments:
        if a["label"] == "AUTO_MATCH":
            a_norm = norm_map.get(a["source_a_id"])
            b_norm = norm_map.get(a["source_b_id"])
            if a_norm and b_norm and {a_norm.source, b_norm.source} == {"razorpay", "bank"}:
                rzp = a_norm if a_norm.source == "razorpay" else b_norm
                bank = b_norm if b_norm.source == "bank" else a_norm
                v = verify_settlegraph_invariants(rzp, bank, config)
                violations += len(v)
    print(f"  {violations} invariant violations detected")

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

    non_auto = [a for a in assignments if a["label"] != "AUTO_MATCH"]
    exception_reports = generate_exception_reports(unmatched, non_auto, scored, norm_map)
    print(f"  Diagnosed {len(exception_reports)} exception root causes")

    # Revenue assurance
    rev_assurance = compute_revenue_assurance(
        rzp_norm, bank_norm, merch_norm, assignments, exception_reports
    )

    # Write outputs
    print("[7/7] Writing results...")

    # Assignments CSV
    if assignments:
        import csv as csv_mod

        fields = list(assignments[0].keys())
        with (output_path / "assignments.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv_mod.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(assignments)

    # Unmatched CSV
    if unmatched:
        with (output_path / "unmatched.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv_mod.DictWriter(fh, fieldnames=["source", "record_id", "status"])
            writer.writeheader()
            writer.writerows(unmatched)

    # Exceptions JSON
    with (output_path / "exceptions.json").open("w", encoding="utf-8") as fh:
        json.dump([r.to_dict() for r in exception_reports], fh, indent=2)

    # Revenue Assurance JSON
    with (output_path / "revenue_assurance.json").open("w", encoding="utf-8") as fh:
        json.dump(rev_assurance, fh, indent=2)

    # Summary JSON
    summary = {
        "records": {
            "razorpay": len(rzp_norm),
            "bank": len(bank_norm),
            "merchant": len(merch_norm),
        },
        "candidates": len(candidates),
        "assignments": {
            "auto_match": auto_matches,
            "likely_match": likely,
            "exception": exceptions,
            "total": len(assignments),
        },
        "unmatched": len(unmatched),
        "invariant_violations": violations,
        "revenue_assurance": rev_assurance,
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

        with (output_path / "evaluation.json").open("w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)

    # Generate Audit Report Markdown
    generate_markdown_audit_report(summary, rev_assurance, results, output_path)

    with (output_path / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(f"  Results written to {output_path}")

    return summary
