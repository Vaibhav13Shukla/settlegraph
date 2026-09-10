# SettleGraph — Phase 0 Forensic Engineering Audit

**Date:** 2026-09-10
**Scope:** `src/settlegraph/`, `datagen/`, `scripts/`, `tests/`, `docs/` (Track 4 = SettleGraph only; `refundguard/` is a separate submission and out of scope).
**Method:** Read the code before trusting the docs. Every claim below is cited to a file and, where useful, a line. Ran the suite, reproduced the headline metrics from committed artifacts.
**Status:** Read-only. No implementation code was modified to produce this audit.

---

## 0. Executive summary

SettleGraph is **not** a thin demo. It is a mature, iteratively-hardened reconciliation engine (~20 engine modules, 34 test files, 9 ADRs) whose core safety architecture — *scoring proposes, deterministic verification decides, AI never becomes financial authority* — is real and defensible in code, not just in the README.

The expert's critique is best read not as "your code is bad" but as **"your product definition and domain realism lag your engineering."** That framing holds up against the code:

- **The engineering is strong and honest.** Duplicate interception, an invariant gate that *demotes* high-confidence matches, candidate-collision handling, fail-closed AI, and a "baseline beats us on recall" result the project reports against itself are all present and genuine.
- **The domain model and product surface are the real gaps.** No merchant identity (single implicit tenant), no operator review lifecycle (the dashboard is read-only over batch artifacts — there is no approve/reject/resolve that persists), generation-time ID correlation that makes the synthetic world tidier than real feeds, and count-only evaluation with no amount-weighted exposure metric.

The single most consequential unknown is **not in the code**: how much of this must the author personally defend, and on what clock. That decides whether the remaining phases are a genuine rebuild or a documented-assumptions pass. It is raised as the one open decision in §11.

---

## 1. Current architecture (as implemented)

Pipeline entry point: [`engine/pipeline.py:131`](../src/settlegraph/engine/pipeline.py) `run_pipeline()`.

```
CSV sources
  → ingest with row-level quarantine        (engine/ingest.py, load_all_with_quarantine)
  → normalize to NormalizedRecord           (engine/normalize.py)
  → idempotency shield (SHA-256 dedupe)      (engine/idempotency.py, wired in pipeline.py:173)
  → candidate graph                          (engine/match.py, build_candidate_graph)
  → weighted edge scoring                    (engine/score.py, Fellegi-Sunter-style weights)
  → within-batch drift check (ADWIN)         (engine/drift.py, sampled)
  → global assignment + near-tie abstention  (engine/assign.py, global_assign)
  → deterministic invariant GATE (demotes)   (engine/verify.py + pipeline.py:55 enforce_invariant_gate)
  → exception diagnosis                      (engine/exceptions.py)
  → [optional] AI-assisted resolution        (engine/ai_reasoner.py, off unless llm_provider="claude")
  → revenue assurance + reports + digest     (engine/report.py, engine/digest.py)
  → evaluation vs ground truth (offline)     (engine/evaluate.py)
```

Satellite surfaces run when their feeds exist: GST-on-fee tax matching ([`engine/tax_matcher.py`](../src/settlegraph/engine/tax_matcher.py)) and Route marketplace-payout splits ([`engine/route_reconciliation.py`](../src/settlegraph/engine/route_reconciliation.py)).

Serving: a zero-dependency stdlib HTTP server ([`server.py`](../src/settlegraph/server.py)) exposing read-only JSON endpoints + one loopback-gated `POST /api/run-reconciliation` + `POST /api/ask`. UI is a single 78 KB `web/index.html`.

**Key architectural strength (verified):** the auto-match gate is *not* the score. `enforce_invariant_gate` ([`pipeline.py:55`](../src/settlegraph/engine/pipeline.py)) demotes any `AUTO_MATCH` that fails `verify_settlegraph_invariants` to `EXCEPTION` in place, with a dedicated `INVARIANT_VIOLATION` report. This is the concrete implementation of "scoring proposes, verification decides."

---

## 2. Actual data model

Source models ([`models/`](../src/settlegraph/models)):

