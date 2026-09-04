# ADR 0006: PDF compliance checklist

`AI_Finance_Controller.pdf` is the official Track 04 buildathon brief. This
is an honest, page-by-page audit against it — not a claim, a checklist, done
after actually re-reading every slide against the code as it stands.

## The verification funnel (pages 7–9)

| Level | Brief | Status |
| --- | --- | --- |
| 1. Deterministic | Exact ID match, 100% trust | ✅ `engine/match.py` UTR/order-id/payment-id exact matching |
| 2. Rule-based | Fuzzy logic, date proximity, high trust | ✅ `engine/score.py` Fellegi-Sunter weighted scoring |
| 3. AI reasoning | Missing references, semantic matching, confidence-scored | ✅ `engine/ai_reasoner.py`, scoped to the 4 anomaly kinds deterministic matching can't touch (see DEVLOG Day 4) |
| 4. Human review | Unresolvable exceptions escalated | ✅ `EXCEPTION` label + `exceptions.json`, now with a triage table in the audit report (§ below) |
| "AI proposes, verification confirms" | Every AI hypothesis must clear the deterministic gate | ✅ `resolve_exceptions_with_ai` runs `verify_settlegraph_invariants` on every hypothesis before promoting anything |

## Metrics (page 11)

| Brief's term | Status |
| --- | --- |
| Match Rate | ✅ `evaluate()["match_rate"]` |
| Accuracy | ✅ `evaluate()["accuracy"]` |
| Exception Rate | ✅ `evaluate()["exception_rate"]` |
| Throughput | ✅ `summary["throughput"]` (records/sec, wall-clock) |
| False Match Rate | ✅ `evaluate()["false_match_rate"]` |
| "We evaluate the entire batch, not a cherry-picked transaction" | ✅ every number above is computed over the full batch against hidden ground truth, and the stress test (`scripts/stress_test.py`) re-confirms it at 20,000 records |

## Forward Cash Position (page 10)

✅ `compute_revenue_assurance()` — Bank Balance + Verified Pending
Receivables − Expected Outflows, in the audit report and `summary.json`.

## Dashboard (page 12)

| Field on the mockup | Status |
| --- | --- |
| Total Records Processed | ✅ stat tile added |
| Match Rate (donut) | ✅ stat tile added |
| Accuracy | ✅ stat tile added |
| Automatically Matched (X, Correct: Y) | ✅ stat tile added |
| Exceptions | ✅ already shown (exceptions tab) + stat tile |
| Auto-Resolved Exceptions | ✅ stat tile added, sourced from `ai_resolved_match` |
| Escalated Exceptions | ✅ stat tile added, sourced from `exception` count |
| Average Processing Time | ✅ stat tile added, sourced from `throughput` |

## Example directions (track page)

| Direction | Status |
| --- | --- |
| Multi-source reconciliation | ✅ the whole engine |
| Settlement Q&A agent | ✅ `qa_agent.py` + `settlegraph ask` — see ADR reasoning below |
| Forward cash forecaster | ✅ (single-batch forward position; a multi-period 7/30-day forecast is a natural extension, not built — see DEVLOG "Roadmap") |
| Tax-line matcher | ✅ `engine/tax_matcher.py` + `settlegraph tax-match` — GST on Razorpay's MDR fee, matched per settlement batch (see DEVLOG Day 5 for a real matching-granularity bug this found and fixed) |

## Tech stack (page 15)

