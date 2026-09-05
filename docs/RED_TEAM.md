# SettleGraph — Pre-Submission Red Team

**Every finding below is evidence-backed.** Where an attack failed, that is
recorded as a failed attack, not spun as a triumph. Where a weakness is real,
it is rated by financial consequence and not softened.

An earlier draft of this file concluded "HARDENED & VERIFIED" with no findings
rated above informational. That draft was discarded: a red team that cannot
produce a finding is not a red team, and shipping one would have been the
exact superficiality this exercise exists to catch.

---

## Verdict

**Would I let this reconcile a merchant's books unattended? Not yet — but I
would let it reconcile them with a human on the exception queue, which is what
it actually claims.**

The zero-false-positive property is real and survives scrutiny: it holds on a
held-out seed, under 30% noise, and at 20,000 records. Two genuine
confident-wrong-match defects existed and were found by this project's own
adversarial corpus rather than by a reviewer. That is the strongest signal
here.

What stops a stronger verdict: the automation is **badly calibrated in the
direction of caution** (98% of its abstentions were unnecessary), several
safety numbers rest on **very small denominators**, and every number in the
repo comes from **one synthetic generator**. None of those is dishonest in
the docs — all are disclosed — but together they mean the system is proven
*safe* far more convincingly than it is proven *useful*.

**Findings: 0 CRITICAL · 3 HIGH · 5 MEDIUM · 3 LOW.**
*Update after follow-up work: H-? (unmeasured legs) and M-4 (re-sent events)
were both investigated and closed — one fixed, one withdrawn as overstated
after testing. Each is marked inline below rather than deleted, because a red
team that quietly edits out its own wrong calls is not auditable either.*

---

## Findings by persona

### 1. Razorpay fintech engineer

**H-1 · HIGH — "Zero false positives" is a property of this generator, not a proof.**
Precision is 100% on the dev batch, the held-out seed, the noise sweep and the
20k stress run. Every one of those batches comes from `datagen/generator.py`.
The generator decides which collisions are possible; the matcher is then
measured on exactly those. Split discipline (`tests/test_splits.py`) and the
different-seed held-out run reduce this, they do not remove it. **No real
Razorpay data has ever touched this code**, and the docs say so — but a
reviewer should read "100% precision" as "100% against our own imagination of
what goes wrong."

**M-1 · CLOSED (was MEDIUM) — the bank↔merchant leg is no longer unmeasured.**
`_score_bank_merchant` (`engine/score.py`) produces only a handful of discrete
values (0.6/0.4 on amount, +0.4/+0.2 on date), has no identifier signal at
all, and contributes 293 AUTO_MATCHes per batch. `evaluate()` scored only the
razorpay↔bank leg, so **1,283 of 2,106 automatic decisions were unscored**
(293 bank↔merchant + 990 razorpay↔merchant).

