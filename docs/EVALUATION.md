# SettleGraph — Evaluation

Every number on this page was measured by running the code in this
repository, not estimated. The commands that produce each one are listed in
§7 so a reviewer can re-run them. Where a result is unflattering, it is
reported at the same size as the flattering ones.

**Batch under test:** 1,000 payments / 999 bank rows / 1,000 merchant rows
(2,999 source records → 2,284 assignments), seed 42, anomaly rate 0.15.
Regenerate with `settlegraph generate --total-records 1000 --seed 42`.

---

## 1. Headline

| Metric | Value |
| --- | --- |
| Precision (auto-match correctness) | **100.00%** |
| Recall | 83.64% |
| F1 | 0.9109 |
| False positives | **0** |
| **False Auto-Book Rate** | **0.00%** |
| Safe Auto-Resolution Rate | 82.30% |
| **Dangerous Miss Rate** | **0.00%** (n=16 no-counterpart records) |
| Exception Recall | 100.00% (same n=16) |
| Invariant violations | 0 |
| Replay consistency | **100%** |
| Throughput | ~1,200–1,600 records/sec |

The two that matter most are the pair: **Safe Auto-Resolution Rate 82.30%
alongside False Auto-Book Rate 0.00%.** Reported together they are
Goodhart-resistant — matching aggressively raises the second, abstaining on
everything starves the first.

---

## 2. Baseline comparison — including where we lose

Three baselines, all scored by the identical `evaluate()` harness against the
identical hidden ground truth.

| Approach | Precision | Recall | True positives | **False positives** | False Auto-Book Rate |
| --- | --- | --- | --- | --- | --- |
| A — exact ID match | 100.00% | 84.86% | 835 | 0 | 0.00% |
| B — amount + date window | 100.00% | **87.50%** | **861** | 0 | 0.00% |
| C — fuzzy heuristic | 69.15% | 86.49% | 621 | **277** | **27.70%** |
| **SettleGraph** | 100.00% | 83.64% | 823 | **0** | **0.00%** |

**Read that honestly: on this batch, SettleGraph has the lowest recall of the
three safe approaches.** Baseline B books 861 correct matches to
SettleGraph's 823 — 38 correct matches that SettleGraph holds for review
instead of booking. It did not prevent a single error that B made, because B
made none here.

Two things are true at once, and both belong in the record:

1. **The safety margin cost real throughput on this batch and bought nothing
   measurable on it.** 38 correct matches held, 0 errors prevented versus B.
2. **Baseline C is what the margin exists for.** Fuzzy matching without a
   verification gate corrupted **277 ledger entries** — a 27.70% false
   auto-book rate and a 68.8% dangerous-miss rate. That is the failure mode
   a reconciliation system is actually judged on, and it is why "just match
   more" is not a free win.

Baseline B's 100% precision is a property of *this* batch, not of the
approach: it has no UTR check, no invariant gate, and no abstention, so it
holds only as long as no two payments collide on amount and date. The
adversarial suite constructs exactly that collision, and B fails it
(`tests/test_baseline.py` asserts the false match explicitly). SettleGraph's
margin is insurance against conditions this batch does not contain — an
honest reason to keep it, and not the same thing as a demonstrated win.

---

## 3. The most useful finding: we abstain too much

`engine/calibration.py`, run against the real batch:

| Abstention quality | Value |
| --- | --- |
| Abstentions (LIKELY_MATCH / EXCEPTION on the razorpay↔bank leg) | 122 |
| Justified (declining protected the books) | **2** |
| Unjustified (the held candidate was already correct) | **120** |
| **Abstention precision** | **0.0164** |
| Abstention rate | 12.91% |

**120 of 122 abstentions held a candidate that was already right.** An
unjustified abstention is not a bug — it is the deliberate cost of a safety
margin — but a precision this low means the abstention mechanism is
currently almost pure throughput cost rather than risk mitigation.

The calibration data says exactly why:

| Confidence bin | n | Mean confidence | Actual accuracy |
| --- | --- | --- | --- |
| 0.6 – 0.7 | 3 | 0.617 | 0.333 |
| **0.7 – 0.8** | **101** | **0.749** | **1.000** |
| 0.8 – 0.9 | 6 | 0.850 | 1.000 |
| 0.9 – 1.0 | 835 | 0.998 | 1.000 |