| Line item | Status |
| --- | --- |
| Evaluation: automated metrics and test dataset scripts | ✅ `engine/evaluate.py`, `scripts/stress_test.py` |
| Interface: Streamlit / lightweight web dashboard | ✅ `src/settlegraph/web/index.html` + `server.py` (lightweight, not Streamlit — explicitly listed as the alternative) |
| Agent Framework: custom agent reasoning loop | ✅ `qa_agent.py`'s tool-calling loop (via Agent SDK), `ai_reasoner.py`'s propose→verify loop |
| **AI Reasoning Layer: LLM-based reasoning via Claude Agent SDK** | ⚠️ **See "Where Agent SDK belongs" below — this is the honest answer to "did you implement Claude Agent SDK."** |
| Matching Engine: deterministic rules + similarity scoring | ✅ `engine/match.py`, `engine/score.py` |
| Data & Processing Layer: synthetic CSVs, Python, Pandas | ⚠️ CSVs and Python, no Pandas — stdlib `csv`/`pydantic` throughout. Deliberate: the datasets here are small enough that Pandas would be an unused dependency, not a capability gain (matches this same page's own subtitle, "do not overcomplicate the architecture") |
| Storage Layer: SQLite / PostgreSQL | ⚠️ `engine/history_store.py` + `settlegraph history` — real cross-run ADWIN drift detection (distinct from the pipeline's own within-batch-only check), but **not SQLite**. A ponytail audit (Day 5) found every operation this module does — append a row, list recent rows, read one metric as a chronological series — is a few lines over a flat JSON Lines file, and that JSONL matches every other output this project produces (CSV/JSON, never a binary DB), which SQLite didn't. Chose the simpler, more consistent option over the literally-named one; see DEVLOG for the full reasoning. |

### Where Agent SDK belongs (the honest answer)

The brief names `claude-agent-sdk` for "the AI reasoning layer" without
specifying *which* one — this repo has two, and they made different choices
on purpose:

- **`engine/ai_reasoner.py`** — proposes match hypotheses, one step from a
  ledger entry (`AI_RESOLVED_MATCH`). Uses the plain `anthropic` package,
  structured JSON-schema output, **zero tools**. This was a deliberate
  choice, not an oversight: giving a money-adjacent component tool access
  would be a safety regression, and it directly contradicts this same PDF's
  own rule on page 7 ("every AI-generated matching hypothesis must pass the
  deterministic verification gate") and `docs/PRD.md`'s non-negotiable rule
  ("LLMs may parse text and summarize verified evidence only; they cannot
  decide matches"). It stays on the plain SDK.
- **`src/settlegraph/qa_agent.py`** (new) — the actual `claude-agent-sdk`
  package, with a genuine multi-turn, tool-calling loop, doing exactly what
  page 14 describes ("Tool usage: querying internal ledgers,"
  "Iterative investigation: chaining queries based on previous results").
  This is where Agent SDK's actual strength — an agentic loop, not a single
  structured call — has a safe home: it only ever reads files
  `run_pipeline` already wrote, never mutates anything, and there is no
  tool in its allow-list that could move a record between labels. See its
  own module docstring for the full safety design (two independent
  restrictions on tool access, tested in `tests/test_qa_agent.py`).

**Confirmed live this session**, no `ANTHROPIC_API_KEY` needed — the
`claude` CLI's own authenticated session carried the subprocess call:

```
$ settlegraph ask "Why wasn't rzp_norm_pay_000085 auto-matched, and what is the forward cash position for this batch?"

## Why rzp_norm_pay_000085 wasn't auto-matched
Category: TIMING_DIFFERENCE (severity: LOW)
Root cause: A 16-day settlement gap between the gateway record and the
bank statement — gateway date 2026-01-20 vs. bank date 2026-02-05.
...
## Forward cash position for this batch
Bank balance: ₹33,35,938.62
Verified pending receivables: ₹3,83,086.46
Expected outflows: ₹2,12,660.28
Forward cash position: ₹35,06,364.80
```

That first live run also found a real bug — the CLI crashed on Windows'
legacy console codepage the instant a real answer contained "₹" — fixed the
same session (`cli.py`, UTF-8 stream reconfiguration). Written up in full in
`DEVLOG.md`.

## Beyond the checklist: Razorpay-ecosystem extensions

Not on the PDF, added because they're a natural fit for what Track 04 is
actually about and directly named in the buildathon's own "why now" framing
(Razorpay's real product surface, not a generic reconciliation demo):

- **Route marketplace-split reconciliation** (`engine/route_reconciliation.py`)
  — verifies a marketplace payment's vendor payout legs actually sum to
  what should have been distributed. A real Razorpay product surface, and
  a genuinely different reconciliation question from settlement matching.
- **RazorpayX as a settlement rail** — the generator now renders a
  realistic share of settlements landing in a RazorpayX current account
  rather than always a generic bank, at zero cost to the matching engine
  (which was already bank-name-agnostic, confirmed by grep before
  changing anything — this is proof of existing generality, not new
  matching logic).
- **Plain-language digest** (`engine/digest.py`, `settlegraph digest`) —
  the thing a merchant would actually read, deterministic (not
  LLM-generated) so it can never drift from the numbers it summarizes.

All three, plus the tax-line matcher and run-history above, found real
bugs on first real use — written up in full in `DEVLOG.md` Day 5.
