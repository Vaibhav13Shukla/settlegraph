# SettleGraph Architecture

> *The model may propose. The ledger must verify. The system must explain.*

This document describes what actually exists in this repository, and — kept
deliberately separate — how those pieces would map onto separate services in
production. Nothing below describes a component that is not in the code.

---

## 1. What this system is

SettleGraph ingests three disagreeing views of the same financial reality
(Razorpay settlements, bank statements, merchant ledger), decides which
records belong together, and — the part that actually matters — **refuses to
decide when the evidence is not strong enough**, routing that record to a
human with a diagnosed reason instead of guessing.

The hard problem is not matching. It is calibrated abstention: knowing when
automation has earned the right to change the books.

---

## 2. Runtime shape (what is actually deployed)

SettleGraph today is **one deployable unit** — a Python package with a CLI
entry point and an embedded HTTP server — not a microservice mesh. This is a
deliberate choice, not an unfinished one: every boundary below is a real
module boundary with a real contract, and none of them currently need a
network hop between them to be correct. Splitting them into separate
processes would add failure modes (partial writes, retry semantics, cross-
service transactionality) without adding a single capability at this batch
size. §6 documents where the seams are when that stops being true.

```
                    ┌──────────────────────────────────┐
                    │  Browser dashboard               │
                    │  web/index.html (Tailwind, no    │
                    │  build step, no framework)       │
                    └────────────────┬─────────────────┘
                                     │ fetch() over HTTP/JSON
                                     ▼
                    ┌──────────────────────────────────┐
                    │  server.py                       │
                    │  stdlib http.server              │
                    │  GET  /api/summary               │
                    │       /api/evaluation            │
                    │       /api/exceptions            │
                    │       /api/assignments           │
                    │       /api/revenue-assurance     │
                    │       /api/audit-report          │
                    │       /api/simulate-failures     │
                    │       /healthz                   │
                    │  POST /api/run-reconciliation    │
                    └────────────────┬─────────────────┘
                                     │ in-process call
                                     ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  engine/pipeline.py — run_pipeline()                            │
   │  the single orchestration rail; every surface below rides it    │
   └─────────────────────────────────────────────────────────────────┘
```

`cli.py` (Typer) is the other entry point onto the same rail:
`generate`, `run`, `benchmark`, `simulate`, `ask`, `tax-match`,
`route-reconcile`, `history`, `digest`, `serve`.

---

## 3. The pipeline, phase by phase

Every phase is a separate module with a pure-function core. The phase numbers
match the progress output `run_pipeline` actually prints.

| Phase | Module | Responsibility | Fails how |
| --- | --- | --- | --- |
| 1. Ingest | `engine/ingest.py` | CSV → Pydantic v2 models. Schema violations reject the row, they do not coerce it. | Validation error, loudly |
| 2. Normalize | `engine/normalize.py` | Three source schemas → one `NormalizedRecord`. Integer paise only; floats never enter the money path. | Unparseable date raises |
| 2.5. Idempotency | `engine/idempotency.py` | SHA-256 fingerprint per record; duplicate webhook/batch replays are intercepted before anything downstream sees them, logged to `duplicates.json`. | Duplicate dropped + audited |
| 3. Candidate graph | `engine/match.py` | Generate plausible links (UTR key, date-bucketed amount scan). Blocking is date-bucketed, not quadratic. | No candidate → orphan path |
| 4. Score | `engine/score.py` | Fellegi-Sunter weighted linkage, source-pair aware (UTR 0.60 / amount 0.25 / date 0.10 / description 0.05 on the razorpay↔bank leg). | Low score → not auto |
| 5. Assign | `engine/assign.py` | Global bipartite assignment under **per-leg** 1-to-1 exclusivity. Deterministic tie-break on record ids, so the result never depends on candidate-generation order. | Conflict → at most one wins |
| 6. **Invariant gate** | `engine/verify.py` + `pipeline.enforce_invariant_gate` | Amount, date-proximity, and credit-direction invariants. **An AUTO_MATCH that fails any invariant is demoted to EXCEPTION**, with the actual violation named. Scoring is not the gate; this is. | Demote + diagnose |
| 7. Unmatched | `engine/assign.py` | Records no edge claimed become orphans, never silently dropped. | Surfaced as unmatched |
| 7a. Diagnose | `engine/exceptions.py` | Root-cause classification per exception (UTR corruption via Levenshtein, missing UTR, amount/fee mismatch, timing, missing counterpart, ambiguous). | Categorised + ₹ exposure |
| 7.5. AI reasoning | `engine/ai_reasoner.py` | **Optional, off by default.** Proposes a hypothesis for exceptions the deterministic layer could not clear. | Any failure → stays EXCEPTION |
| 8. Report | `engine/report.py`, `engine/digest.py` | Revenue assurance, forward cash position, audit report, plain-language digest. | — |
| 9. Evaluate | `engine/evaluate.py` | Scores the whole batch against hidden ground truth. | — |