- Expected Calibration Error: **0.0304**
- Brier score: **0.0079**

ECE and Brier both look good, but that is mostly because 88% of the mass sits
at confidence ≈1.0 where the system is correct. The real signal is the
**0.7–0.8 bin: 101 assignments scored at 0.749 were 100% correct.** The
system is materially *under*confident there, and those ~100 records are the
same ones surfacing as unjustified abstentions and as the 38-match recall gap
against Baseline B.

**Root cause (already documented, DEVLOG Day 5):** a 15–20 day settlement
delay zeroes the date-proximity scoring component even when UTR and amount
match exactly, landing a genuinely correct match at ~0.90 — just under the
0.95 auto-match bar.

**Why it is not "fixed" here:** the fix is a threshold or scoring change, and
tuning it on this batch would be tuning on the same corpus used to report
final performance. The `calibration` split exists and is now verified
leak-free (§5), which is where that work belongs. A number tuned on its own
test set would be worth less than reporting this honestly.

---

## 4. Progressive noise — how the system degrades

`scripts/noise_sweep.py --records 500`, six anomaly rates, isolated
workspace, real pipeline at each level.

| Noise | Precision | Recall | Safe Auto | False Book | Auto | Abstained | **Abstention %** |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0% | 100.00% | 91.80% | 91.80% | 0.00% | 1126 | 66 | 5.54% |
| 5% | 100.00% | 89.13% | 88.60% | 0.00% | 1105 | 71 | 6.04% |
| 10% | 100.00% | 86.67% | 85.80% | 0.00% | 1084 | 78 | 6.71% |
| 15% | 100.00% | 83.00% | 82.00% | 0.00% | 1063 | 88 | 7.64% |
| 20% | 100.00% | 80.45% | 79.00% | 0.00% | 1042 | 99 | 8.67% |
| 30% | 100.00% | 73.98% | 72.20% | 0.00% | 1002 | 111 | 9.96% |

**Verdict: HEALTHY.** Precision never dropped below 100% at any tested noise
level. Abstention rose monotonically (5.54% → 9.96%) as data quality fell,
and recall absorbed the cost (91.80% → 73.98%).

That is the correct shape: **the system trades recall for safety, not
precision for recall.** The failure mode the sweep was built to catch — a
system that keeps confidently auto-matching as evidence quality collapses —
does not occur.

**Honest limit:** precision did not break inside the tested range, so this
sweep does not report a true breaking point. It establishes that one does not
exist below 30% heterogeneous corruption at 500 records, which is a weaker
claim than "we found the cliff." Pushing past 30%, or corrupting along axes
the generator does not currently model, is the next place to look.

---

## 4a. The Breaking Point Batch — where it actually stops

`scripts/noise_sweep.py` varies one dial (anomaly rate) on otherwise
well-formed data. `scripts/chaos_batch.py` is the harsher cousin: it pins the
anomaly rate high (0.30) and then inflicts *structural* damage on the written
CSVs — duplicated whole rows, nulled optional fields, mangled
UTR/invoice/reference strings, physically impossible amounts, out-of-order
rows, and malformed timestamps — escalating across chaos levels.

**Result — 400 records, anomaly rate 0.30:**

| Chaos | Status | Precision | Recall | Auto | Abstained | Exceptions | Abstain % | Invariant viol. | Duplicates caught |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0% | OK | **100.00%** | 77.81% | 798 | 81 | 2 | 9.19% | 0 | 0 |
| 10% | OK | **100.00%** | 57.96% | 635 | 117 | 46 | 14.66% | 0 | 119 |
| 20% | OK | **100.00%** | 41.25% | 486 | 153 | 102 | 20.65% | 0 | 238 |
| 30% | OK | **100.00%** | 29.50% | 372 | 164 | 139 | 24.30% | 0 | 357 |
| 40% | OK | **100.00%** | 20.63% | 269 | 197 | 164 | 31.27% | 0 | 475 |
| 50% | OK | **100.00%** | 13.32% | 211 | 209 | 192 | 34.15% | 0 | 592 |

**VERDICT: SAFE, and — after a fix this harness forced — RESILIENT.**

1. **Precision never broke.** 100.00% at every level, 0 invariant violations
   throughout. No false-auto-book cliff exists to report and none was
   manufactured.
