# Real Engineering Skills & Invariant Verification Rules

- **Language & Types**: Python 3.11+ strict typing with Pydantic v2. Never use raw floating-point arithmetic for currency (always integer paise).
- **Invariants**: Precision must remain 100.0% against ground truth. False positives are catastrophic financial ledger errors.
- **Testing**: TDD discipline with `pytest`. Always verify tests against `.pytest-tmp`.
- **Formatting**: Format with `ruff`. Keep ASCII characters in terminal outputs on Windows.
