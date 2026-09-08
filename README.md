# SettleGraph

**Find every rupee. Prove every match.**

> **Reviewing this cold?** Start with **[`docs/DEMO.md`](docs/DEMO.md)** — a
> 5-minute walkthrough with the exact commands. Then
> **[`docs/EVALUATION.md`](docs/EVALUATION.md)** for every number and how it
> was measured, **[`docs/RED_TEAM.md`](docs/RED_TEAM.md)** for what a hostile
> reviewer found, and **§8 below** for what this system *cannot* safely do.

> An evidence-first settlement reconciliation and revenue-assurance controller for Razorpay merchants. It reconciles Razorpay settlement records, bank-statement records, and merchant-ledger records; every accepted match must be verifiable, and unresolved money stays visible as an exception.

---

## 1. Architectural Philosophy

> *"The model may propose. The ledger must verify. The system must explain."*

In high-throughput financial infrastructure, unconstrained AI/LLMs cannot be trusted with autonomous ledger mutation. Contemporary research (e.g. *FinBalance*, arXiv:2606.15949) proves that state-of-the-art LLMs achieve at most 46% exact balance-sheet accuracy on multi-document reconciliation.

SettleGraph implements a **Shielded Constrained Architecture** (Altman CMDP, 1999; Alshiekh et al., AAAI 2018):
1. **Probabilistic Linkage Layer:** Fellegi-Sunter (1969) weighted scoring proposes candidate links based on field discriminating power.
2. **Global Assignment Optimizer:** Bipartite assignment under hard leg-exclusivity constraints prevents conflicting matches.
3. **Deterministic Safety Shield:** Mathematical invariant verification (double-entry arithmetic, fee invariants, date tolerance) guarantees **zero corrupted ledger entries**.
4. **Exception Diagnosis Engine:** Unmatched or ambiguous records are not dropped; they are classified into actionable root-cause categories with suggested human remediation.