2. **Abstention rises monotonically** (9.19% → 34.15%) while recall absorbs
   the damage (77.81% → 13.32%). The system degrades into review, never into
   wrong answers.
3. **Duplicates are caught at scale under adversarial input** — 592 at the
   worst level, none inflating the match counts.

**What this harness found, and what it forced.** On its first run the pipeline
**hard-crashed from 20% damage onward**. `engine/ingest.py` built records with
an all-or-nothing comprehension, so a *single* unparseable timestamp anywhere
in a source file aborted the whole batch — zero row-level fault tolerance. A
merchant with one bad row in a 20,000-row file would have got nothing.

That is now fixed: `load_all_with_quarantine` isolates each failing row into
`results/quarantine.json` with its source file, 1-indexed row number, raw
content and exact validation error, and `summary.json` carries a
`quarantined_records` count. Valid rows process normally.

**Quarantine, deliberately, not skipping.** Silently dropping unparseable rows
would be far worse than crashing: the batch would report clean while money
vanished from the reconciliation entirely. A quarantined batch can never look
identical to a clean one — the count appears in the console, in
`summary.json`, and in its own file.

**The honest limit that remains:** at 50% structural damage recall is 13.32%.
The system is still never *wrong*, but it is close to useless — nearly
everything lands in review. Fail-closed is preserved at the whole-file level
too: a wholly missing source file still raises `FileNotFoundError`, because
"there is nothing to reconcile" is a different failure from "one row is bad".

Reproduce: `python scripts/chaos_batch.py --records 400`.

---

## 5. Dataset split discipline

`_write_splits` produces train (40%) / calibration (15%) / test (30%) /
adversarial (15%) partitions. Until now nothing asserted they were actually
disjoint — a split harness that silently leaks is worse than none, because it
produces a number everyone trusts and nobody checked.

`tests/test_splits.py` now asserts, and all pass:

- **No payment id appears in two splits** (the leakage check)
- Splits partition the whole batch with no records dropped
- Each split's `ground_truth.csv` covers exactly that split's payments — an
  evaluation run cannot be scored against another split's answer key
- Partition is deterministic for a seed, and genuinely different across seeds

---

## 5a. Held-out evaluation — the anti-overfitting check

Splits being disjoint (§5) proves the harness is honest. It does not prove
the *system* generalises. Every number in §1–§4 comes from `data/generated`,
which is the corpus development happened against. So
`scripts/eval_holdout.py` generates a fresh batch with a **different seed**
(`20260905` — the thresholds and scoring were never tuned against it), runs
the real unmodified pipeline against only the held-out `test` split, and
scores all three baselines on that same split with the identical harness.

**Result — 180 held-out records, seed 20260905:**

| Approach | Precision | Recall | TP | FP | False Auto-Book | Dangerous Miss |
| --- | --- | --- | --- | --- | --- | --- |
| **SettleGraph** | **100.00%** | 81.82% | 144 | **0** | **0.00%** | **0.00%** |
| A — exact ID | 100.00% | 82.95% | 146 | 0 | 0.00% | 0.00% |
| B — amount + date | 100.00% | **86.36%** | 152 | 0 | 0.00% | 0.00% |
| C — fuzzy heuristic | 87.01% | 84.81% | 134 | **20** | **11.11%** | 50.00% |

**VERDICT: HEALTHY — no overfitting detected.**

- Precision held at **exactly 100.00%** on data the system was never
  developed against, with **0 false positives and 0 invariant violations**.
- Recall moved only **−1.82pp** (83.64% dev → 81.82% held-out), well inside
  the 5pp band the script treats as material.
- ECE 0.0308 / Brier 0.0073 held-out, essentially identical to the
  development figures (0.0304 / 0.0079).
- Baseline C reproduced its danger profile on unseen data too — 20 false
  positives, 11.11% false-auto-book rate, 50% dangerous-miss rate. The
  argument for a verification gate is not an artefact of one batch.
- Baseline B again finished as the highest-recall *safe* approach, exactly as
  on the development batch. Consistent, and still reported.

