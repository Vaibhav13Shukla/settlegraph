# SettleGraph — 5-Minute Demo Runbook

> **"An AI controller that doesn't just reconcile your books. It proves its
> work and refuses to guess."**

Every command below is real and every number is one this repo actually
produces. If a number here disagrees with what the machine prints, trust the
machine — that disagreement is a bug in this document.

**Setup (once):**
```bash
.venv\Scripts\python -m pip install -e ".[dev]"
```

---

## 0. The problem, in one screen (20s)

Finance teams get three views of the same money — the payment gateway, the
bank statement, the internal ledger — and the three disagree. Fees, timing,
splits, refunds, duplicate webhooks, recycled reference numbers. Somebody
reconciles that by hand every month.

Most automation either matches the obvious rows and dumps the rest on a
human, or matches aggressively and quietly corrupts the books.

**The hard problem is not matching. It is knowing when automation has earned
the right to change the books.**

---

## 1. Generate a messy batch (30s)

```bash
python -m settlegraph.cli generate --total-records 1000 --seed 42
```

1,000 payments rendered into three imperfect source views, with 15% injected
anomalies across **12 kinds**: UTR corruption, missing references, MDR fee
variation, partial/full refunds, timing delays, split settlements, recycled
reference numbers, out-of-order arrival, unit-confusion amount errors,
malformed descriptions, currency drift, and settlements that genuinely never
reached the bank.

The ground truth is generated **first**; the three imperfect views are
rendered **from** it. So the answer key is real, not reverse-engineered — and
it never reaches the matching code.

---

## 2. Run it (30s)

```bash
python -m settlegraph.cli run
```

```
  Razorpay records: 1000    Bank records: 999    Merchant records: 1000
  Candidates:       3306
  AUTO_MATCH:       2106
  LIKELY_MATCH:      175      <- abstentions
  EXCEPTION:           3
  Unmatched:          43
  Invariant fails:     0

  Precision: 100.0%   Recall: 83.6%   F1: 0.9109
```

~1,500 records/sec. Point at the **abstention** line: that is the product.

---

## 3. The headline pair (30s)

Open `results/AUDIT_REPORT.md` or the dashboard.

| | |
| --- | --- |
| **Safe Auto-Resolution Rate** | **82.30%** |
| **False Auto-Book Rate** | **0.00%** |

Reported *together* on purpose. Matching aggressively raises the second;
abstaining on everything starves the first. Neither can be gamed without the
other moving — which is what makes the pair Goodhart-resistant.

Alongside them: **Dangerous Miss Rate 0.00%** and **Exception Recall 100%** —
of the 16 records that genuinely had no bank counterpart at all, the system
forced zero of them into a match.

---

## 4. Compare against three baselines — including where we lose (45s)

```bash
python -m settlegraph.cli benchmark
```

| Metric | A: Exact ID | B: Amount+Date | C: Fuzzy | **SettleGraph** |
| --- | --- | --- | --- | --- |
| Precision | 100.0% | 100.0% | 69.2% | **100.0%** |
| Recall | 84.9% | **87.5%** | 86.5% | 83.6% |
| True Positives | 835 | **861** | 621 | 823 |
| False Positives | 0 | 0 | **277** | **0** |
| False Auto-Book Rate | 0.00% | 0.00% | **27.70%** | **0.00%** |

**Say this out loud, do not skip it:** Baseline B beats us on recall — 861
correct matches to our 823, at the same 100% precision. Our safety margin
cost 38 correct matches and prevented zero errors *on this batch*.

Then point at column C: fuzzy matching without a verification gate corrupted
**277 ledger entries**. That is the failure a reconciliation system is
actually judged on, and it is why "just match more" is not free.

Baseline B's clean precision is a property of *this* batch, not of the
method — it has no UTR check, no invariant gate and no abstention. The
adversarial suite builds the amount+date collision where it fails.

---

## 5. Open one successful match (20s)

Dashboard → **Decisions** tab → the auto-matched card.

Both record ids, both amounts, both UTRs, the confidence, and a "why" line
assembled from the row itself — not a hardcoded sentence. Every accepted
match cleared confidence scoring **and** three deterministic invariants
(amount, date proximity, credit direction).