```
┌─────────────────────────────────────────────────────────────┐
│                       INPUT SOURCES                         │
│  Razorpay Settlements  │  Bank Statements  │  Merchant ERP  │
└──────────────────────────────┬──────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────┐
│                    NORMALIZATION LAYER                      │
│      Standardized integer paise, UTR, Order ID, Dates       │
└──────────────────────────────┬──────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────┐
│               FELLEGI-SUNTER PROBABILISTIC SCORING          │
│   UTR (0.60) + Net Amount (0.25) + Date (0.10) + Desc (0.05)│
└──────────────────────────────┬──────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────┐
│                 GLOBAL ASSIGNMENT OPTIMIZER                 │
│      Exclusivity constraints per reconciliation leg         │
└──────────────────────────────┬──────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────┐
│                DETERMINISTIC INVARIANT SHIELD               │
│     Net amount verified │ Value date verified │ Credits > 0  │
└──────────────┬───────────────────────────────┬──────────────┘
               │ Passed                        │ Failed / Ambiguous
               ↓                               ↓
┌──────────────────────────────┐ ┌────────────────────────────┐
│      AUTO_MATCH (100% P)     │ │    EXCEPTION INVESTIGATION │
│    Automated Ledger Booking  │ │ Root-cause diagnosis & ₹ exp│
└──────────────┬───────────────┘ └─────────────┬──────────────┘
               └───────────────┬───────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────┐
│             AUDIT & REVENUE ASSURANCE REPORT                │
│    Reconciliation throughput, unexplained exposure, logs    │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Design Laws

1. **Never produce an incorrect ledger entry.** (Precision = 100.0% is a non-negotiable hard constraint).
2. **Never waste human attention unnecessarily.** (Automate the 80%+ clean flow, focus human review on true anomalies).
3. **Always quantify unexplained exposure.** (Unresolved money must be surfaced as an actionable revenue-assurance metric).

---

## 3. Evaluation Benchmark (Hidden Ground Truth)

Evaluated on a seeded, out-of-sample batch of **1,000 transaction realities** with 15% injected anomalies across **12 kinds** (UTR corruption, missing references, MDR fee variation, partial/full refunds, bank timing delays, split settlements, duplicate reference reuse, out-of-order arrival, unit-confusion-shaped amount errors, malformed descriptions, currency drift, and settlements that genuinely never reached the bank — see `DEVLOG.md` Days 4 and 7):

Measured against **three** baselines, not one — a single exact-match strawman
would flatter the result. Full numbers, method and caveats:
**[`docs/EVALUATION.md`](docs/EVALUATION.md)**.

| Metric | A: Exact ID | B: Amount + Date | C: Fuzzy Heuristic | **SettleGraph** |
| :--- | :--- | :--- | :--- | :--- |
| **Precision** | 100.0% | 100.0% | 69.2% | **100.0%** |
| **Recall** | 84.9% | **87.5%** | 86.5% | 83.6% |
| **F1** | 0.9181 | 0.9333 | 0.7686 | 0.9109 |
| **True Positives** | 835 | **861** | 621 | 823 |
| **False Positives** | 0 | 0 | **277** | **0** |
| **False Auto-Book Rate** | 0.00% | 0.00% | **27.70%** | **0.00%** |
| **Dangerous Miss Rate** | 0.0% | 0.0% | 68.8% | **0.0%** |
| **Exception Diagnosis** | None | None | None | **Root-cause classification + ₹ exposure** |

**Read that honestly: Baseline B beats SettleGraph on recall on this batch**
(861 correct matches to our 823, at the same 100% precision). The safety
margin cost 38 correct matches and prevented zero errors *here*. What
justifies it is column C: fuzzy matching without a verification gate
corrupted **277 ledger entries**. And Baseline B's clean precision is a
property of *this* batch, not of the approach — it has no UTR check, no
invariant gate and no abstention, and the adversarial suite constructs the
amount+date collision where it fails (`tests/test_baseline.py` asserts it).

Reproduce with `settlegraph benchmark`.

Stress-tested at **20,000 records (~60,000 total across all three sources)** with `scripts/stress_test.py`: zero false positives, zero invariant violations held at scale, ~1,600 records/sec after fixing two real bottlenecks the stress run surfaced (see DEVLOG Day 4 — a quadratic candidate-matching loop and an over-eager drift detector, both measured and fixed, 12.8× faster on the worse of the two).

Beyond the single batch, four harnesses exist because one number proves nothing:

| Harness | Question it answers | Result |
| :--- | :--- | :--- |
| `scripts/noise_sweep.py` | Does the system stay confident as data quality collapses? | **Healthy** — precision held 100% from 0→30% corruption while abstention rose 5.1%→9.2% and recall absorbed the cost |
| `scripts/chaos_batch.py` | Where is the actual breaking point under mixed structural damage? | **Safe and resilient** — precision held 100% at every level 0→50%; bad rows are quarantined, not fatal (this harness found the crash that forced that fix) |
| `scripts/eval_holdout.py` | Does it hold up on a split development never saw? | **No overfitting** — precision held at exactly 100% on a different seed, recall −1.82pp |
| `settlegraph replay` | Would you get the same decision twice? | **2,284/2,284 identical, 100% stability** |

### Anomaly Breakdown — the honest version, including what doesn't work yet
- **Normal transactions:** 782/782, zero false positives.
- **Recovers well without AI assistance** (UTR corruption, missing UTR, fee mismatch, refund deductions, currency mismatch, malformed descriptions, missing merchant record): each routes cleanly to its exception category with **zero false positives**, and most clear a majority of cases automatically.
- **Currently 0% automated recall** — the honest gap the PDF asks for, not a rounding error: `split_settlement`, `extreme_amount_mismatch`, and `out_of_order_arrival` each corrupt the UTR-driven candidate graph itself, so no candidate edge ever reaches the deterministic scorer. This is the exact target set for the Claude-backed reasoning layer in `engine/ai_reasoner.py` (§7) — every promotion it makes still has to clear the same invariant gate `AUTO_MATCH` does, which is why it gets its own `AI_RESOLVED_MATCH` label rather than inflating the deterministic number above.
- **`duplicate_utr_reuse` was in that list until Day 8, for a worse reason than "unrecoverable."** The adversarial corpus (`datagen/adversarial.py`) found that a reused UTR made `build_candidate_graph` *silently overwrite* one of the two colliding payments, after which the bank credit was confidently `AUTO_MATCH`ed to the **wrong** payment at 0.95. Not a recall gap — a dangerous false match, reachable from an anomaly this repo's own generator already injected, with 130+ tests and a 20,000-record stress run green the whole time. Both colliding records now survive into scoring, and a win by less than `config.ambiguity_margin` is held rather than booked. See `DEVLOG.md` Day 8.

---

## 4. Goodhart's Law Resistance

> *"When a measure becomes a target, it ceases to be a good measure."* (Goodhart, 1975)

If an automated reconciliation engine optimizes solely for **Match Rate %**, it can easily game the metric by relaxing amount and UTR thresholds, forcing false links on ambiguous records.

SettleGraph resists Goodharting by being **Precision-Constrained**:
- It maximizes throughput *subject to* a strict zero-tolerance invariant verification gate.
- Exceptions are treated as a **first-class engineering feature**, not a failure. As the saying goes: *"Good automation knows when it doesn't know."*

---

## 5. AI-Assisted Exception Resolution (Optional, Off by Default)

The three zero-recall anomaly kinds above (§3) share one property: the deterministic
candidate graph produces nothing for the scorer to accept or reject. `engine/ai_reasoner.py`
lets a Claude call attempt those specifically — and only those — under the same rule this
project has followed since Day 1:

> A model proposes. Deterministic code decides.

Concretely: the model returns a hypothesis (which candidate, if any, is the true
counterpart) against a strict JSON schema. That hypothesis is fed straight back through the
same `verify_settlegraph_invariants` gate every `AUTO_MATCH` already has to clear. Only if
every invariant passes does it get promoted — to a distinct `AI_RESOLVED_MATCH` label, never
`AUTO_MATCH`, so the audit trail always shows which matches were LLM-assisted. A hallucinated
candidate id, an out-of-range confidence, a timeout, a refusal, and a failed invariant are all
treated identically: the exception stays open, now carrying the model's rationale.

```bash
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=...
```

Off by default (`config.llm_provider = "none"`) — the core pipeline and its entire test suite
run with zero network and zero key. Turn it on per-run with `PipelineConfig(llm_provider="claude")`.
Fully unit-tested against a fake client (`tests/test_ai_reasoner.py`); nothing in the default
path calls out to Anthropic.

---

## 6. Quickstart

```bash
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q --basetemp .pytest-tmp   # 288 tests
```

**The whole demo, in two commands:**
```bash
python -m settlegraph.cli generate --total-records 1000 --seed 42
python -m settlegraph.cli run
```
`run` is one rail: settlement matching, GST tax-line reconciliation, Route
split reconciliation, the audit report, the plain-language digest, and a
recorded run-history row.

**Prove the claims:**
```bash
python -m settlegraph.cli benchmark    # vs. all three baselines
python -m settlegraph.cli simulate     # 7 failure-injection scenarios
python -m settlegraph.cli replay       # every decision re-derives identically
python scripts/stress_test.py  --records 20000
python scripts/noise_sweep.py  --records 500
python scripts/chaos_batch.py  --records 400
python scripts/eval_holdout.py --records 600 --seed 20260905
```

**Also available:** `serve` (dashboard on :8080), `history` (cross-run drift),
`digest`, `tax-match`, `route-reconcile`, and `ask "Why wasn't payment X
matched?"` (needs `pip install -e ".[llm]"` plus a key or an authenticated
`claude` CLI session).

