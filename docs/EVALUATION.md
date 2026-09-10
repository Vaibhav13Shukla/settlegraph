# SettleGraph — Evaluation

Every number on this page was measured by running the code in this
repository, not estimated. The commands that produce each one are listed in
§7 so a reviewer can re-run them. Where a result is unflattering, it is
reported at the same size as the flattering ones.

**Batch under test:** 1,000 payments / 1,003 bank rows / 1,000 merchant rows
(3,003 source records → 2,308 assignments) across six merchants, seed 42,
anomaly rate 0.15, independent opaque identifiers (ADR 0011).
Regenerate with `settlegraph generate --total-records 1000 --seed 42`.

---

## 1. Headline

| Metric | Value |
| --- | --- |
| Precision (auto-match correctness) | **100.00%** |
| Recall | 84.49% |
| F1 | 0.9159 |
| False positives | **0** |
| **False Auto-Book Rate** | **0.00%** |
| Safe Auto-Resolution Rate | 83.90% |
| **Dangerous Miss Rate** | **0.00%** (n=7 no-counterpart records) |
| Exception Recall | 100.00% (same n=7) |
| Invariant violations | 0 |
| Replay consistency | **100%** (2,308/2,308) |
| Throughput | ~1,000–1,600 records/sec |

The two that matter most are the pair: **Safe Auto-Resolution Rate 83.90%
alongside False Auto-Book Rate 0.00%.** Reported together they are
Goodhart-resistant — matching aggressively raises the second, abstaining on
everything starves the first.

---

## 1a. Amount-weighted exposure — rupees, not counts

Precision treats a wrong ₹10,00,000 settlement and a wrong ₹100 one as one
false positive each. A reconciliation analyst does not: the cost is asymmetric
in *rupees*, not in *rows*. `evaluate()` therefore also reports the auto-booked
decisions weighted by settlement amount (seed 42, 1,000 records):

| Amount-weighted metric | Value |
| --- | ---: |
| Rupees auto-booked unattended | **₹28,97,124.59** |
| …of which booked onto the **wrong** counterpart | **₹0.00** |
| Worst-case single unattended auto-book (blast radius) | **₹41,088.48** |
| Amount-weighted precision | 1.0 |

The claim worth making is the second row, not "precision 1.0": **zero rupees
were booked unattended onto a wrong counterpart.** The worst-case row is the
blast radius of trusting any *one* auto-match without review — the largest
single decision the system made on its own was ₹41,088.48.

**Offline vs live — two different questions, deliberately kept apart.** This
table is the *offline* view: it needs hidden ground truth to ask whether the
booked rupees were *correct*, so it exists only in evaluation. The *live* view
an analyst sees in production is `revenue_assurance` (`engine/report.py`),
which sums rupees reconciled / held for review / unexplained **without** ground
truth. The two are not merged: one answers "how much money is where" (live),
the other "was the money we moved moved correctly" (offline). Amount-weighted
recall (rupees of true matches *missed*) is intentionally **not** reported — a
missed payment's authoritative amount lives on the payment record, which
`ground_truth.csv` does not carry; the honest "rupees not yet booked" figure is
revenue assurance's pending + unexplained totals, not a number reconstructed
here from a partial source.

---

## 2. Baseline comparison — including where we lose

Three baselines, all scored by the identical `evaluate()` harness against the
identical hidden ground truth.

| Approach | Precision | Recall | True positives | **False positives** | False Auto-Book Rate |
| --- | --- | --- | --- | --- | --- |
| A — exact ID match | 100.00% | 86.00% | 854 | 0 | 0.00% |
| B — amount + date window | 100.00% | 87.21% | 866 | 0 | 0.00% |
| C — fuzzy heuristic | 100.00% | **87.81%** | **872** | 0 | 0.00% |
| **SettleGraph** | 100.00% | 84.49% | 839 | **0** | **0.00%** |

**Read that honestly: on this batch, SettleGraph has the *lowest* recall of
the four approaches, and every naive baseline is exactly as safe as it is —
100% precision, zero false auto-books.** Each baseline books more correct
matches than SettleGraph (854 / 866 / 872 vs 839) because none of them abstain.
On this batch the safety margin bought nothing measurable and cost throughput.

### Correction: a retracted claim