| Model | Key fields | Notable |
|---|---|---|
| `RazorpaySettlementRecord` | `entity_type` ∈ {payment, refund, transfer, adjustment}, `settlement_id`, `settlement_utr`, gross/fee/tax/net paise | `net_must_balance` validator (razorpay.py:25): net == amount − fee − tax for payments |
| `BankStatementRecord` | `reference_number` (UTR), `credit`/`debit` paise, `value_date` | `exactly_one_direction` validator (bank.py:18) |
| `MerchantLedgerRecord` | `payment_gateway_id`, `order_id`, `invoice_number`, `transaction_type` | |
| `GSTInvoiceRecord`, `RoutePayoutRecord` | tax + marketplace-split surfaces | |
| `GroundTruthRecord` | `razorpay_record_id`, `true_bank_record_ids: list`, `true_merchant_record_id`, `relationship_type` ∈ {exact_match, split, refund_of, no_counterpart} | Already relationship-typed, already list-valued. (`merge`/`adjustment_for`/`timing_only` were dead enum values, removed — see §16.) |
| `NormalizedRecord` | unified; `record_type` ∈ {payment, refund, settlement_credit, sale, adjustment, unknown}; `provenance.direction` | The lingua franca of the engine |

**Money is integer paise throughout.** No floats in the financial path. Good.

**Gap — no tenancy:** there is **no `merchant_id` / `store_id` / `operator_id`** on any model. The entire batch is one implicit merchant. Multi-merchant isolation (expert §N) cannot be expressed today.

---

## 3. Current assumptions (some explicit, some latent)

1. **Reconciliation unit = payment ↔ bank credit**, cross-checked against merchant ledger, with settlement batching *present but not the matching key*: `settlement_id = setl_{index//20}` groups ~20 payments (generator.py:219), and GST invoices are raised one-per-settlement-batch (generator.py:433). Matching itself is payment-grained. — *This is the smallest defensible reading of the code and is adopted as the documented assumption (see §11, Decision D2).*
2. **One currency (INR)** except an injected `currency_mismatch` anomaly.
3. **Day-1 bank view is settlement credits only**; refunds are modeled as reduced net credit, not yet as separate debit rows (generator.py:207-214, acknowledged in-code).
4. **Single tenant.** No merchant identity.
5. **Bounded batch, single writer.** No concurrent operators, no persistent operational state beyond `results/` artifacts + `history.jsonl`.

---

## 4. Actual invariants (the safety core)

[`engine/verify.py`](../src/settlegraph/engine/verify.py), run only on razorpay↔bank `AUTO_MATCH` candidates:

- **Amount** — |rzp_net − bank_credit| ≤ 100 paise (₹1). (verify.py:17)
- **Date** — |settlement_date − bank_txn_date| ≤ `date_tolerance_days`. (verify.py:33)
- **Direction** — a bank *debit* cannot satisfy a settlement *credit* match. (verify.py:48)
- **Record type** — a Razorpay `refund`/`adjustment` cannot be booked as a payment. (verify.py:72)

The last two exist because adversarial testing found real defects: `new_transaction_category` (adjustment scoring 0.95, byte-identical to a payment) and a debit-as-credit path. Both were fixed by *preserving* `entity_type` through normalization (normalize.py:36) and adding the invariant — a genuine, tellable engineering story.

**Scope caveat:** invariants cover razorpay↔bank only. razorpay↔merchant and bank↔merchant auto-matches are *not* invariant-gated (they rely on identifier/amount signal + the secondary-leg evaluator). Documented honestly in `enforce_invariant_gate`'s docstring.

---

## 5. Current failure modes & how they're handled