Outputs land in `results/`: `assignments.csv`, `unmatched.csv`,
`exceptions.json`, `quarantine.json`, `revenue_assurance.json`,
`evaluation.json`, `summary.json`, `AUDIT_REPORT.md`, `DIGEST.md`.

`docs/adr/0006-pdf-compliance.md` is the honest checklist against the
official Track 04 brief.

---

## 7. What Broke & How We Found It

Full engineering log in **[`DEVLOG.md`](DEVLOG.md)**. Two entries matter most,
because they are the argument for how this project was built:

**A green test suite proved nothing.** The worst defect in this codebase — a
recycled UTR causing a confident `AUTO_MATCH` to the **wrong** payment — was
live while 130+ tests passed, precision read 100%, and a 20,000-record stress
run was clean. It was found by `datagen/adversarial.py`, a corpus written
specifically to make the engine confidently wrong. **Three** defects surfaced
that way; all are now closed with tests asserting the safe behaviour.

The third is the sharpest illustration: a Razorpay `adjustment` row scored
**0.95** against a bank settlement credit — byte-identical to an ordinary
payment — and was auto-booked, because `normalize_razorpay` hardcoded
`record_type="payment"` and `score_edge` never inspects it. No generated
batch could reach it (the generator only emits `entity_type="payment"`), so
it was reachable on **real** merchant data and not on ours. It still scores
0.95 today; `verify_record_type_invariant` demotes it to `EXCEPTION`.
Scoring is not the gate.

**The harnesses found their own bugs.** `scripts/chaos_batch.py` hard-crashed
the pipeline on its first run at 20% structural damage — ingest was
all-or-nothing, so one bad timestamp killed an entire file. Bad rows are now
quarantined with their row number and validation error. The harness existed
before the fix, which is the point of building it.

---

## 8. What This System Cannot Safely Do

A reconciliation system that only advertises its strengths is asking to be
trusted rather than checked. These are the real boundaries, measured or
reasoned, not softened.