**The one number that did not improve:** abstention precision was **0.0000**
held-out (0 of 23 abstentions justified) versus 0.0164 in development. Both
are tiny samples and both say the same thing §3 already says — the abstention
margin is currently throughput cost rather than measured risk mitigation on
this generator. It is consistent with the known weakness, not a new one, and
it did not trip the overfitting verdict.

Reproduce: `python scripts/eval_holdout.py --records 600 --seed 20260905`.

---

## 6. Invariants — enforced, not documented

The rule applied: *no invariant may exist only in documentation.* Each is
executable code plus tests in three shapes — obvious violation, subtle
boundary violation, and a violation that would otherwise receive a high match
score.

| Invariant | Enforced in | Boundary tested | High-score case tested |
| --- | --- | --- | --- |
| Net = amount − fee − tax | `models/razorpay.py::net_must_balance` | — | — |
| Amount reconciles (±₹1.00) | `verify.py::verify_amount_invariant` | 100 paise passes / 101 fails | UTR + date perfect, amount wrong → still rejected |
| Date proximity | `verify.py::verify_date_invariant` | exactly 3 days passes / 4 fails; symmetric for backdated rows | — |
| Credit direction | `verify.py::verify_direction_invariant` | undeclared passes (documented default) | **identical UTR + amount + date scores ≥ 0.95 and is still rejected** |

That last row is the important one. A debit line (a refund payout, say)
sharing a UTR, amount and date with a settlement scores at the top of the
range — `score_edge` never inspects ledger direction. `test_verify.py`
asserts the score really does clear the auto-match threshold *and* that the
invariant still rejects it. `pipeline.enforce_invariant_gate` then demotes it
to `EXCEPTION` with the violation named.

Duplicate interception (`IdempotencyShield`) runs on the real ingest path and
writes `duplicates.json`; `summary.json` carries `duplicates_intercepted`.

---

## 6a. Adversarial corpus — two real defects it found

`datagen/adversarial.py` builds eight scenarios whose purpose is to make the
engine produce a **confident but wrong** answer, and `tests/test_adversarial.py`
runs each through the real `build_candidate_graph → score_all → global_assign`
path. Two of the eight found genuine defects. Both are now fixed; the tests
that documented them now assert the safe behaviour instead.

| Scenario | Outcome |
| --- | --- |
| Twin candidates, no reference | Safe — both score 0.35/0.32, far below threshold |
| **Near-tie scores** | **Was a defect → fixed** |
| Conflicting sources, no fee evidence | Safe — 0.60, EXCEPTION; no fee invented |
| **Reused UTR collision** | **Was a serious defect → fixed** |
| Amount tolerance boundary (99/100/101p) | Safe, deliberate: `<=` is inclusive |
| Date tolerance boundary (2/3/4 days) | Safe, deliberate |
| Unicode / case / homoglyph UTR drift | Safe — strict `==` creates no false candidate |
| Prompt injection in `description` | Safe — confirmed identical label to a clean twin |

**Defect 1 — reused UTR silently erased the true counterpart.**
`build_candidate_graph` indexed Razorpay records into a plain
`dict[utr] -> record`. When two payments shared a UTR — a recycled bank
reference, which the generator already injects as `duplicate_utr_reuse` and
real banks genuinely produce — the second **overwrote** the first before
scoring ran. The true counterpart never became a candidate, and the bank
credit was confidently `AUTO_MATCH`ed at 0.95 (UTR + exact amount + same-day
date) to the *wrong* payment, with nothing in the output signalling the
collision. That is a confident, invariant-passing, factually wrong ledger
entry — the worst output this system can produce, and it was reachable from
the generator's own anomaly set.

**Defect 2 — near-ties were resolved by a coin flip.** Two candidates within
floating-point noise of each other both cleared 0.95; `global_assign` booked
whichever rounding favoured and the runner-up vanished from the output
entirely. Nothing anywhere lowered confidence in a winner because a
near-identical competitor existed.

**Root cause, shared:** this project's rule for automation has always been
three-part — *evidence above threshold, **AND no competing explanation**, AND
invariants hold*. The first and third were enforced in code. The middle
clause was in the README, the ADRs and the architecture doc, and implemented
nowhere.

**Fix:** `match.py` keeps every colliding record so the competition survives
into scoring, and `global_assign` now suppresses an `AUTO_MATCH` when a
distinct alternative counterpart on the same leg scores within
`PipelineConfig.ambiguity_margin` (0.05, per-merchant configurable). The
demoted decision carries `competing_candidates` and a plain-language
`abstention_reason` into `assignments.csv`.