An earlier version of this page reported Baseline C (fuzzy) at **69.15%
precision and a 27.70% false-auto-book rate (277 corrupted ledger entries)**,
and used it as the headline justification for the verification gate. **That
number is retracted.** It was an artifact of the old identifier scheme, not a
property of fuzzy matching. The previous UTRs were sequential
(`RZP{index:012d}`), so unrelated references shared almost all their characters
and `difflib.SequenceMatcher` scored them as ~0.94 similar — the fuzzy matcher
was bridging *coincidentally-similar strings*. With independent 16-character
random UTRs (ADR 0011), different UTRs no longer resemble each other, and the
fuzzy baseline's false-auto-book rate on this batch is **0.00%**.

The honest consequence: **an easy, clean-ish batch no longer differentiates a
naive matcher from SettleGraph.** That is expected — when identifiers are
unique and mostly present, exact/amount/fuzzy matching all succeed. The value
of the architecture is not visible on this batch; it is visible where the
failure modes actually live:

1. **Ambiguity.** When two candidates are within the tie-break margin,
   SettleGraph abstains (LIKELY_MATCH) rather than booking a coin-flip; the
   baselines book first-come-first-served. See §3.
2. **Cross-merchant collisions.** SettleGraph refuses to link records across
   merchants even on an identical UTR (ADR 0010,
   `tests/test_match.py::test_candidate_graph_never_links_across_merchants`);
   the baselines have no concept of a merchant.
3. **Semantic / direction / record-type confusion and recycled references.**
   The invariant gate and the per-scenario adversarial suite
   (`datagen/adversarial.py`) exercise these; a naive matcher mis-books and the
   gate does not.

SettleGraph's margin is insurance against conditions this batch does not
contain — an honest reason to keep it, and explicitly *not* a demonstrated win
on the standard batch.

---

## 3. The abstention story — and the metric that got it wrong

> **Correction.** An earlier version of this section reported
> `abstention_precision = 0.0164` and concluded the review queue was "~98%
> noise". **That number was measuring the wrong thing.** The corrected figure
> is **0.9016** and the conclusion is close to the opposite. The original is
> left described below rather than deleted, because how a metric misled its
> own authors is more useful to a reviewer than a tidy number.

**What the old definition did.** It counted a hold as *unjustified* whenever
the held candidate turned out to be the correct counterpart — reasoning that
the system "was right but too cautious". That is wrong whenever the **money
does not reconcile**. Holding the correct payment because ₹16,260 of it is
unexplained is not over-caution; it is the single most valuable thing this
system does.

**What the batch actually contains** (measured directly, not inferred):

| | Count |
| --- | --- |
| Abstentions on the razorpay↔bank leg | 122 |
| Justified — held candidate was the **wrong** counterpart | 2 |
| Justified — **money does not reconcile** (unexplained gap) | **108** |
| Genuinely unnecessary — right counterpart, money fine | **12** |
| **Abstention precision** | **0.9016** |

Of 119 held razorpay↔bank pairs, **107 have a same-day date and an exact UTR
but an unexplained rupee gap** ranging from ₹5 to ₹16,260. The queue is ~90%
legitimate finance work, not noise.

**The date-collapse story is real but small.** The remaining **12** holds are
the ones with exact amounts and settlement delays of 12–20 days, where
`score_edge`'s date-proximity component drops to zero (DEVLOG Day 5). That is
10% of the queue, not the bulk — an earlier draft of this document blamed it
for the whole thing.

**Calibration is genuinely good, once read correctly:**

| Confidence bin | n | Mean confidence | Actual accuracy |
| --- | --- | --- | --- |
| 0.6 – 0.7 | 3 | 0.617 | 0.333 |
| 0.7 – 0.8 | 101 | 0.749 | 1.000 |
| 0.8 – 0.9 | 6 | 0.850 | 1.000 |
| 0.9 – 1.0 | 835 | 0.998 | 1.000 |

- Expected Calibration Error: **0.0304** · Brier score: **0.0079**

The 0.7–0.8 bin looks like under-confidence — 101 records scored 0.749 were
100% "correct" — but that bin is measuring *did we name the right
counterpart*, and those are the same records whose amounts do not reconcile.
Naming the right payment and refusing to book it are both correct there. The
apparent miscalibration is largely an artefact of the same conflation the
abstention metric made.

