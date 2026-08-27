# RefundGuard

**Nothing moves money until it clears.**

A deterministic gate that sits in front of refunds requested by a merchant's AI
agents. Every attempt is resolved to `ALLOW`, `HOLD` or `BLOCK`, with a reason
code, the evidence the decision rested on, and a hash-chained audit record.

Razorpay shipped Agent Studio in March 2026 and opened its third-party agent
builder platform on 9 May. Merchant accounts now run agents that hold refund
authority and read untrusted customer text. RefundGuard is the layer that
decides whether a given refund should actually happen.

---

## Status: Day 1 of 9

The deterministic core only. **No model, no network, no Razorpay call.**

| Built | Not yet |
| --- | --- |
| Decision engine, 15 ordered invariants | MCP proxy in front of `refunds.create` |
| Hash-chained append-only audit log | Live Claude Agent SDK demo agent |
| Idempotency / replay ledger | Semantic layer (injection, authority spoofing, evidence-free refunds) |
| Agent velocity + customer cumulative counters | Batch evaluation, baselines, PR curves |
| 41 tests, 0 dependencies | Review console |

```bash
python -m pytest refundguard/tests -q
```

`41 passed in 0.29s`

---

## The decision path

```
replay  ->  payment state  ->  amount validity  ->  policy holds  ->  ALLOW
```

Blocks precede holds because a block means the action is invalid no matter who
signed off on it; a hold means a human still might. Velocity is evaluated last
so a structuring scenario reports the structuring, not the side effect of
having made several calls.

**Blocks** — payment not found, not captured, fully refunded, under active
dispute, malformed amount, declared-vs-submitted amount mismatch, amount over
refundable balance, idempotency replay.

**Holds** — outside refund window, silent `optimum` speed upgrade, over the
per-call ceiling, structuring across calls on one payment, structuring across
payments for one customer, agent velocity spike.

## Design decisions

**Refunds have no destination.** Razorpay's refund endpoint takes `amount`,
`speed`, `notes` and `receipt`; the money returns to the source instrument.
There is no destination-substitution attack on this action, so there is no
destination check. That attack belongs to payouts and payment links, which are
out of scope.

**Integer paise everywhere.** Floats are banned from the monetary path.
`is_valid_paise_amount` rejects `float`, `str`, `bool` (an `int` subclass that
would otherwise pass as 1 paisa), zero and negatives.

**Declared intent is cross-checked.** An agent states the amount in its own
structured output as well as on the wire. Requiring both turns rupee/paise
confusion into a deterministic equality check rather than a judgement call. A
payment large enough to absorb a 100x error will still fail this check when the
balance check cannot see the problem.

**Pending refunds consume balance.** Treating a pending refund as free balance
is how a retrying agent refunds a payment twice.

**Aggregation, not just per-call caps.** Three refunds of Rs 9,000 each clear a
Rs 10,000 per-call ceiling. Only a running total catches that.

**The audit log hashes its evidence, not just its verdict.** A chain that
protects the decision but leaves the supporting numbers rewritable would let
someone manufacture a justification after the fact.

**The LLM, when it arrives, may only raise suspicion.** An `ALLOW` that a
deterministic invariant refused stays refused. Nothing in this module is
permitted to become probabilistic.

## Limitations

- Merchant policy is a flat dataclass; there is no per-agent policy hierarchy.
- Ledgers are in-memory. Persistence is a Day 2 concern.
- The refund window is anchored on capture time, not delivery or order time.
- No semantic checks exist yet, so the classes of loss that motivate this
  project — injected instructions, authority spoofing, evidence-free refunds —
  are not yet detected by anything here.