**Cost on real data: zero.** Re-running the 1,000-record batch after the fix
gives byte-identical metrics — 823 true positives, 0 false positives,
precision 100%, recall 83.64% — and **0 suppressions fired**, because this
batch contains no genuine near-ties at that margin. The gate is targeted
rather than blunt: it changes nothing on clean data and closes the hole on
adversarial data.

One honest caveat about how that was found: the first version of the
suppression counted *edges* rather than distinct counterparts, and
`build_candidate_graph` legitimately proposes the same razorpay↔merchant pair
twice (once on `order_id`, once on `payment_id`). That made every such pair
look contested and wrongly suppressed 990 correct auto-matches. It was caught
by measuring the real batch after the change, not by any test — which is the
argument for always running the batch, not just the suite.

---

## 7. Reproducing everything here

```bash
# headline batch + metrics
python -m settlegraph.cli generate --total-records 1000 --seed 42
python -m settlegraph.cli run
cat results/evaluation.json

# baseline comparison
python -m settlegraph.cli benchmark

# progressive noise sweep
python scripts/noise_sweep.py --records 500 --json noise.json

# breaking point batch (structural damage, not just anomaly rate)
python scripts/chaos_batch.py --records 400 --json chaos.json

# held-out evaluation on a split development never saw
python scripts/eval_holdout.py --records 600 --seed 20260905 --json holdout.json

# replay consistency: re-derive every decision and require it to match
python -m settlegraph.cli replay

# adversarial corpus + invariant boundary + split leakage
python -m pytest tests/test_adversarial.py tests/test_verify.py tests/test_splits.py -v

# calibration, abstention quality, replay consistency
python -c "from settlegraph.engine.calibration import compute_calibration; from pathlib import Path; \
print(compute_calibration(Path('results/assignments.csv'), Path('data/generated/ground_truth.csv')))"

# scale
python scripts/stress_test.py --records 20000

# failure containment
python -m settlegraph.cli simulate

# full gate (tests + e2e)
python -m pytest -q && python scripts/run_e2e.py
```

---

## 8. What this evaluation still cannot tell you

- The corpus is synthetic, generated by this repository's own
  `datagen/generator.py`. Split discipline reduces but cannot eliminate the
  risk that good scores mean "generalises to what this generator produces"
  rather than to a real merchant's feed.
- `dangerous_miss_rate = 0.00%` and `exception_recall = 100%` rest on n=16
  no-counterpart records in this batch. Real, measured, and a small sample.
- The noise sweep found no precision breaking point below 30% corruption,
  which is a bounded negative result, not a located cliff.
- Abstention precision (0.0164) is a measured weakness that has **not** been
  fixed, deliberately — see §3.
- The LLM reasoning layer is off by default; every number on this page comes
  from the deterministic path. Nothing here is a claim about model quality.
- **Out-of-distribution coverage is real but partial.** Three things do
  genuinely test it: the held-out run uses a seed the system was never tuned
  against (§5a), the noise sweep shifts the anomaly distribution across six
  levels (§4), and the adversarial corpus covers unexpected *identifier*
  formats — case drift, leading zeros, Cyrillic homoglyphs (§6a). What is
  **not** covered: genuinely new transaction categories, unusual fee
  structures, and settlement-timing regimes the generator does not model at
  all. Those would need generator work, not just new assertions, and the
  claim "confidence decreases on unfamiliar patterns" is therefore only
  demonstrated for the identifier and noise axes, not universally.
- **Malformed input is now survivable, but a whole-file failure still is
  not — deliberately.** Probed directly: empty-but-well-formed CSVs complete
  cleanly; individual unparseable rows are quarantined (§4a) with their row
  number and validation error; a *wholly missing* source file still raises
  `FileNotFoundError` and always will, because "there is nothing to
  reconcile" is a different failure from "one row is bad". Nothing is
  silently coerced. The remaining rough edge is that the missing-file case
  surfaces as a raw traceback rather than an actionable operator message.
  Not wrapped in a broad `except` on purpose: swallowing ingest errors to
  look tidy is exactly how a half-ingested batch gets reported as clean.