**Threshold study confirms it is not a threshold problem.**
`scripts/threshold_study.py` sweeps `auto_match_threshold` 0.80–0.97 on the
**calibration split only**: precision holds at 100% with zero false positives
at every value, and recall is *identical* (80.46%) throughout. Lowering the
threshold would gain **+0.00pp recall**. These holds are not sitting just
under the bar — the money genuinely does not add up, and no threshold should
release them. Recommendation: **keep 0.95**.

### The original section, as published



`engine/calibration.py`, run against the real batch:

| Abstention quality | Value |
| --- | --- |
| Abstentions (LIKELY_MATCH / EXCEPTION on the razorpay↔bank leg) | 122 |
| Justified (declining protected the books) | **2** |
| Unjustified (the held candidate was already correct) | **120** |
| **Abstention precision** | **0.0164** ← *superseded, see above* |
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

**And the study was run — the answer is "keep 0.95".** `scripts/threshold_study.py`
sweeps `auto_match_threshold` across 0.80–0.97 on the **calibration split
only**. Result: precision holds at 100% with zero false positives at *every*
swept value, and recall is **identical (80.46%) from 0.80 through 0.95**.
Dropping the threshold would gain **+0.00pp recall and +0.0000 abstention
precision** while shedding safety margin.

That is a real finding about the root cause: **the abstention problem is not
a threshold problem.** Lowering the bar does not release those 120 unjustified
holds, because they are not sitting just under 0.95 — the date-proximity
component collapses to zero on delayed settlements, so they land well below
any threshold worth setting. The fix belongs in `score_edge`'s date handling,
not in `config.auto_match_threshold`.

Worth recording how that conclusion was reached: the study's first version
recommended dropping 0.95 → 0.80, because it picked "the lowest threshold
that keeps precision at 100%" without requiring the change to *buy* anything.
Its own docstring already said a gainless-but-safe threshold is not a reason
to touch a working default; the code did not enforce it. Now it does, with a
0.5pp materiality bar.

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

| Chaos | Status | Precision | Recall | Auto | Abstained | Exceptions | Abstain % | Invariant viol. | Quarantined | Duplicates caught |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0% | OK | **100.00%** | 75.00% | 818 | 81 | 5 | 8.96% | 0 | 0 | 0 |
| 10% | OK | **100.00%** | 52.78% | 630 | 123 | 45 | 15.41% | 0 | 0 | 121 |
| 20% | OK | **100.00%** | 39.39% | 493 | 127 | 77 | 18.22% | 0 | 4 | 241 |
| 30% | OK | **100.00%** | 27.02% | 382 | 149 | 100 | 23.61% | 0 | 4 | 363 |
| 40% | OK | **100.00%** | 20.20% | 292 | 150 | 141 | 25.73% | 0 | 11 | 480 |
| 50% | OK | **100.00%** | 10.61% | 200 | 168 | 170 | 31.23% | 0 | 8 | 604 |

The `Quarantined` column is direct evidence that the fix described below
actually fires: from 20% damage onward — the exact level that used to crash this
sweep — rows are isolated and the batch still completes. Re-measured 2026-09-10
on the current code (independent-identifier, multi-merchant data); every figure
above reproduced exactly.

**VERDICT: SAFE, and — after a fix this harness forced — RESILIENT.**

1. **Precision never broke.** 100.00% at every level, 0 invariant violations
   throughout. No false-auto-book cliff exists to report and none was
   manufactured.
2. **Abstention rises monotonically** (8.96% → 31.23%) while recall absorbs
   the damage (75.00% → 10.61%). The system degrades into review, never into
   wrong answers.
3. **Duplicates are caught at scale under adversarial input** — 604 at the
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
`summary.json`, in its own file, and in the dashboard's **Data integrity**
panel, where a non-zero count renders in red and an *absent* count renders `--`
rather than `0`, so "we don't know" is never displayed as "nothing was
quarantined".

**The honest limit that remains:** at 50% structural damage recall is 10.61%.
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

**Result — 600 held-out records (the test split of a fresh 2,000-record
generation), seed 20260905:**

