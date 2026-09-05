"""Adversarial tests: try to make the reconciliation engine confidently wrong.

Every case here runs through the REAL pipeline functions
(`build_candidate_graph` -> `score_all` -> `global_assign`) with a real
`PipelineConfig()` -- no mocking of thresholds or scoring. Where the engine
behaves safely (abstains as it should), the test asserts that safety
directly. Where it does not, the test asserts the ACTUAL observed behavior
and is marked with a `# KNOWN GAP:` comment describing what the system does
today versus what it should do. Weakening an assertion to hide a gap, or
"fixing" the engine here, is out of scope and defeats the point of this
module.
"""

from __future__ import annotations

from datagen.adversarial import (
    amount_tolerance_boundary,
    conflicting_sources_no_fee_evidence,
    date_tolerance_boundary,
    high_confidence_wrong_match,
    near_tie_scores,
    new_transaction_category,
    prompt_injection_in_description,
    twin_candidates_no_reference,
    unexpected_identifier_format,
    unicode_and_case_utr_drift,
    unusual_fee_structure,
    unusual_settlement_timing,
)
from settlegraph.config import PipelineConfig
from settlegraph.engine.assign import classify_unmatched, global_assign
from settlegraph.engine.match import build_candidate_graph
from settlegraph.engine.score import score_all, score_edge


def _run_pipeline(case, config: PipelineConfig) -> list[dict]:
    candidates = build_candidate_graph(case.rzp, case.bank, case.merchant, config)
    scored = score_all(candidates)
    return global_assign(scored, config)


def test_twin_candidates_no_reference_never_auto_matches() -> None:
    """Two same-amount bank credits one day apart, Razorpay side has no UTR.
    Genuinely ambiguous -- neither candidate may be AUTO_MATCH."""
    config = PipelineConfig()
    case = twin_candidates_no_reference()

    assignments = _run_pipeline(case, config)

    assert all(a["label"] != "AUTO_MATCH" for a in assignments)
    # Exactly one of the two ambiguous bank rows gets an (EXCEPTION) entry at
    # all -- exclusivity on the single rzp record means the loser is dropped
    # from `assignments` entirely, not demoted. Confirm it resurfaces as a
    # plain UNMATCHED record rather than silently vanishing.
    assert len(assignments) == 1
    assert assignments[0]["label"] == "EXCEPTION"
    unmatched = classify_unmatched(
        {r.record_id for r in case.rzp},
        {b.record_id for b in case.bank},
        set(),
        assignments,
    )
    unmatched_bank_ids = {u["record_id"] for u in unmatched if u["source"] == "bank"}
    assert len(unmatched_bank_ids) == 1


def test_near_tie_scores_abstain_instead_of_coin_flipping() -> None:
    """Two bank candidates for the same Razorpay record score, for all
    practical purposes, identically (both land on the 0.95 auto-match
    threshold, differing only by floating-point summation order).

    GAP CLOSED. This test previously documented a real defect: `global_assign`
    picked whichever edge floating-point rounding put a hair ahead, stamped
    it AUTO_MATCH, and the runner-up disappeared from the output entirely.
    Nothing anywhere lowered confidence in a winner because a near-identical
    competitor existed -- which meant the "no competing explanation" clause
    of this project's own rule for automation was documented but not
    implemented.

    `PipelineConfig.ambiguity_margin` (default 0.05) plus the near-tie
    suppression in `global_assign` now enforce it: a win by less than the
    margin is treated as a tie-break rather than evidence, and the record is
    held for review with the competition named in `abstention_reason`.
    """
    config = PipelineConfig()
    case = near_tie_scores()

    scores = [score_edge(r, b) for r in case.rzp for b in case.bank]
    assert abs(scores[0] - scores[1]) < 0.02, "fixture must produce a near-tie by construction"
    assert all(s >= config.auto_match_threshold for s in scores), (
        "fixture must construct both competing edges at/above the auto-match threshold"
    )

    assignments = _run_pipeline(case, config)

    assert len(assignments) == 1
    winner = assignments[0]
    assert winner["label"] == "LIKELY_MATCH", (
        "a coin-flip margin must be held for review, never auto-booked"
    )
    assert winner["competing_candidates"] >= 1
    assert "competing" in winner["abstention_reason"].lower()


