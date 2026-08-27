# Engineering Standards & Agent Operational Guidelines (AGENTS.md)

This repository follows the **Real Engineering Methodology** (incorporating principles from Matt Pocock's skills.sh, Andrej Karpathy's coding guidelines, Garry Tan's gstack verification gates, Superpowers TDD, and Impeccable frontend design).

---

## 1. Core Principles

### I. Think Before Coding
- Never guess or make assumptions on domain logic or financial rules.
- Maintain a **Ubiquitous Domain Language** aligned with `CONTEXT.md` and `docs/PRD.md`.
- Document non-trivial architectural decisions in `docs/adr/`.

### II. Simplicity First (YAGNI & Surgical Changes)
- Write the minimum viable code that satisfies requirements and passes invariants.
- No speculative abstractions or unnecessary dependencies.
- Zero orthogonal edits: touch only the files and functions related to the task.

### III. Test-Driven Development & Red-Green-Refactor
- Every new feature or bug fix begins with a failing test.
- Verify that tests fail for the right reason before implementing the fix.
- Test suites must run fast (< 2 seconds for unit/integration suites).

### IV. Zero-Error Invariant Verification Gates (gstack & Safe RL)
- In financial computing, **Precision = 100.0%** is a non-negotiable hard invariant.
- Abstention (`UNMATCHED` / `EXCEPTION`) is strictly superior to false positive matches.
- Double-entry accounting conservation: \(\text{Merchant Sales} - \text{MDR Fees} - \text{Taxes} = \text{Net Settlement Credits}\).

---

## 2. Verification Stop Hooks

Before marking any task complete or merging code, execute the verification gates:
1. `ruff check src tests scripts datagen` — 0 lint errors.
2. `ruff format --check src tests scripts datagen` — 100% formatted.
3. `pytest -v --basetemp .pytest-tmp` — 100% test pass rate.
4. `python scripts/run_e2e.py` — 0 invariant violations across 1,000 transactions and 5 failure containment scenarios.

---

## 3. Architecture Boundary
- **`src/settlegraph/models/`**: Strongly-typed Pydantic v2 schemas (integer paise, non-negative bounds).
- **`src/settlegraph/engine/`**: Pure functional modules (score, assign, verify, exceptions, report, drift, idempotency, simulator).
- **`src/settlegraph/server.py`**: Lightweight REST API and health probe handler.
- **`src/settlegraph/web/`**: Accessible, responsive, zero-slop Tailwind CSS web console.