Satellite reconciliation surfaces ride the same rail when their feeds exist:
`engine/tax_matcher.py` (GST on MDR fees) and
`engine/route_reconciliation.py` (marketplace payout splits).

---

## 4. Where AI runs, and what it is not allowed to do

Two LLM surfaces exist. They made **opposite** design choices on purpose.

**`engine/ai_reasoner.py` — proposes, never decides.** Plain `anthropic`
package, strict JSON-schema output, **zero tools**. It sees an unresolved
exception and a candidate shortlist, and returns one hypothesis. That
hypothesis is fed straight back through `verify_settlegraph_invariants` —
the *same* deterministic gate every `AUTO_MATCH` clears. Only if every
invariant passes is it promoted, and it is promoted to a distinct
`AI_RESOLVED_MATCH` label, never `AUTO_MATCH`, so the audit trail always
shows which matches were LLM-assisted. A timeout, rate limit, refusal,
malformed JSON, out-of-range confidence, or hallucinated candidate id are all
treated identically: **the record stays EXCEPTION**. Fail closed, every path.

Giving this component tools would be a safety regression, not an upgrade —
it is one step from a ledger entry.

**`qa_agent.py` — investigates, cannot mutate.** The real
`claude-agent-sdk`, with a genuine multi-turn tool-calling loop. Six
read-only tools over files `run_pipeline` already wrote. Blast radius is
restricted twice over (empty built-in `tools` preset *and* an explicit
`disallowed_tools` list), because relying on a single control to hold is
exactly the failure mode this codebase refuses elsewhere. There is no tool in
its allow-list that can move a record between labels. A fully subverted agent
can hand a reviewer a wrong *explanation*; it cannot touch the ledger.

It also has an `arithmetic` tool and a system prompt forbidding mental math —
added after a live run where it did correct-but-unverified arithmetic in an
answer. Every other number in this system is correct by construction; an
answer should meet the same bar.

**Untrusted text is data.** Bank descriptions, merchant names, and notes are
never interpreted as instructions. The deterministic scorer cannot execute
text at all; the corpus in `datagen/adversarial.py` includes an explicit
injection payload (`"IGNORE PREVIOUS RULES..."`) to keep that testable rather
than assumed.

---

## 5. Storage — and why it is not SQLite

**Verified fact about this repo:** every artefact is a plain file.

| Artefact | Format | Why |
| --- | --- | --- |
| Source feeds | CSV | What merchants actually export |
| `assignments.csv`, `unmatched.csv` | CSV | Openable in the tool a finance controller already uses |
| `exceptions.json`, `revenue_assurance.json`, `summary.json`, `evaluation.json`, `duplicates.json`, `ai_resolutions.json` | JSON | Structured, diffable |
| `history.jsonl` | JSON Lines | Append-only run history; `cat`/`tail`/`grep`-able |
| `AUDIT_REPORT.md`, `DIGEST.md` | Markdown | Read by a human, not a client library |

The Track 04 brief names "SQLite / PostgreSQL" as the suggested storage
layer. This uses neither, and that is a decision with a stated reason
(`engine/history_store.py`, ADR 0006): every operation the persistence layer
actually performs — append a row, list recent rows, read one metric as a
chronological series — is a few lines over a flat file. SQLite would add a
schema, a connection lifecycle, and a query-construction surface for that,
and would break the one property every other output here was chosen for: an
auditor can read it without a client. For a project whose entire thesis is an
inspectable audit trail, that is not a minor convenience.