def test_conflicting_sources_no_fee_evidence_forces_exception() -> None:
    """Bank shows 95,000 paise; gateway and merchant ledger both agree on
    100,000 paise; no fee record anywhere explains the gap. The rzp<->bank
    edge must classify as EXCEPTION, and the amount gap must never be
    silently netted against an invented fee."""
    config = PipelineConfig()
    case = conflicting_sources_no_fee_evidence()

    assignments = _run_pipeline(case, config)

    rzp_bank_edges = [
        a for a in assignments if {a["source_a"], a["source_b"]} == {"razorpay", "bank"}
    ]
    assert len(rzp_bank_edges) == 1
    assert rzp_bank_edges[0]["label"] == "EXCEPTION"
    assert rzp_bank_edges[0]["confidence"] < config.exception_threshold

    # The rzp<->merchant leg is a separate, internally-consistent agreement
    # (gateway and ERP/invoice both say 100,000) -- it is legitimate for that
    # leg to match confidently. This is not the ambiguity under test; it is
    # confirmation that the *bank* discrepancy specifically is what forces
    # the abstention, not a blanket refusal to match anything in the graph.
    rzp_merch_edges = [
        a for a in assignments if {a["source_a"], a["source_b"]} == {"razorpay", "merchant"}
    ]
    assert len(rzp_merch_edges) == 1
    assert rzp_merch_edges[0]["label"] in {"AUTO_MATCH", "LIKELY_MATCH"}


def test_reused_utr_collision_is_held_not_confidently_mismatched() -> None:
    """A UTR is reused across two unrelated Razorpay settlements with equal
    amount and date. Only one of them is the real counterpart of the single
    bank credit that exists.

    GAP CLOSED -- and this was the worst defect the adversarial corpus found.
    `build_candidate_graph` indexed Razorpay records into a plain
    `dict[utr] -> record`, so the second record silently **overwrote** the
    first before scoring ran. The true counterpart never became a candidate
    at all, and the bank credit was confidently AUTO_MATCHed (0.95: UTR +
    exact amount + same-day date) to the *wrong* payment, with nothing in
    the output signalling the collision. A confident, invariant-passing,
    factually wrong ledger entry is the single most dangerous output this
    system can produce, and it was reachable from an anomaly the generator
    already injects (`duplicate_utr_reuse`).

    Two changes close it: the UTR index keeps every colliding record
    (`match.py`), so the competition survives into scoring; and near-tie
    suppression (`assign.py`) refuses to auto-book when the runner-up is
    within `ambiguity_margin`. The outcome is an honest hold rather than a
    confident guess -- which is the entire thesis of this project.
    """
    config = PipelineConfig()
    case = high_confidence_wrong_match()
    rzp_true, rzp_impostor = case.rzp
    (bank,) = case.bank

    candidates = build_candidate_graph(case.rzp, case.bank, case.merchant, config)

    # Both colliding payments now reach the scorer; neither is silently lost.
    assert len(candidates) == 2
    candidate_rzp_ids = {a.record_id for a, _ in candidates}
    assert candidate_rzp_ids == {rzp_true.record_id, rzp_impostor.record_id}
    assert all(b.record_id == bank.record_id for _, b in candidates)

    assignments = _run_pipeline(case, config)

    # Exclusivity still admits at most one edge for the single bank credit,
    # but it is no longer booked: the collision makes it a tie-break, and a
    # tie-break is not evidence.
    assert len(assignments) == 1
    decision = assignments[0]
    assert decision["label"] != "AUTO_MATCH", (
        "a reused-UTR collision must never produce a confident auto-booking"
    )
    assert decision["competing_candidates"] >= 1
    assert "competing" in decision["abstention_reason"].lower()


def test_amount_tolerance_boundary_is_inclusive_and_deliberate() -> None:
    """diff=99 and diff=100 (== amount_tolerance_paise) both admit a
    candidate; diff=101 admits none. The boundary is `diff <= tolerance`,
    confirmed against the real engine rather than assumed."""
    config = PipelineConfig()
    cases = {c.name: c for c in amount_tolerance_boundary()}
    assert config.amount_tolerance_paise == 100

    for diff, expect_candidate in ((99, True), (100, True), (101, False)):
        case = cases[f"amount_tolerance_boundary_diff_{diff}"]
        candidates = build_candidate_graph(case.rzp, case.bank, case.merchant, config)
        assert (len(candidates) == 1) is expect_candidate, (
            f"diff={diff} candidate presence should be {expect_candidate}"
        )
        if expect_candidate:
            assignments = _run_pipeline(case, config)
            assert len(assignments) == 1
            # Neither boundary point is anywhere near auto-match: amount
            # tolerance alone (no UTR) tops out well below the 0.95 bar.
            assert assignments[0]["label"] != "AUTO_MATCH"


