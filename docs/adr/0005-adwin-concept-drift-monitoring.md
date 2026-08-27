# ADR 0005: ADWIN Concept Drift Monitoring for Upstream Distribution Shifts

## Status
Accepted

## Context
Payment gateways and acquiring banks periodically update statement export schemas, change narration formats, or alter fee schedules without notice. Static reconciliation engines fail silently when confidence scores degrade gradually across thousands of transactions.

## Decision
We implement an online ADWIN (Adaptive Windowing) drift detection mechanism based on Bifet & Gavaldà (2007) with Hoeffding bounds. The detector dynamically maintains sliding windows of edge confidence scores. When sub-window means diverge beyond the statistical Hoeffding bound with confidence \(1 - \delta\), a drift alert is emitted to the engineering and finance operations dashboard.

## Consequences
- Immediate automated detection of upstream schema/fee mutations.
- Eliminates silent reconciliation degradation before monthly audit cutoffs.