**About 10% of the review queue is genuinely unnecessary.** Abstention
precision is **0.9016**: of 122 holds, 2 caught a wrong counterpart and 108
caught money that does not reconcile (unexplained gaps of ₹5–₹16,260 on
same-day, exact-UTR pairs). The remaining **12** are avoidable — exact
amounts held only because a 12–20 day settlement delay zeroes the
date-proximity score. Fixing that means changing `score_edge`'s date
handling, not the threshold: a sweep on the calibration split shows recall is
identical from 0.80 to 0.95, so lowering the bar buys nothing.

*An earlier version of this README reported abstention precision as 0.0164
and called the queue "98% noise". That metric counted any hold on a correct
counterpart as unjustified, ignoring whether the money reconciled. See
`docs/EVALUATION.md` §3 for the correction.*

**A simpler baseline beats it on recall.** Baseline B (amount + date window)
books 861 correct matches to our 823 at the same 100% precision on this
batch. Our safety margin cost 38 correct matches and prevented zero errors
*here*. The margin is insurance against conditions this batch does not
contain — which is an honest reason to keep it, not a demonstrated win.

**Its evidence of scale is bounded.** 20,000 records is measured. Beyond
~10⁶ records per batch the in-memory candidate graph is the first thing that
breaks. The progressive-noise sweep found no precision cliff below 30%
corruption — a bounded negative result, not a located breaking point.

**Its data is synthetic.** Every number comes from `datagen/generator.py`.
Split discipline (verified disjoint, no leakage) reduces but cannot eliminate
the risk that good scores mean "generalises to what our own generator
produces" rather than to a real merchant's feed. No real Razorpay data has
ever touched this code.

**Small denominators carry big-sounding rates.** `dangerous_miss_rate = 0%`
and `exception_recall = 100%` rest on **n=16** no-counterpart records in this
batch. Real and measured; also a small sample.

**It cannot decide what it has no evidence for.** One payment to many
invoices, many payments to one invoice, and aggregated settlements are not
modelled as first-class relationships — they surface as exceptions rather
than being resolved. A merchant with a heavily split-settlement profile would
see a large review queue, correctly but expensively.

**Its LLM layer is off by default and unmeasured on quality.** Every headline
number here comes from the deterministic path. `engine/ai_reasoner.py` has no
batch evaluation of its own hypothesis quality; what *is* proven is that
every failure path (timeout, refusal, malformed JSON, hallucinated candidate,
invariant failure) leaves the record exactly where the deterministic layer
put it.

**Under heavy corruption it becomes safe but nearly useless.** Malformed rows
are now quarantined rather than fatal (`results/quarantine.json`, counted in
`summary.json`), so the pipeline completes at every chaos level — but at 50%
structural damage recall falls to 13.32%. It is still never *wrong*; almost
everything simply lands in review. A wholly missing source file still raises
`FileNotFoundError`, deliberately: "nothing to reconcile" is a different
failure from "one bad row".

**Row-level quarantine covers validation failures, not unreadable files.**
`_load_with_quarantine` catches `ValidationError` per row, so a bad timestamp or
a failed cross-field invariant costs you that row and nothing else. It does not
catch failures raised while *reading* the file: `_read_csv` opens with
`encoding="utf-8"`, so a cp1252/latin-1 export (an Excel round-trip is the
realistic way this happens) raises `UnicodeDecodeError`, and a NUL byte or an
over-long field raises `csv.Error` — either one still aborts that entire source
file, the same all-or-nothing failure quarantine was built to remove. This is
unmeasured territory rather than a demonstrated weakness: none of
`chaos_batch.py`'s six damage axes can produce it, so we have no evidence about
how often it bites, and we chose to name it here rather than build a fix no
harness exercises.

**Storage and concurrency are single-process.** Flat files, one writer. A
multi-process deployment would need the run-history and idempotency state
shared before the duplicate and cumulative checks stay correct.

Full method, commands to reproduce every number, and the two real defects the
adversarial corpus found (and how they were fixed): **[`docs/EVALUATION.md`](docs/EVALUATION.md)**.

---

## 9. Contributing

Setup, the safety gates every change must clear, the commit convention, and the
evidence rule this project holds itself to — **[`CONTRIBUTING.md`](CONTRIBUTING.md)**.

Two things worth knowing before filing anything: abstaining is a *correct* outcome here,
so a patch that trades precision for recall will be turned down; and no number goes into
the docs unless the command that produces it is in the repo. That file also states what
the project is still missing — there is **no `LICENSE` yet**, so please ask about terms
before investing in a substantial contribution.