def test_date_tolerance_boundary_is_inclusive_and_deliberate() -> None:
    """delta=2 and delta=3 (== date_tolerance_days) both admit a candidate;
    delta=4 admits none, confirmed against the real engine. Also documents
    that the candidate-generation gate (config-driven) and score_edge's own
    internal date-proximity bucket (hard-coded <=5 days) are two different
    numbers that are not the same knob."""
    config = PipelineConfig()
    cases = {c.name: c for c in date_tolerance_boundary()}
    assert config.date_tolerance_days == 3

    for delta, expect_candidate in ((2, True), (3, True), (4, False)):
        case = cases[f"date_tolerance_boundary_delta_{delta}"]
        candidates = build_candidate_graph(case.rzp, case.bank, case.merchant, config)
        assert (len(candidates) == 1) is expect_candidate, (
            f"delta={delta} candidate presence should be {expect_candidate}"
        )
        if expect_candidate:
            assignments = _run_pipeline(case, config)
            assert len(assignments) == 1
            assert assignments[0]["label"] != "AUTO_MATCH"


def test_unicode_and_case_utr_drift_never_creates_a_candidate() -> None:
    """Case-only, leading-zero, and Unicode-homoglyph UTR drift must never
    be treated as identical -- zero candidate edges for each pair. Safe (no
    false match) but confirmed-blind: a corrupted-UTR record additionally
    gets no shot at the amount+date fallback, since that fallback is gated
    on *both* sides having no UTR at all. This is documented, not silently
    assumed."""
    config = PipelineConfig()
    cases = unicode_and_case_utr_drift()
    assert len(cases) == 3

    for case in cases:
        candidates = build_candidate_graph(case.rzp, case.bank, case.merchant, config)
        assert candidates == [], f"{case.name} must not produce a spurious candidate edge"

        # Confirm this isn't just an accident of the fallback path being
        # gated -- even scored directly, the mismatched UTR pair never
        # clears exception_threshold, so nothing is lost by never reaching
        # scoring in the real pipeline for this particular case.
        (rzp,) = case.rzp
        (bank,) = case.bank
        assert score_edge(rzp, bank) < config.exception_threshold


def test_prompt_injection_in_description_never_changes_the_label() -> None:
    """A description field containing instruction-shaped text must be
    treated purely as data. Verified directly against the real scorer and
    assignment pipeline, not assumed."""
    config = PipelineConfig()
    malicious_case, clean_case = prompt_injection_in_description()

    (rzp_evil,) = malicious_case.rzp
    (bank_evil,) = malicious_case.bank
    (rzp_clean,) = clean_case.rzp
    (bank_clean,) = clean_case.bank

    assert "IGNORE PREVIOUS RULES" in bank_evil.description
    assert "IGNORE PREVIOUS RULES" not in (bank_clean.description or "")

    malicious_score = score_edge(rzp_evil, bank_evil)
    clean_score = score_edge(rzp_clean, bank_clean)
    assert malicious_score == clean_score

    malicious_assignments = _run_pipeline(malicious_case, config)
    clean_assignments = _run_pipeline(clean_case, config)
    assert len(malicious_assignments) == len(clean_assignments) == 1
    assert malicious_assignments[0]["label"] == clean_assignments[0]["label"]
    assert malicious_assignments[0]["confidence"] == clean_assignments[0]["confidence"]
    # Sanity: the fixture isn't trivially abstaining regardless of content --
    # it actually reaches AUTO_MATCH, so the injected text had a real
    # opportunity to change the outcome and did not.
    assert malicious_assignments[0]["label"] == "AUTO_MATCH"


# --- OOD stress corpus: fee structure, settlement timing, transaction
# category, and identifier format. See docs/EVALUATION.md §8 for the
# previously-documented gap these four close.


def test_unusual_fee_structure_does_not_invent_a_fee_explanation() -> None:
    """UTR and date match perfectly; the bank credit is 9,764 paise short of
    the declared net (a flat ~10% surcharge/reserve model, not the
    generator's normal ~0.24% MDR band) with no fee record anywhere to
    explain it. The amount component must score 0 and the edge must never
    reach AUTO_MATCH."""
    config = PipelineConfig()
    case = unusual_fee_structure()
    (rzp,) = case.rzp
    (bank,) = case.bank

    score = score_edge(rzp, bank)
    # UTR (0.60) + date same-day (0.10) only -- the 9,764 paise gap earns
    # zero amount credit (not exact, not within 100-paise tolerance, not
    # within 1% of the declared net).
    assert score == 0.70

    assignments = _run_pipeline(case, config)
    assert len(assignments) == 1
    decision = assignments[0]
    assert decision["label"] != "AUTO_MATCH", (
        "an unexplained amount gap must never be silently netted against an "
        "invented fee and confidently booked"
    )
    assert decision["confidence"] < config.auto_match_threshold