**When that stops being true:** concurrent writers, multi-tenant isolation,
or history large enough that a linear scan hurts. See §6.

---

## 6. Production service boundaries (mapping, not fiction)

None of the following is implemented as a separate process. This section
exists because the module boundaries are already drawn where the service
boundaries would go, and a reviewer is entitled to ask "what happens when
this grows."

| Would-be service | Modules that already form it | What forces the split |
| --- | --- | --- |
| Ingestion + validation | `ingest.py`, `normalize.py`, `idempotency.py` | Streaming webhooks instead of batch CSV |
| Reconciliation engine | `match.py`, `score.py`, `assign.py`, `verify.py` | CPU isolation; independent scaling from I/O |
| AI reasoning | `ai_reasoner.py` | Network egress isolation, per-tenant key handling, rate-limit blast radius |
| Audit + replay | `report.py`, `history_store.py`, `digest.py` | Retention/compliance policy differing from operational data |
| API + dashboard | `server.py`, `web/` | Independent uptime from batch processing |

The storage swap that goes with it: `history.jsonl` → PostgreSQL, source
feeds → object storage, with `history_store.py`'s existing four-function
interface (`record_run`, `list_runs`, `get_metric_series`,
`check_cross_run_drift`) as the port. That interface was kept narrow
specifically so this substitution is a rewrite of one module, not a
migration.

**Assumption, stated as one:** that a merchant's daily batch stays in the
10²–10⁵ record range. Measured throughput is ~1,200–1,600 records/sec on a
laptop, and the 20,000-record stress test (`scripts/stress_test.py`) holds
100% precision with zero invariant violations. Beyond ~10⁶ records per batch,
the in-memory candidate graph is the first thing that breaks, and the
date-bucketed blocking in `match.py` is the seam where partitioning goes.

---

## 7. Data model layering

Source truth is never destroyed. Each layer keeps a pointer back.

```
RAW CSV ROW
    │  ingest.py — Pydantic validation, no coercion of the money path
    ▼
SOURCE MODEL            RazorpaySettlementRecord | BankStatementRecord | MerchantLedgerRecord
    │  normalize.py — carries raw_record + provenance forward, unchanged
    ▼
NormalizedRecord        integer paise, canonical dates, source lineage in .provenance
    │  match.py
    ▼
CANDIDATE EDGE          (record_a, record_b)
    │  score.py
    ▼
SCORED EDGE             (record_a, record_b, confidence)
    │  assign.py + verify.py
    ▼
ASSIGNMENT              label ∈ {AUTO_MATCH, AI_RESOLVED_MATCH, LIKELY_MATCH, EXCEPTION}
    │  exceptions.py / report.py
    ▼
EXCEPTION REPORT        category, severity, root cause, ₹ exposure, evidence dict
    │  history_store.py
    ▼
RUN HISTORY ROW         run_id ties summary.json to its history.jsonl row
```

`NormalizedRecord.raw_record` holds the full original row and
`.provenance` holds source + original id, so any decision can be traced back
to the bytes it came from without re-reading the source file.

---

## 8. The decision contract

Every row in `assignments.csv` carries: both record ids, both sources, both
amounts, both UTRs, both order ids, the confidence, and the label. Every
exception in `exceptions.json` carries: record id, source, category,
severity, root cause in plain language, unexplained ₹ exposure, suggested
remediation, and an evidence dict. Every AI attempt in `ai_resolutions.json`
carries: candidates shown, hypothesis, confidence, rationale, and outcome —
including `rejected_invariant_failure` with the specific invariant that
rejected it.

The four labels are a constrained set, not free text:

- **`AUTO_MATCH`** — cleared confidence scoring *and* every deterministic
  invariant. Booked automatically.
- **`AI_RESOLVED_MATCH`** — an LLM proposed it and it then cleared the same
  invariant gate. Kept distinct so the audit trail never hides LLM
  involvement.
- **`LIKELY_MATCH`** — plausible, not proven. This is the *abstention*: above
  the exception floor, below the auto-match ceiling. Counted as "verified
  pending receivables" in the forward cash position, never booked.
- **`EXCEPTION`** — cannot be resolved safely. Diagnosed, costed, queued for
  a human.

---

## 9. How correctness is measured