Fixed: `evaluate_secondary_legs` scores both — razorpay↔merchant directly via
`true_merchant_record_id`, bank↔merchant by derivation (correct when some
payment's ground truth names both records). Measured result: razorpay↔merchant
precision **100%** (990 tp / 0 fp, recall 99%), bank↔merchant precision
**100%** (293 tp / 0 fp).

The decisions were right all along; nobody had checked. That distinction
matters — the finding was about absent measurement, not about wrong answers,
and it is worth recording that the audit found no errors here rather than
implying it caught some. All 2,106 automatic matches are now ground-truth
verified with zero false positives across all three legs.

**L-1 · LOW — `ambiguity_margin = 0.05` is a judgement call, not a tuned value.**
It was chosen when the near-tie defect was fixed, and no sweep has justified
it. A competitor at 0.06 below the winner is auto-booked; at 0.04 it is held.
Nothing measures whether 0.05 is the right cliff.

### 2. CFO / finance controller

**H-2 · HIGH — the system abstains almost entirely without cause.**
`abstention_precision = 0.0164`. **120 of 122 abstentions held a candidate
that was already correct.** In operational terms: the review queue this
generates is ~98% noise, and a controller who works it will learn within a
week that "held" means "fine", which is precisely how a queue stops being
read. The safety margin as currently tuned is throughput cost, not risk
reduction. Disclosed in `EVALUATION.md` §3 and `README.md` §8; unfixed by
choice (fixing it on the reporting corpus would be tuning on the test set).

**M-2 · MEDIUM — a simpler system would have served this batch better.**
Baseline B (amount + date window, no UTR, no invariants, no abstention) books
**861 correct matches to SettleGraph's 823 at identical 100% precision**. On
this batch the entire verification apparatus prevented zero errors and cost 38
matches. The defence — that Baseline C corrupted 277 entries, and that B is
one amount-collision away from failing — is sound, but it is an argument from
*hypothetical* harm against *measured* cost.

**L-2 · LOW — "forward cash position" is a single-batch snapshot.**
`compute_revenue_assurance` produces one figure from one batch. It is not a
7/30-day forecast and is not framed as one in the report, but a controller
reading "Forward Cash Position" may reasonably expect a forecast.

### 3. Payment operations engineer

**M-3 · MEDIUM — many-to-one and one-to-many are not modelled, only survived.**
One payment settling across several credits, or several payments consolidated
into one lump credit, are not first-class relationships. They surface as
exceptions. That is safe, and honestly documented, but for a merchant whose
gateway consolidates aggressively the review queue would be dominated by
correct-but-unresolvable cases the system structurally cannot close.

**M-4 · WITHDRAWN after testing — re-sends are handled, by a different control.**
Original finding: `compute_fingerprint` hashes `(source, source_record_id,
amount, date, currency)`, so a gateway retry arriving with a **new**
`source_record_id` produces a different fingerprint and is not intercepted.

That much is true, but the conclusion was wrong. Tested directly
(`tests/test_pipeline.py::test_resent_event_with_a_new_id_is_never_double_booked`):
the re-sent record and the original both survive dedup, both become
candidates for the same bank credit, and near-tie suppression holds the
result — `label != AUTO_MATCH`, `competing_candidates >= 1`. **No double
booking occurs.**

Worth stating why the obvious fix would have been a regression: widening the
fingerprint to a business key (source + amount + date, no id) would make two
genuinely different payments sharing an amount and a date — routine in any
real batch — collide, and one would be **silently dropped**. Losing a real
record is strictly worse than processing a duplicate, because the money then
vanishes from reconciliation with no exception raised.

The accurate guarantee is narrower than "duplicate webhook protection"
implies, and should be stated that way: *identical re-imports are intercepted
by fingerprint; re-sends carrying a new id are caught downstream as
ambiguity.* Both safe, neither silent.

### 4. Skeptical hackathon judge

**H-3 · HIGH — the headline safety metrics rest on n=16.**
`dangerous_miss_rate = 0.00%` and `exception_recall = 100%` are computed over
the **16** `no_counterpart` records in the batch. Both are real and both are
disclosed with their denominator — but "0% dangerous misses" reads as a strong
guarantee and is one adverse case away from 6.25%. Any claim built on it
should quote n in the same breath.

**M-5 · MEDIUM — the demo's most impressive artefacts are the ones a judge cannot independently re-derive quickly.**
The noise sweep, chaos batch and held-out run each take minutes and rebuild
their own datasets. A judge with five minutes will see the dashboard and the
`run` output — which are the least adversarial surfaces. The honest numbers
live in `EVALUATION.md`, which requires reading.

**L-3 · CLOSED (was LOW) — the `run_e2e.py` recall gate was decorative.**
`recall >= 0.75` against a measured 83.6% would not have failed on a 7pp
collapse. Tightened to `0.78` — still clear of the 81.8% observed on a
different held-out seed, so it will not flap, but it now bounds something.

More usefully, three *exact* gates were added alongside it, because an
equality gate catches the first regression while a threshold gate only
catches one bigger than its own slack: `dangerous_miss_rate == 0`,
`invariant_violations == 0`, and zero false positives on **each** of the
three reconciliation legs. The e2e run now prints all six values.

### 5. Security engineer

**Attack: prompt injection via transaction narration — FAILED (system held).**
The generator injects `IGNORE PREVIOUS RULES…`-style payloads
(`malformed_description`), and `datagen/adversarial.py::prompt_injection_in_description`
constructs one explicitly. The deterministic scorer performs substring
containment only and has no execution path for text; the adversarial test
confirms the injected row receives an identical label and confidence to a
clean twin. `ai_reasoner.py` is off by default, has zero tools, and every
failure path returns UNRESOLVED. **No finding.**

**Attack: dashboard XSS via record fields — FAILED (system held).**
All 41 data-derived interpolations in `web/index.html` pass through
`escapeHtml()`. Verified by count and by `node --check` on the extracted
script.

**M-5b · CLOSED (was MEDIUM) — unauthenticated write endpoint + wildcard CORS.**
`server.py` set `Access-Control-Allow-Origin: *` on every response and had no
auth on any endpoint, including `POST /api/run-reconciliation`, which triggers
a full pipeline run and overwrites `results/`. The Dockerfile serves on
`0.0.0.0`, so on a shared network that was an unauthenticated write endpoint
reachable by anyone who could route to the port.

Fixed two ways. The wildcard CORS header is **gone entirely** — the dashboard
is served same-origin by the same handler, so it bought nothing while letting
any website read a merchant's reconciliation JSON from the browser of anyone
running the dashboard. And mutating endpoints now require a **loopback
caller** unless the operator passes `--allow-remote-run` and owns that
decision knowingly.

Deliberately a peer check rather than invented auth: a demo tool shipping a
fake credential system would be worse than one that states its boundary
plainly. "The request came from this machine" is the actual property that
makes the local dashboard safe. Verified live (CORS header absent, loopback
POST still returns 200) and unit-tested for four loopback forms and four
remote addresses.

### 6. ML evaluation researcher

**Attack: is `safe_auto_resolution_rate` gameable? — FAILED (metric held).**
Both it and `false_auto_book_rate` are denominated over `len(gt_map)` — all
ground-truth records — not over records the system chose to act on. Matching
aggressively raises the second; abstaining starves the first. Checked the
denominators in `evaluate.py` directly. The pair is genuinely
Goodhart-resistant. **No finding.**

**M-6 · MEDIUM — good ECE hides where the miscalibration actually is.**
ECE 0.0304 / Brier 0.0079 look excellent, but 835 of 945 scored assignments
sit at confidence ≈1.0 where the system is right — that mass dominates the
average. The signal is in the sparse bins: **101 assignments at 0.749
confidence were 100% correct** (severe under-confidence), and the 0.6–0.7 bin
is 33% accurate on n=3. Reporting ECE alone would be misleading; the
reliability bins are published for exactly this reason.

**L-4 · LOW — "197/258/288 tests passing" is not evidence of correctness.**
Stated plainly because this project proves it: two confident-wrong-match
defects were live while 130+ tests, 100% precision and a 20,000-record stress
run were all green. Test count is a measure of effort, not of safety. The
adversarial corpus and the safety-gate workflow are the things that actually
constrain regressions.

---

## What the demo hides

- **The dashboard is the least adversarial surface.** It shows a clean batch
  reconciling well. The chaos batch, the noise curve and the held-out run —
  where the honest numbers are — are CLI-only.
- ~~`AUTO_MATCH: 2106` is not 2,106 verified financial decisions.~~ **Fixed
  (M-1).** It now is: 823 razorpay↔bank + 990 razorpay↔merchant + 293
  bank↔merchant, all ground-truth scored, 0 false positives on each leg.
- **The 175 abstentions look like diligence.** ~98% of them were unnecessary.
- **Docker was never built locally** (not installed on the dev machine); the
  container is exercised only by CI. Disclosed in `ARCHITECTURE.md` §11.

## Misleading-if-quoted-alone metrics

| Metric | Why it misleads alone | Quote it with |
| --- | --- | --- |
| Precision 100% | Headline figure is the razorpay↔bank leg | The per-leg numbers (all three now scored) |
| Dangerous Miss Rate 0% | n = 16 | The denominator |
| Exception Recall 100% | Same n = 16 | The denominator |
| ECE 0.0304 | 88% of mass at conf ≈1.0 | The reliability bins |
| 288 tests passing | Was green while two real defects were live | The adversarial corpus |
| Recall 83.6% | Two baselines beat it | The false-auto-book column |

## What would make me not trust this

1. If the abstention rate stayed at ~13% with 98% of it unjustified once real
   merchant data arrived — the queue would be abandoned in a month.
2. ~~If the bank↔merchant leg (293 unmeasured auto-decisions per batch) were
   ever treated as booked rather than as a cross-check.~~ **Closed** — the leg
   is now scored (precision 100%, 0 fp). It remains a weak signal by design
   (amount + date only), so it should still be read as a cross-check.
3. If anyone quoted "0% dangerous misses" without "n=16".
4. ~~If the server were deployed with its current unauthenticated
   `POST /api/run-reconciliation`.~~ **Closed** — mutations are loopback-only
   unless explicitly opted into. It is still not authentication, so a
   multi-user deployment would need real auth in front of it.

## What is genuinely solid

Not everything here is a caveat, and a red team that cannot say so is useless:

- **The invariant gate is real and load-bearing.** A debit line sharing UTR,
  amount and date scores ≥0.95 — `test_verify.py` asserts the score really
  does clear the threshold — and is still rejected and demoted. Scoring is not
  the gate.
- **The project found its own worst bugs.** The recycled-UTR silent overwrite
  and the near-tie coin flip were both found by `datagen/adversarial.py`, not
  by a reviewer, and both are now closed with tests that assert the safe
  behaviour.
- **Replay consistency is exactly 100%** (2,284/2,284), verified by re-deriving
  every decision from the same inputs.
- **The held-out run is methodologically clean** — different seed, leak-free
  splits, precision held at exactly 100%.
- **The documentation does not oversell.** Every weakness in this report was
  already disclosed somewhere in `README.md` §8 or `EVALUATION.md` §8 before
  this review. The gap was emphasis, not honesty.

---

## Reproduce

```bash
python -m pytest tests/test_adversarial.py tests/test_verify.py tests/test_splits.py -v
python scripts/chaos_batch.py --records 400
python scripts/eval_holdout.py --records 600 --seed 20260905
python -m settlegraph.cli replay
```