def test_unusual_settlement_timing_drops_confidence_and_holds() -> None:
    """UTR and amount match exactly, but the bank credit lands T+30 --
    thirty days past the Razorpay settlement date, an order of magnitude
    past both date_tolerance_days (3) and score_edge's own 5-day
    partial-credit bucket. Date proximity must contribute 0 to the score."""
    config = PipelineConfig()
    case = unusual_settlement_timing()
    (rzp,) = case.rzp
    (bank,) = case.bank

    score = score_edge(rzp, bank)
    # UTR (0.60) + exact amount (0.25) only -- 30 days clears every date
    # bucket in score_edge, so date proximity contributes exactly 0.
    assert score == 0.85
    assert score < config.auto_match_threshold

    assignments = _run_pipeline(case, config)
    assert len(assignments) == 1
    decision = assignments[0]
    assert decision["label"] != "AUTO_MATCH", (
        "a settlement timing regime the generator never produces must drop "
        "confidence below the auto-match bar, not be booked on UTR+amount alone"
    )
    assert decision["label"] == "LIKELY_MATCH"


def test_new_transaction_category_is_confidently_auto_matched_anyway() -> None:
    """An `adjustment`-type Razorpay record (a category `score_edge` has no
    dedicated path for) matched against an ordinary bank `settlement_credit`,
    with UTR, amount, and date all otherwise perfect.

    KNOWN GAP: `record_type` is invisible to both `build_candidate_graph` and
    `score_edge` -- the score is byte-identical to the ordinary-`payment`
    happy path (0.60 UTR + 0.25 exact amount + 0.10 same-day = 0.95), which
    clears the auto-match threshold with no competing candidate.
    `global_assign` emits a confident AUTO_MATCH for a transaction category
    the system has never validated a scoring path against -- exactly the
    "confident hallucination on an unfamiliar pattern" failure mode this
    module exists to catch. This is NOT the same as the already-enforced
    `verify_direction_invariant` (which checks the bank record's
    `provenance["direction"]`, not the Razorpay side's `record_type`) and is
    not caught anywhere in the `build_candidate_graph -> score_all ->
    global_assign` path under test here.
    """
    config = PipelineConfig()
    case = new_transaction_category()
    (rzp,) = case.rzp
    (bank,) = case.bank
    assert rzp.record_type == "adjustment"

    score = score_edge(rzp, bank)
    assert score == 0.95, (
        "score is identical to an ordinary payment's -- record_type is invisible to score_edge"
    )

    assignments = _run_pipeline(case, config)
    assert len(assignments) == 1
    decision = assignments[0]
    # KNOWN GAP: this SHOULD abstain (no validated scoring path for this
    # category) but the real engine confidently auto-matches it. Asserting
    # the actual behavior, not the ideal one.
    assert decision["label"] == "AUTO_MATCH"
    assert decision["confidence"] == 0.95
    assert decision["competing_candidates"] == 0


def test_unexpected_identifier_format_abstains_at_the_cost_of_recall() -> None:
    """rzp_fmt and bank_fmt are the TRUE counterpart of each other (same
    amount, same date) but carry UTRs in unrelated shapes -- Razorpay's own
    short alphanumeric convention versus an unfamiliar, longer,
    separator-embedded bank export reference. Confirmed against the real
    engine: zero candidate edges are produced, so the record ends up plainly
    UNMATCHED rather than confidently mismatched. Safe, but a genuine recall
    cost -- documented as the correct trade, not asserted as cost-free."""
    config = PipelineConfig()
    case = unexpected_identifier_format()
    (rzp,) = case.rzp
    (bank,) = case.bank

    assert rzp.utr != bank.utr
    assert len(rzp.utr) != len(bank.utr), "fixture must genuinely differ in shape, not just content"

    candidates = build_candidate_graph(case.rzp, case.bank, case.merchant, config)
    assert candidates == [], (
        "an unfamiliar identifier format on the true counterpart must never "
        "be forced into a candidate edge, but is correctly never even considered"
    )

    assignments = _run_pipeline(case, config)
    assert assignments == []
    unmatched = classify_unmatched(
        {r.record_id for r in case.rzp},
        {b.record_id for b in case.bank},
        set(),
        assignments,
    )
    unmatched_ids = {u["record_id"] for u in unmatched}
    assert unmatched_ids == {rzp.record_id, bank.record_id}, (
        "the true counterpart is missed entirely, not confidently mismatched "
        "-- the correct trade for a system whose thesis is 'never a "
        "confident wrong answer', but a real recall cost worth naming"
    )