| Approach | Precision | Recall | TP | FP | False Auto-Book | Dangerous Miss |
| --- | --- | --- | --- | --- | --- | --- |
| **SettleGraph** | **100.00%** | 82.60% | 489 | **0** | **0.00%** | **0.00%** |
| A — exact ID | 100.00% | 83.61% | 495 | 0 | 0.00% | 0.00% |
| B — amount + date | 100.00% | **87.16%** | 516 | 0 | 0.00% | 0.00% |
| C — fuzzy heuristic | 100.00% | 86.15% | 510 | 0 | 0.00% | 0.00% |

**VERDICT: HEALTHY — no overfitting detected.**

- Precision held at **exactly 100.00%** on data the system was never
  developed against, with **0 false positives and 0 invariant violations**.
- Recall moved only **−1.89pp** (84.49% dev → 82.60% held-out), well inside
  the 5pp band the script treats as material.
- ECE 0.0315 / Brier 0.0075 held-out, essentially identical to the
  development figures.
- **Same pattern as the development batch, and it is the honest one:** every
  naive baseline is exactly as safe as SettleGraph (100% precision, 0 false
  auto-books) and each posts marginally higher recall by never abstaining.
  Baseline C's danger does **not** reproduce here either — with independent
  identifiers fuzzy matching is not the hazard it appeared to be under the old
  sequential UTRs (see §2 correction). The verification gate's value is
  demonstrated on the per-scenario adversarial suite, not on this split.
- The held-out split was sized at 600 records (a 2,000-record generation)
  rather than 180: at n≈180 a single split's recall carries ~±5pp of binomial
  noise, enough to swamp the signal the check exists to detect. A larger
  held-out split makes the "did recall really move?" question answerable.

**One caveat on the held-out abstention figure:** it was computed with the
*old* metric definition (0.0000 held-out vs 0.0164 development), before §3's
correction. Both numbers understate the same way — they count any hold on a
correct counterpart as unjustified regardless of whether the money
reconciled. The held-out run has not been re-scored under the corrected
definition; treat that one cell as superseded rather than as a result. It is
consistent with the development figure either way, and
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

## 6a. Adversarial corpus — three real defects it found

`datagen/adversarial.py` builds twelve scenarios whose purpose is to make the
engine produce a **confident but wrong** answer, and `tests/test_adversarial.py`
runs each through the real `build_candidate_graph → score_all → global_assign`
path. Three of the twelve found genuine defects. All three are now fixed; the
tests that documented them now assert the safe behaviour instead.

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
| Unusual fee structure (out-of-band MDR) | Safe — gap is not absorbed as "probably fees" |
| Unusual settlement timing (T+30) | Safe — date proximity contributes nothing, held |
| **New transaction category (`adjustment`)** | **Was a defect → fixed** |
| Unexpected identifier format | Safe, at a recall cost — abstains rather than mismatching |

**Defect 3 — a non-payment record was booked as a settlement.**
`normalize_razorpay` hardcoded `record_type="payment"` for every Razorpay
row, discarding `entity_type` (payment / refund / transfer / adjustment)
before anything downstream could act on it — and `score_edge` never inspects
`record_type` anyway. An `adjustment` with a matching UTR, exact amount and
same-day date therefore scored **0.95**, byte-identical to the ordinary
payment path, and was confidently `AUTO_MATCH`ed: a settlement booked
against a record that is not a settlement.

It was invisible to the entire suite because `datagen/generator.py` only ever
emits `entity_type="payment"`, so **no generated batch could reach it** —
reachable on real merchant data and not on ours, the worst combination.
Fixed by preserving the category through normalization and adding
`verify_record_type_invariant`. The score is *still* 0.95; the invariant gate
demotes it to `EXCEPTION`. It is the clearest demonstration in the suite that
scoring is not the gate.

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
gives byte-identical metrics — 839 true positives, 0 false positives,
precision 100%, recall 84.49% — and **0 suppressions fired**, because this
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
- `dangerous_miss_rate = 0.00%` and `exception_recall = 100%` rest on n=7
  no-counterpart records in this batch. Real, measured, and a small sample.
- The noise sweep found no precision breaking point below 30% corruption,
  which is a bounded negative result, not a located cliff.
- Abstention precision is **0.9016** once holds on unreconciled money are
  counted as justified (§3). The residual **12** avoidable holds are a real
  but small weakness, and the fix belongs in `score_edge`'s date handling
  rather than in a threshold — not done.
- The held-out abstention figure has not been re-scored under the corrected
  definition (§5a).
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
