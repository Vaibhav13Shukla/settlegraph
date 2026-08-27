# ADR 0004: Cryptographic SHA-256 Idempotency Shield

## Status
Accepted

## Context
Payment gateways frequently emit retry webhooks, network resends, or double batch statements. Ingesting duplicate records without deduplication leads to ledger inflation, double-counting of credits, and corrupted financial audit reports.

## Decision
We implement a streaming in-memory and persistent SHA-256 fingerprinting Idempotency Shield. Every ingested raw and normalized record is hashed across source, entity ID, amount, and timestamp. Replays are isolated into a quarantined stream and logged before reaching the candidate matcher.

## Consequences
- Guarantees zero duplicate ledger entries under network retry storms.
- Ensures O(1) deduplication overhead during batch and stream ingestion.