| Failure | Handling | Evidence |
|---|---|---|
| Unparseable CSV row | Quarantined, counted, written to `quarantine.json` | ingest.py, pipeline.py:157 |
| Retried/duplicate ingestion | SHA-256 idempotency shield drops before scoring | pipeline.py:173 |
| UTR collision (recycled ref#) | Both kept as candidates → assignment abstains, never books both | match.py:74, DEVLOG story |
| High-confidence wrong match | Invariant gate demotes to EXCEPTION | pipeline.py:55 |
| Near-tie (ambiguous winner) | Demoted AUTO→LIKELY with reason | assign.py:153 |
| No true counterpart | `no_counterpart` correctly abstained; dangerous-miss tracked | evaluate.py:244 |
| LLM outage/timeout/malformed/hallucinated id | Fail closed → `UNRESOLVED`, record stays EXCEPTION | ai_reasoner.py:235 |

---

## 6. Current test coverage & suite state

**Ran `pytest` (this Windows env): 228 passed, 81 errored, 38 s.**

**The 81 errors are all `PermissionError: [WinError 5]`** during temp-file/temp-dir setup or teardown — concentrated in `test_ingest`, `test_splits`, `test_server`, `test_qa_agent`, `test_report`, `test_history_store` (all of which write real temp files). These are **environmental Windows file-handle failures, not assertion failures**: no logic assertion failed. Still a real problem — the Windows dev loop is degraded and these tests give no signal here. **Recommended early fix** (low risk): ensure temp files are closed before reopen / use `tmp_path` consistently with explicit handle close on Windows.

Coverage is broad: unit (models, normalize, score, match, assign, verify), property-based (`test_property_based.py`, money/assignment invariants), adversarial (`test_adversarial.py`), baselines, calibration, evaluation, e2e, chaos batch, threshold study. This is stronger than typical for a hackathon submission.

---

## 7. Benchmark methodology & reproduced numbers

`evaluate()` ([`engine/evaluate.py:177`](../src/settlegraph/engine/evaluate.py)) definitions (verified):

- **Precision** = TP / (TP + FP), where a positive is an `AUTO_MATCH`/`AI_RESOLVED_MATCH` razorpay↔bank pair whose bank id is in that payment's `true_bank_record_ids`.
- **Recall** = TP / (TP + FN); only auto-booked matches count as TP by design (LIKELY_MATCH deliberately does not).
- Also computes: `safe_auto_resolution_rate`, `false_auto_book_rate`, `dangerous_miss_rate`, `exception_recall`, per-anomaly breakdown, and the two previously-unmeasured legs (razorpay↔merchant, bank↔merchant) via `evaluate_secondary_legs`.

**Reproduced from committed `results/evaluation.json`:** precision **1.0**, recall **0.8364**, F1 **0.9109**, TP **823**, FP **0**, FN **161**. The recall gap is entirely injected anomalies (partial_refund 47, full_refund 21, split_settlement 16, missing_utr 14, out_of_order 13, fee_mismatch 11, extreme_amount 9 …) that the system abstains on rather than mis-booking.

**Ground-truth isolation (verified):** the engine's *decision* path (`match`/`score`/`assign`/`verify`) never imports `ground_truth`. Only `evaluate.py` (the evaluator) and `pipeline.py`'s evaluation phase read `ground_truth.csv`. Isolation holds.

**Nuance on the "baseline beats us" story:** `count_correctly_flagged_for_review` (evaluate.py:20) exists precisely to explain that a zero-safety-margin baseline can post higher raw recall while being strictly less safe — the "lost" matches are correctly identified and *conservatively held*, not missed. This is the project's strongest honesty artifact and it is real.

---

## 8. AI usage & safety boundary (answers "which AI, and is it safe?")

Two distinct AI surfaces, both optional and both fail-closed:

1. **Exception reasoner** ([`ai_reasoner.py`](../src/settlegraph/engine/ai_reasoner.py), off unless `llm_provider="claude"`). The model is shown *only* the unresolved record and a bounded candidate list — **no tools, no ground truth, no other merchants' data**. It returns a hypothesis constrained to a shown `candidate_record_id`; a hallucinated id is treated like malformed JSON (parse_verdict:172). Any promotion must clear the *same* deterministic invariant gate and is labeled `AI_RESOLVED_MATCH`, never `AUTO_MATCH`. Every error path → `UNRESOLVED`.
2. **QA agent** ([`qa_agent.py`](../src/settlegraph/qa_agent.py)) — read-only Q&A over `results/` with a deterministic fallback when no key is present (server.py:335). Tests assert it enables *exactly* the read-only ledger tools and *no* dangerous builtin (`test_qa_agent.py`).

**This is the precise, correct answer to "an LLM can't do arithmetic":** the LLM never does arithmetic and never books anything. Deterministic code recomputes every total and owns every decision. Model id in code: `claude-opus-5`.

---

## 9. Expert feedback → code reality mapping

| # | Expert point | Verdict | Evidence / nuance |
|---|---|---|---|
| A | Synthetic IDs too correlated | **TRUE (at generation)** | All ids derive from one `index` (generator.py:215-230); bank description embeds UTR+settlement_id; merchant copies payment_id. |
| — | "Matches on last 6 digits" | **FALSE (literal)** | Engine keys on UTR/order_id/payment_id/amount/date (match.py, score.py), not a numeric suffix. Coach is correct that the *dataset* criticism still lands. |
| B | Define reconciliation semantics | **Partial** | `entity_type`/`record_type` exist and are enforced; refund/adjustment/settlement distinguished. No merchant identity; refunds not yet separate debit rows. |
| C | Support 1:N / N:1 aggregation | **Partial** | `split` (1→2 bank credits), GST 20:1 batch aggregation, Route 1:N payouts all exist; `merge` (N:1) enum value defined but **never generated or matched**. |
| D | Refund/reversal/fee semantics | **Partial** | Refund + fee + tax modeled; reversal/chargeback not distinct types. |
| E | Realistic payment rails | **Partial** | UPI/card/netbanking/wallet/emi + ICICI/HDFC/RazorpayX; timing/aggregation not rail-differentiated. |
| F | Explicit primary user | **MISSING** | Directive §6 fixes it: Razorpay internal reconciliation analyst. Not modeled (no operator_id). |
| G | Operator review lifecycle | **MISSING** | No state machine, no persisted decisions. Dashboard is read-only (server.py). |
| H | Real review queue with actions | **MISSING** | No approve/reject/reclassify/resolve endpoint exists. |
| I | Separate finance vs engineering UI | **MISSING** | One dashboard mixes exposure with chaos/replay/calibration. |
| J/K | Business metrics vs ML metrics; precision/recall semantics | **Partial** | ML metrics + revenue_assurance exist but are co-mingled; definitions are correct but offline-vs-live not documented. |
| L | Don't optimize F1 (asymmetric cost) | **Aligned in spirit** | System is precision-first by construction; not stated as an explicit cost model. |
| M | 100k scale | **Honest gap** | Measured to 20k; in-memory candidate graph is the named first bottleneck (match.py comments). No false 100k claim. |
| N | Multi-tenant isolation | **MISSING** | No merchant_id anywhere. |
| O | Real value to Razorpay | **Defensible, prototype-only** | README already disclaims production readiness + synthetic data. |

---

## 10. Highest-impact weaknesses (ranked)

1. **No operator review lifecycle / persistence (G, H).** The biggest product gap and the one the expert pressed hardest. There is nowhere for a human decision to live.
2. **No merchant identity / tenancy (F, N).** Blocks the stated primary user's core mental model ("which merchants are affected?").
3. **Generation-time ID correlation (A).** Makes the clean 85% tidier than real feeds. *Note the blast radius:* fixing it touches `_reality`/`_truth`/`_write_splits` (which hardcode `bank_{i}`, `led_{i}`, and `.replace('pay_','')`) and **invalidates every published number** (823/100%/83.64%) until data is regenerated. Evaluation *structure* already uses an explicit relationship table, so it does not need rewriting — only the generator and the docs' numbers.
4. **Metrics co-mingled; no amount-weighted exposure in `evaluate` (J, K).** A wrong ₹10L match and a wrong ₹100 match count equally today.
5. **Windows test suite degraded (81 temp-file errors).** Not logic, but it blinds the dev loop here.
6. **`merge` (N:1) is defined but never exercised (C).** An enum value pretending to be a capability.

---

## 11. Open decision (genuinely the author's; not derivable from code)

Everything above is settled by evidence. Exactly one thing is not, and it changes the shape of every later phase:

- **D1 — Change depth & defend-clock:** How much of SettleGraph must the author personally understand and defend in the interview, and how much time is there before it? This decides whether Phases 4–14 are a *real rebuild* (invasive, higher-risk, more to defend) or a *documented-assumptions pass* (surgical, lower-risk, each change small enough to explain line-by-line). A large AI-generated diff the author cannot defend would recreate the exact failure the expert exposed.

Provisional decisions taken under §5 of the directive ("smallest defensible assumption, documented, reversible"):

- **D2 — Reconciliation unit:** payment ↔ bank settlement credit, cross-checked to merchant ledger, with settlement batching retained as a grouping (not the match key). Documented; reversible.
- **D3 — Primary user:** Razorpay internal reconciliation analyst managing a merchant portfolio (per directive §6).
- **D4 — Money:** integer paise, deterministic arithmetic only; LLM never authoritative. (Already true; keep.)

---

## 12. Proposed change order (subject to D1)

Preserving all existing safety engineering, in dependency order:

1. Add `merchant_id` to models + generator + isolation tests (unblocks F/N and the primary-user UI).
2. Independent, opaque source IDs + regenerate → refresh every published number in one commit (A). Ground truth stays the relationship table it already is.
3. Operator review lifecycle: state machine + persisted `ReviewDecision` + audit events + real approve/reject/reclassify/resolve endpoints, with optimistic-concurrency protection (G/H).
4. Amount-weighted exposure metrics in `evaluate` + a documented offline-vs-live split (J/K).
5. Split the UI into finance-operations vs admin/engineering surfaces (I).
6. Fill genuine domain gaps only where they buy defensibility: `merge` N:1 or delete the dead enum; reversal/chargeback types (C/D).
7. Fix the Windows temp-file test errors.
8. Refresh docs/ADRs to match, marking IMPLEMENTED vs PROPOSED.

Each step: change → run tests → inspect diff → verify → document → proceed.

## 13. Risks

- **Metric invalidation** (weakness #3): any ID/data change strands the README's numbers. Must regenerate and refresh in the same commit or the repo ships stale numbers next to new data.
- **Defensibility risk** (D1): the deeper the rebuild, the more the author must be able to explain unaided.
- **Scope risk:** the directive is 30 phases; this repo is mature. Grinding all phases blindly would add volume, not correctness. Correctness-per-line is the target.

---

## 14. Post-change measured outcome (this session)

Implemented the first slice of the focused rebuild: **merchant identity + cross-merchant isolation (ADR 0010)** and **independent opaque source identifiers (ADR 0011)**, then regenerated the seeded 1,000-record batch and refreshed every published number in the same change.

**Refreshed headline (seed 42, 1,000 records):** precision **1.0**, recall **0.8449** (was 0.8364), F1 **0.9159**, TP **839**, FP **0**, FN **154**, false-auto-book **0.00%**, invariant violations **0**. Held-out (seed 20260905): precision 100%, recall **0.7921**. Replay: **2,308/2,308** identical.

**Isolation is non-vacuous:** on this batch the merchant guard suppresses **23** cross-merchant candidate pairs that would otherwise be scored; a dedicated deterministic test (`tests/test_match.py::test_candidate_graph_never_links_across_merchants`) proves a same-UTR pair across two merchants never becomes a candidate.

**Benchmark-narrative correction (important, honest finding):** with realistic independent identifiers, **all three naive baselines reach 100% precision with 0 false auto-books** and each finds slightly *more* true positives than SettleGraph (A 854, B 866, C 872 vs 839), because they never abstain. The previously-reported fuzzy-baseline **27.70% false-auto-book rate is retracted** — it was an artifact of the old sequential `RZP{index:012d}` UTR format (near-identical strings fool `difflib.SequenceMatcher`); real 16-char random UTRs do not resemble each other. The data-realism fix therefore *invalidated the project's own headline benchmark claim*, and that is reported rather than re-manufactured (round-amount clustering was considered and rejected as tuning-to-a-result). SettleGraph's safety case now rests on abstention under ambiguity, merchant isolation, and the invariant gate — demonstrated per-scenario in `datagen/adversarial.py`, not on out-scoring strawmen on an easy batch.

Full suite (clean temp dir): green. The "81 Windows errors" in §6 were purely a poisoned `pytest-of-LENOVO` temp root; a clean `--basetemp` makes the whole suite pass.

---

## 15. Operator review lifecycle (this session)

The highest-impact gap from §10 is closed. A persisted review-decision layer
(`engine/review.py`, ADR 0012) sits as an **overlay** over the immutable batch
artifacts — it never mutates `assignments.csv`/`exceptions.json`, so replay
determinism is preserved.

- **State machine:** `OPEN → APPROVED / REJECTED / RESOLVED`, plus `RECLASSIFY`
  (re-triage in place) and `REOPEN` (un-close). One transition table; illegal
  moves raise `IllegalTransition`.
- **Audit trail:** append-only, every transition records event id, case,
  merchant, actor, action, previous/new status, reason, timestamp.
- **Optimistic concurrency:** per-case `version`; a stale decision is refused
  with `ConcurrencyConflict` (HTTP 409) — double-approval is impossible and
  tested.
- **Endpoints:** `GET /api/review-queue` + `GET /api/audit` (read-only, both the
  stdlib server and the FastAPI adapter); `POST /api/exceptions/<case>/<action>`
  behind the existing loopback mutation gate (local console only; hosted demo is
  read-only). `ExceptionReport` now carries `merchant_id` so the queue groups by
  portfolio.
- **UI:** a real Review queue tab in `web/index.html` with working
  Approve/Reject/Reclassify/Resolve buttons calling those endpoints, a reviewer
  id (recorded, not authenticated — demo), per-case reason, and 409 handling.
- **Verified live:** approving a case dropped the open badge 208→207, removed it
  from the queue, and wrote an `OPEN→APPROVED` audit event. 16 new tests
  (`tests/test_review.py`); full suite 326 passed.

**Still explicitly unbuilt** (honest scope): authentication / per-operator
authorization (isolation is enforced in the engine, not at an access boundary),
multi-writer durability (single-writer file store; the `ReviewStore` interface
is the seam for a DB later), and a hosted mutable console.

---

## 16. Remaining expert-feedback pass (this session)

After the focused rebuild (§14–15), the rest of the §9 feedback set was worked
through in dependency order, one small commit each, each pushed so CI validated
it. Correctness-per-line, not volume (§13).

- **AI-path merchant isolation (closes a real bypass of A/F/N's guarantee).**
  `ai_reasoner._widen_candidates` scans the bank pool on date+amount and does
  not go through `build_candidate_graph`, and `verify_settlegraph_invariants`
  had no merchant check — so an AI-widened cross-merchant leg could be booked as
  `AI_RESOLVED_MATCH`. Closed at the authoritative gate
  (`verify_merchant_invariant`, fail-safe on unknown ids) plus a defense-in-depth
  filter in the widen scan. ADR 0010 updated; the guarantee now holds on the AI
  path, not only the deterministic one.
- **Amount-weighted exposure (J/K).** `evaluate()` now reports the auto-booked
  decisions weighted by settlement amount: `false_auto_booked_exposure_inr`
  (rupees booked onto a wrong counterpart — **₹0.00** on seed 42, the claim
  worth making, not "precision 1.0"), `worst_case_single_auto_book_inr`
  (**₹41,088.48**, blast radius of one unattended decision), and
  `amount_weighted_precision`. Kept deliberately distinct from
  `revenue_assurance`'s live view rather than co-mingled (the real J/K defect);
  FN is not rupee-weighted because ground truth carries no amount — documented,
  not reconstructed from a partial source. EVALUATION.md §1a.
- **Reversal / chargeback (D).** Added to the adversarial corpus (not the main
  generator, so zero seeded-batch churn): a Razorpay `refund`/reversal caught by
  the record-type invariant, and a bank *debit* chargeback caught by the
  direction invariant — two failures exercising two different invariants.
- **Dead enum removed (C).** `relationship_type` dropped `merge`,
  `adjustment_for`, `timing_only` — never generated, matched, evaluated, or in
  any committed data. `merge` in particular claimed an N:1 capability the system
  does not have; real N:1 is a reconciliation-unit change, not an enum edit.
- **Dashboard grouped Operations vs Engineering (I).** The one dashboard's nav
  is now two labelled groups (same tabs, same `switchTab`); the exposure figure
  is surfaced in the Operations cockpit.

**Deliberately NOT done, and why:** authentication/authorization (ADR 0012
rejected invented per-user auth as fake security — reversing that to clear a
checklist is worse than the documented gap); real N:1 aggregation (changes the
reconciliation unit D2 and re-invalidates every number — a separate decision);
100k-scale claims (measured honestly to 20k; an unmeasured "scales to 100k" is
the exact fabrication the expert probed for); and a `FINAL_ENGINEERING_REVIEW.md`
(a directive deliverable for a 30-phase run that was not performed — this audit
plus ADRs 0010–0012 are the record).

Suite grew 326 → **337 passed**; branch CI green across the 9-cell matrix.