---

## 6. Open one abstention — the screen that matters (45s)

Same tab, the **Abstained** card.

```
Decision: LIKELY_MATCH (held, not booked)
Competing candidates: 1
Reason: 1 competing candidate within 0.05 of this score on the
        razorpay<->bank leg; held for review rather than auto-booked
        on a tie-break
```

This is the thesis. The rule for automation is three-part — *evidence above
threshold, **AND no competing explanation**, AND invariants hold* — and the
middle clause is enforced in code, not just documented.

**Worth telling honestly:** that clause was documented for weeks and
implemented nowhere. The adversarial corpus found it: a recycled UTR made the
candidate graph *silently overwrite* one of two colliding payments, after
which the bank credit was confidently auto-matched to the **wrong** payment
at 0.95. 130+ tests, 100% precision and a 20,000-record stress run were all
green while that was true. See `DEVLOG.md` Day 8.

---

## 7. Open one genuine exception (20s)

The **Exception** card: category, severity, root cause in plain language,
unexplained ₹ exposure, and a suggested remediation. Not "the AI couldn't
match this" — an actual diagnosis a finance controller can act on.

---

## 8. Show it fails closed (20s)

```bash
python -m settlegraph.cli simulate
```

7 injected failure scenarios, all contained. Then the **Fail-Closed** card:
every LLM failure path — timeout, refusal, malformed JSON, hallucinated
candidate id, or a hypothesis that fails an invariant — leaves the record
exactly where the deterministic layer put it. The model proposes; it never
decides.

---

## 9. Prove the decisions replay (20s)

```bash
python -m settlegraph.cli replay
```

```
  Decisions compared: 2284
  Reproduced identically: 2284
  Stability rate: 100.00%
```

"Auditable" is a claim. This is the check.

---

## 10. Show where it breaks (45s)

The part most demos skip.

```bash
python scripts/noise_sweep.py --records 500
python scripts/chaos_batch.py --records 400
python scripts/eval_holdout.py --records 600 --seed 20260905
```

- **Noise sweep — HEALTHY.** Precision held at 100% from 0% to 30%
  corruption while abstention rose 5.1% → 9.2% and recall absorbed the cost.
  It trades recall for safety, not precision for recall.
- **Chaos batch — SAFE but NOT RESILIENT.** Precision never broke. The
  breaking point is a **hard crash at 20% structural damage**: ingest is
  all-or-nothing, so one unparseable timestamp aborts the file. Real,
  measured, and named as a limitation.
- **Held-out — no overfitting.** On a seed the system was never tuned
  against, precision held at **exactly 100.00%**, recall moved −1.82pp.

---

## 11. The honest weakness (30s)

Do not let a judge find this before you say it.

**Abstention precision is 0.0164** — 120 of 122 abstentions held a candidate
that was *already correct*. The calibration data explains it: 101 assignments
scored at 0.749 confidence were 100% correct. The system is materially
*under*-confident in that band.

It is **not fixed**, deliberately: the fix is a threshold change, and tuning
it on the batch we report performance on would be tuning on our own test set.
The `calibration` split exists and is verified leak-free; that is where that
work belongs (`scripts/threshold_study.py`).

---

## 12. Close (15s)

> The value is not that AI can guess which rows match.
>
> The value is knowing when the evidence is strong enough to automate — and
> when the system should stop.

If a Razorpay engineer asks *"how do I know this won't quietly mess up my
books?"*: every automated decision is evidence-backed and invariant-gated,
ambiguous cases are explicitly abstained from with the competing candidate
named, performance is measured against hidden ground truth **and** a held-out
split, and every decision replays identically.

---

## Appendix — full verification in one go

```bash
python -m pytest -q                 # 258 tests
python scripts/run_e2e.py           # 1,000 records + 7 failure scenarios
ruff check src tests scripts datagen
```

Deeper reading: [`EVALUATION.md`](EVALUATION.md) (every number + method),
[`ARCHITECTURE.md`](ARCHITECTURE.md), [`RED_TEAM.md`](RED_TEAM.md),
[`../DEVLOG.md`](../DEVLOG.md) (what broke and how it was found).
