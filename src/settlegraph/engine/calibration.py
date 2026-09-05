"""Confidence-quality and decision-stability metrics.

`evaluate.py` answers "did the matcher find the right records?" This module
answers a different, and in some ways more important, question: "when the
matcher says 0.90, does that number mean anything?" A reconciliation system
that displays confidence scores in its UI is making an implicit promise --
that a human looking at a batch of 0.98s and a batch of 0.73s can trust the
first batch more than the second, in proportion to the gap. Nothing in this
codebase checked that promise before this file existed. It is entirely
possible to have perfect precision/recall (every AUTO_MATCH is correct) while
the confidence numbers themselves are meaningless noise that happens to
correlate with the auto-match threshold and nothing else. Precision/recall
cannot catch that; calibration metrics can.

The second thing this file checks is a different kind of trust: does the
pipeline behave the same way twice on the same input? A deterministic
reconciliation engine that reruns and produces a different set of AUTO_MATCH
decisions from identical evidence is not safe to put in front of an
accountant, no matter how good its precision/recall numbers looked on the
run that got measured. `compute_replay_consistency` is the executable check
for that claim rather than an assertion buried in a design doc.

Everything here is read-only and stdlib-only (csv, pathlib) -- no numpy, no
sklearn, no scipy, consistent with the rest of this project's
zero-runtime-dependency discipline. The id-stripping convention
(`rzp_norm_` / `bank_norm_` prefixes) and the ground-truth loading shape are
copied verbatim from `evaluate.py` rather than reinvented, because a second,
slightly-different implementation of "how do you map an assignment row back
to a ground-truth record" is exactly the kind of thing that quietly drifts
and produces two evaluators that disagree with each other for no principled
reason. `_load_assignments` and `_load_ground_truth` are imported directly
from `evaluate.py` for the same reason: one source of truth for "what a CSV
row looks like."
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from settlegraph.engine.evaluate import _load_assignments, _load_ground_truth, _strip

_strip_prefix = _strip


def _build_gt_map(truth: list[dict[str, str]]) -> dict[str, list[str]]:
    """razorpay_record_id -> list of true bank record ids.

    Copied from the same logic in `evaluate.evaluate()` (not
    `count_correctly_flagged_for_review`, which uses the older
    `.split("|")` on a possibly-empty string and would produce `[""]`
    instead of `[]` for a `no_counterpart` record). An empty
    `true_bank_record_ids` cell must map to `[]`, not `[""]` -- otherwise a
    fabricated match against a `no_counterpart` record could accidentally
    compare equal to the empty string and be scored "correct," which would
    be exactly backwards for the one relationship type where the honest
    answer is "there is nothing to match."
    """
    gt_map: dict[str, list[str]] = {}
    for row in truth:
        rzp_id = row["razorpay_record_id"]
        raw = row["true_bank_record_ids"]
        gt_map[rzp_id] = raw.split("|") if raw else []
    return gt_map


def _iter_rzp_bank_rows(
    assignments: list[dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any]]]:
    """Every razorpay<->bank assignment row, normalized to (rzp_orig, bank_orig, row).

    `assignments.csv` holds three kinds of pairing (razorpay<->bank,
    razorpay<->merchant, bank<->merchant) because the matcher reconciles
    across all three sources, not just the two this module cares about.
    Confidence calibration and abstention quality are both specifically
    about the razorpay<->bank leg -- that is the leg ground truth
    (`ground_truth.csv`) is keyed against, via `razorpay_record_id` and
    `true_bank_record_ids`. Filtering here rather than in every caller
    keeps that filter defined in exactly one place.

    The id-stripping convention matches `evaluate.py` exactly: assignment
    ids are the *normalized join ids* the matcher works with
    (`rzp_norm_pay_1`, `bank_norm_bank_1`), while ground truth is keyed by
    the *original record ids* (`pay_1`, `bank_1`). Get the prefix stripping
    wrong here and every lookup against `gt_map` below silently misses --
    that shows up as a suspiciously large "skipped, no ground truth" count,
    not a crash, so it has to be exact.
    """
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for a in assignments:
        sources = {a["source_a"], a["source_b"]}
        if sources != {"razorpay", "bank"}:
            continue
        if a["source_a"] == "razorpay":
            rzp_orig = a["source_a_id"].replace("rzp_norm_", "")
            bank_orig = a["source_b_id"].replace("bank_norm_", "")
        else:
            rzp_orig = a["source_b_id"].replace("rzp_norm_", "")
            bank_orig = a["source_a_id"].replace("bank_norm_", "")
        rows.append((rzp_orig, bank_orig, a))
    return rows


def compute_calibration(
    assignment_path: Path, ground_truth_path: Path, n_bins: int = 10
) -> dict[str, Any]:
    """Expected Calibration Error, Brier score, and reliability-diagram data.

    Why this exists: the Track 04 brief is explicit that a displayed
    confidence number is a claim, not decoration -- "if your UI displays
    confidence (0.98, 0.73, 0.41), then confidence must actually mean
    something." A confidence score is well-calibrated if, among every
    assignment the system rated (say) 0.90, roughly 90% actually turn out to
    be correct matches. This function is the only place in the codebase that
    checks that, by treating every razorpay<->bank assignment's `confidence`
    field as a predicted probability of correctness and every ground-truth
    row as the binary outcome (1 if the assignment's stated counterpart is
    the true counterpart, 0 otherwise), then measuring the gap between
    stated and actual.

    Two complementary numbers come out:

    - Brier score: mean squared error between confidence and the 0/1
      outcome, `mean((confidence - outcome)**2)`. Lower is better (0 is
      perfect, 1 is the worst possible). It rewards being both
      well-calibrated *and* confidently right -- a model that says 0.5 on
      everything gets a middling Brier score no matter how the coin lands,
      while a model that says 0.99 when right and 0.01 when wrong gets
      close to 0. That dual sensitivity is also its weakness as a
      calibration-only signal: a model that is well-calibrated in aggregate
      but never confident (always says 0.5, and is right 50% of the time)
      scores worse on Brier than a similarly-calibrated-but-more-decisive
      model, even though neither is "wrong" about calibration. Brier alone
      conflates calibration with discrimination (how separable correct and
      incorrect cases are); read it next to ECE, not instead of it.

    - Expected Calibration Error (ECE): assignments are bucketed into
      `n_bins` equal-width confidence bins (default 10, so bin 0 is
      [0.0, 0.1), ..., bin 9 is [0.9, 1.0], with confidence 1.0 landing in
      the top bin rather than spilling into a nonexistent 11th bin). Within
      each bin, compare the bin's mean stated confidence against its actual
      accuracy (fraction of assignments in that bin that were correct).
      ECE is the count-weighted average of `|accuracy - mean_confidence|`
      across bins -- 0 means every bin's confidence matched its accuracy
      exactly; values near 1 mean confidence and accuracy are pointing in
      opposite directions almost everywhere. This is the metric that
      catches the failure mode precision/recall cannot: a system can have
      perfect precision on its AUTO_MATCH set while still being badly
      miscalibrated on the LIKELY_MATCH/EXCEPTION assignments it holds back,
      because precision only looks at the assignments crossing a threshold,
      not at whether the number attached to each one was honest.

    Caveats, stated plainly rather than left implicit:

    - ECE is binning-dependent. A bin can average out to "well calibrated"
      while containing a mix of badly-over- and under-confident cases that
      cancel each other in the mean. More bins reduce that risk but increase
      variance in bins with few samples (a bin with 2 assignments has a
      wildly unstable "accuracy"). `n_bins=10` is a reasonable default, not
      a proof of anything -- read `reliability_bins` alongside the scalar,
      not instead of it, and treat a bin's accuracy with suspicion if its
      `count` is small.
    - This dataset's confidence values cluster heavily near 1.0 (deterministic
      exact-match records dominate the corpus) -- expect most of the mass in
      the top bin and several empty or near-empty low-confidence bins. That
      is a property of this reconciliation problem (most settlements really
      are unambiguous exact matches), not a bug in this function.
    - A razorpay<->bank assignment whose `razorpay_record_id` has no entry
      in ground truth at all is skipped rather than guessed at (mirrors
      `count_correctly_flagged_for_review`'s `expected is None: continue`).
      For real data this should not happen; if `total_scored` is
      unexpectedly smaller than the number of razorpay<->bank rows in
      `assignments.csv`, that gap is worth investigating on its own.
    - This measures confidence *as a probability the counterpart is
      correct*. It says nothing about whether the confidence *formula*
      itself is a good idea, only whether the numbers it outputs are honest
      relative to outcomes actually observed in this ground truth.

    Empty input (no scorable razorpay<->bank assignments) returns all-zero
    metrics and an all-empty-but-present set of bins -- never raises, never
    divides by zero. A `total_scored` of 0 is itself informative (nothing
    was measured), which is different from, and more honest than, a
    fabricated "perfect" score.
    """
    truth = _load_ground_truth(ground_truth_path)
    assignments = _load_assignments(assignment_path)
    gt_map = _build_gt_map(truth)

    confidences: list[float] = []
    outcomes: list[int] = []
    for rzp_orig, bank_orig, row in _iter_rzp_bank_rows(assignments):
        expected = gt_map.get(rzp_orig)
        if expected is None:
            continue
        confidences.append(float(row["confidence"]))
        outcomes.append(1 if bank_orig in expected else 0)

    total_scored = len(confidences)

    # Bucket accumulators. Built for all n_bins up front (not just the ones
    # that end up populated) so `reliability_bins` always has a stable shape
    # for a dashboard to render, whether or not every bin has data.
    bin_count = [0] * n_bins
    bin_confidence_sum = [0.0] * n_bins
    bin_correct_sum = [0] * n_bins

    for conf, outcome in zip(confidences, outcomes):
        idx = int(conf * n_bins)
        idx = min(max(idx, 0), n_bins - 1)
        bin_count[idx] += 1
        bin_confidence_sum[idx] += conf
        bin_correct_sum[idx] += outcome

    reliability_bins: list[dict[str, Any]] = []
    ece = 0.0
    for i in range(n_bins):
        count = bin_count[i]
        mean_confidence = bin_confidence_sum[i] / count if count else 0.0
        accuracy = bin_correct_sum[i] / count if count else 0.0
        reliability_bins.append(
            {
                "bin_lower": round(i / n_bins, 4),
                "bin_upper": round((i + 1) / n_bins, 4),
                "count": count,
                "mean_confidence": round(mean_confidence, 4),
                "accuracy": round(accuracy, 4),
            }
        )
        if total_scored:
            ece += (count / total_scored) * abs(accuracy - mean_confidence)

    brier_score = (
        sum((c - o) ** 2 for c, o in zip(confidences, outcomes)) / total_scored
        if total_scored
        else 0.0
    )

    return {
        "expected_calibration_error": round(ece, 4),
        "brier_score": round(brier_score, 4),
        "reliability_bins": reliability_bins,
        "total_scored": total_scored,
    }


# Exception categories that mean "the money itself does not reconcile".
# A hold on one of these is justified no matter which counterpart was named:
# an unexplained rupee gap is precisely what a human should look at.
_MONEY_DISCREPANCY_CATEGORIES = frozenset(
    {"AMOUNT_MISMATCH", "REFUND_OR_FEE_DEDUCTION", "INVARIANT_VIOLATION"}
)


def compute_abstention_quality(
    assignment_path: Path,
    ground_truth_path: Path,
    exceptions_path: Path | None = None,
) -> dict[str, Any]:
    """Abstention Precision: when the system declined to auto-book, was declining right?

    `LIKELY_MATCH` and `EXCEPTION` are this pipeline's two ways of saying "I
    am not confident enough to auto-book this" -- one still names a
    candidate counterpart held for review, the other flags the pair as
    needing a human's attention. Both are abstentions from the same
    decision: auto-matching. This function asks the safety question the
    Track 04 brief calls "Abstention Precision" -- of the pairs the system
    declined to auto-book, how many of those declines actually prevented a
    wrong entry from hitting the ledger?

    An abstention is **justified** when the candidate counterpart the system
    was looking at was *not* actually the true match -- abstaining protected
    the books from a false auto-book that would otherwise have landed.
    An abstention is **unjustified** when the held candidate *was* actually
    the correct counterpart -- the system was right, but too cautious to say
    so at auto-match confidence, so a genuine match sits in a review queue
    instead of being booked automatically.

    The critical nuance, spelled out because it is easy to read
    "unjustified" as a bug report: it is not one. An unjustified abstention
    costs throughput (a human now has to confirm something the system
    already had right) but it costs nothing in correctness -- the ledger is
    never wrong because of it. A justified abstention is the system doing
    exactly its job. Neither number, by itself, says whether the system's
    confidence threshold is well-tuned; a very conservative threshold will
    rack up unjustified abstentions (safe but slow) while a very aggressive
    one will let false positives past into AUTO_MATCH instead of holding
    them as abstentions at all (fast but unsafe). `abstention_precision`
    (justified / total abstentions) is the summary number for "when we did
    decide not to trust ourselves, how often were we right not to" --
    high is good, but a precision of 1.0 achieved by abstaining on
    everything is not a system anyone would want either. Read this next to
    `abstention_rate` and next to `evaluate()`'s `safe_auto_resolution_rate`
    / `false_auto_book_rate`, not alone.

    Relationship to `evaluate.count_correctly_flagged_for_review`: that
    function already computes something adjacent, scoped to `LIKELY_MATCH`
    only -- it counts how many `LIKELY_MATCH` assignments point at the
    genuinely correct counterpart ("correct") versus the wrong one
    ("incorrect"), framed as "how much of the recall gap is actually just
    correctly-identified, conservatively-held matches." This function is
    not a replacement for that one and does not contradict it -- for the
    `LIKELY_MATCH` subset alone, `count_correctly_flagged_for_review`'s
    `correct` count is exactly this function's `unjustified_abstentions`
    count restricted to `LIKELY_MATCH`, and its `incorrect` count is
    exactly this function's `justified_abstentions` count restricted to
    `LIKELY_MATCH` -- same underlying fact, opposite framing (that function
    asks "was the review-queue candidate right?" from a recall-accounting
    angle; this one asks "was declining to auto-book the right call?" from
    a safety angle, and additionally folds in `EXCEPTION`, which
    `count_correctly_flagged_for_review` does not touch at all). If the two
    ever disagree on the `LIKELY_MATCH`-only numbers, that is a bug in one
    of them, not a legitimate difference in definition.

    Empty input (no razorpay<->bank assignments at all) returns zeros for
    every field rather than dividing by zero.
    """
    truth = _load_ground_truth(ground_truth_path)
    assignments = _load_assignments(assignment_path)
    gt_map = _build_gt_map(truth)

    # Records whose money does not reconcile, per the diagnosis the pipeline
    # already wrote. Measured on a real batch, 107 of 119 holds are exactly
    # this: same-day dates, exact UTR, and an unexplained rupee gap from Rs 5
    # to Rs 16,260. Counting those as "unjustified" because the counterpart
    # was nonetheless the right payment produced a badly misleading number --
    # see the docstring note above.
    unreconciled: set[str] = set()
    if exceptions_path is not None and exceptions_path.exists():
        for entry in json.loads(exceptions_path.read_text(encoding="utf-8")) or []:
            if entry.get("category") in _MONEY_DISCREPANCY_CATEGORIES:
                unreconciled.add(_strip_prefix(entry.get("record_id", "")))

    abstain_labels = {"LIKELY_MATCH", "EXCEPTION"}
    total_rzp_bank = 0
    abstentions = 0
    justified_wrong_counterpart = 0
    justified_unreconciled_amount = 0
    unjustified = 0

    for rzp_orig, bank_orig, row in _iter_rzp_bank_rows(assignments):
        total_rzp_bank += 1
        if row["label"] not in abstain_labels:
            continue
        abstentions += 1
        expected = gt_map.get(rzp_orig)
        if expected is None:
            # No ground truth to judge this abstention against -- doesn't
            # happen for real data, but skip rather than guess (same
            # convention as everywhere else in this module).
            continue
        if bank_orig not in expected:
            justified_wrong_counterpart += 1
        elif rzp_orig in unreconciled:
            justified_unreconciled_amount += 1
        else:
            unjustified += 1

    justified = justified_wrong_counterpart + justified_unreconciled_amount

    return {
        "abstentions": abstentions,
        "justified_abstentions": justified,
        "justified_wrong_counterpart": justified_wrong_counterpart,
        "justified_unreconciled_amount": justified_unreconciled_amount,
        "unjustified_abstentions": unjustified,
        "abstention_precision": round(justified / abstentions, 4) if abstentions else 0.0,
        "abstention_rate": round(abstentions / total_rzp_bank, 4) if total_rzp_bank else 0.0,
    }


def compute_replay_consistency(
    run_a_assignment_path: Path, run_b_assignment_path: Path
) -> dict[str, Any]:
    """Replay Consistency: same evidence, replayed independently, same decision.

    The Track 04 brief demands this as a trust property in its own right,
    separate from precision/recall: "the same decision replayed from the
    same evidence should produce the same decision." A reconciliation
    engine is meant to be a deterministic function of its input evidence --
    not a coin flip that happens to land on the right side most of the
    time. If rerunning the pipeline over byte-identical input CSVs can
    change which bank record a razorpay payment gets matched to, or flip a
    label from `AUTO_MATCH` to `EXCEPTION`, that is a correctness bug no
    matter how good either individual run's precision/recall score looks --
    an accountant cannot trust a ledger that would have looked different if
    reconciliation had merely been run five minutes later. This function is
    the executable proof of determinism the brief is asking for, in place
    of an assertion in a design doc that nobody has actually run.

    A "decision" here is keyed by (source_a, source_a_id, source_b) --
    which foreign-source counterpart, if any, a given entity was paired
    against -- rather than by the full four-column pairing. That
    distinction matters: keying by the full pairing (including
    `source_b_id`) would make a genuinely unstable decision -- the same
    razorpay record matched to a *different* bank record on rerun --
    invisible, because the two runs' rows would simply never share a key at
    all and would look like two unrelated, independently-stable
    observations instead of one record's decision changing. Keying by "which
    entity, against which foreign source" and then comparing the
    `(label, counterpart_id)` pair each run assigned it is what actually
    catches that failure mode.

    A key present in only one run (a row that existed in run A's
    `assignments.csv` but not run B's, or vice versa) counts as a changed
    decision, not as a decision to skip over -- a pairing appearing or
    disappearing between runs over identical input is exactly as much of a
    stability problem as a pairing that persists but flips its label.

    `changed_examples` caps at 10 entries so a badly non-deterministic
    pipeline (this should never happen, but the function must not choke if
    it does) doesn't return an unbounded payload; it is meant as a set of
    concrete examples to debug from, not an exhaustive diff.

    What this does and does not prove: 100% stability across two runs is
    strong evidence of determinism, but it is evidence, not a formal
    guarantee -- it only rules out non-determinism that this particular
    pair of runs happened to exercise (e.g. it would not catch
    non-determinism that depends on wall-clock time, dict-ordering quirks
    that happen to be stable within one Python process, or filesystem
    iteration order, if this codebase's pipeline had any of those, which by
    design it should not). It also says nothing about whether the
    decisions being replayed are *correct* -- a pipeline can be perfectly,
    deterministically wrong. Pair this with `evaluate()` and
    `compute_calibration()`, which are the checks for "correct" and
    "honest"; this one is only the check for "consistent."

    Two empty (header-only) inputs return 100% stability trivially (zero
    decisions, zero disagreements) rather than raising or reporting a
    fabricated 0%.
    """
    run_a = _load_assignments(run_a_assignment_path)
    run_b = _load_assignments(run_b_assignment_path)

    def build_decisions(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], tuple[str, str]]:
        decisions: dict[tuple[str, str, str], tuple[str, str]] = {}
        for row in rows:
            key = (row["source_a"], row["source_a_id"], row["source_b"])
            decisions[key] = (row["label"], row["source_b_id"])
        return decisions

    decisions_a = build_decisions(run_a)
    decisions_b = build_decisions(run_b)

    all_keys = set(decisions_a) | set(decisions_b)
    total_decisions = len(all_keys)
    stable_decisions = 0
    changed_examples: list[dict[str, Any]] = []

    for key in sorted(all_keys):
        decision_a = decisions_a.get(key)
        decision_b = decisions_b.get(key)
        if decision_a == decision_b:
            stable_decisions += 1
            continue
        if len(changed_examples) < 10:
            source_a, source_a_id, source_b = key
            changed_examples.append(
                {
                    "record_id": source_a_id,
                    "source_a": source_a,
                    "source_b": source_b,
                    "run_a": (
                        {"label": decision_a[0], "counterpart": decision_a[1]}
                        if decision_a is not None
                        else None
                    ),
                    "run_b": (
                        {"label": decision_b[0], "counterpart": decision_b[1]}
                        if decision_b is not None
                        else None
                    ),
                }
            )

    changed_decisions = total_decisions - stable_decisions

    return {
        "total_decisions": total_decisions,
        "stable_decisions": stable_decisions,
        "changed_decisions": changed_decisions,
        "stability_rate": round(stable_decisions / total_decisions, 4) if total_decisions else 1.0,
        "changed_examples": changed_examples,
    }