Ground truth is generated first and the three imperfect source views are
rendered from it second (`datagen/generator.py`), so the truth is real rather
than reverse-engineered. It is evaluation-only and never reaches matching code.

The metric suite (`engine/evaluate.py`, `engine/calibration.py`) deliberately
avoids a single accuracy number:

- **Tier 1 — financial correctness:** precision, recall, F1, false match rate
- **Tier 1 headline pair:** Safe Auto-Resolution Rate + **False Auto-Book
  Rate** (both denominated over *all* records, which is what makes the pair
  Goodhart-resistant — matching aggressively raises the second, abstaining on
  everything starves the first)
- **Tier 2 — agent safety:** **Dangerous Miss Rate** (auto-matched a record
  that genuinely had no counterpart), Exception Recall, Abstention Precision
- **Tier 3 — confidence quality:** Expected Calibration Error, Brier score,
  reliability bins
- **Tier 4 — invariants:** violation count, duplicate interception count
- **Tier 5 — operational:** records/sec, elapsed, throughput at scale

Baselines (`engine/baseline.py`) exist so the tradeoff is visible rather than
asserted: exact-ID match, amount+date-window match, and fuzzy heuristic
match. A baseline reconciling *more* records while corrupting the ledger is
the outcome this project exists to argue against, so it is measured, not
hand-waved.

Adversarial coverage lives in `datagen/adversarial.py` and
`scripts/noise_sweep.py`: twin candidates with no reference, near-tie scores,
conflicting sources with no fee evidence, tolerance boundaries, identity
drift, injection payloads, and a progressive-noise sweep that reports the
level at which precision first degrades.

---

## 10. Failure behaviour, in one place

| Failure | System response |
| --- | --- |
| LLM unavailable / timeout / rate-limited | Record stays `EXCEPTION`. Deterministic path unaffected. |
| LLM returns malformed JSON or out-of-range confidence | Treated as unavailable. Fail closed. |
| LLM names a candidate it was never shown | Rejected, logged as `rejected_unknown_candidate`. |
| LLM hypothesis fails an invariant | Rejected, logged as `rejected_invariant_failure` with the violation. |
| Injected instructions in a description field | Data. Never interpreted. Deterministic scorer cannot execute text. |
| Duplicate webhook / re-imported batch file | Intercepted by fingerprint before candidate generation; logged. |
| A high-confidence match that breaks an invariant | Demoted `AUTO_MATCH` → `EXCEPTION`, violation named. |
| Two candidates competing for one record | Per-leg exclusivity: at most one wins; the loser surfaces as an exception. |
| A record with no plausible counterpart at all | `MISSING_COUNTERPART` exception with ₹ exposure quantified. |
| Ground truth says no counterpart exists | Correctly abstaining is scored as Exception Recall, not as a miss. |

---

## 11. Deployment

Single container. `Dockerfile` builds a non-root image, pre-generates a
1,000-record batch and runs one reconciliation at build time (so the image
ships with a populated dashboard), exposes `:8080`, and declares a
`HEALTHCHECK` against `/healthz`. `docker-compose.yml` mounts named volumes
for `data/generated` and `results` and sets thresholds via
`SETTLEGRAPH_*` environment variables — the same `PipelineConfig` fields the
CLI flags set, so a merchant's risk tolerance is configuration, not a code
change.

CI (`.github/workflows/ci.yml`) runs lint + format, the full test suite on a
3-OS × 3-Python matrix, a real 1,000-record generate → run → benchmark →
failure-injection cycle, an end-to-end probe, and a Docker build with a live
`/healthz` check. A second workflow
(`.github/workflows/evaluation.yml`) gates on the safety metrics themselves:
precision, false-auto-book rate, dangerous-miss rate, exception recall,
invariant violations, the adversarial corpus, split-leakage checks, the
noise-degradation curve, and the 20,000-record stress test.

**Verification status, stated plainly:** everything in §9 was measured by
running it in the development environment. The container itself was **not**
built or run there — Docker is not installed on that machine — so the
Dockerfile and compose file are exercised only by CI's `docker-build` job,
not by a local run. Treat the deployment section as reviewed configuration
plus a CI-verified build, not as something demonstrated end-to-end by hand.
