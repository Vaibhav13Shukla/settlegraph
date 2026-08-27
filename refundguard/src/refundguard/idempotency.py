"""Replay and duplicate-submission protection.

An agent that times out mid-call and retries is the ordinary case, not the
adversarial one, and it is the single most likely way a well-behaved agent
refunds the same payment twice. Razorpay treats the receipt field as an
idempotency key, so RefundGuard keys on the same value and refuses any
re-presentation of it.

Key reuse carrying a different payload is treated as a replay too, and more
seriously: it means the caller either lost track of its own keys or is probing
for a way through.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayCheck:
    is_replay: bool
    fingerprint_matches: bool | None = None
    first_seen_seq: int | None = None


def fingerprint(payment_id: str, amount_paise: int | None, speed: str) -> str:
    raw = "|".join([payment_id, str(amount_paise), speed])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class IdempotencyLedger:
    def __init__(self) -> None:
        self._seen: dict[str, tuple[str, int]] = {}

    def __len__(self) -> int:
        return len(self._seen)

    def check(self, key: str, payment_id: str, amount_paise: int | None, speed: str) -> ReplayCheck:
        prior = self._seen.get(key)
        if prior is None:
            return ReplayCheck(is_replay=False)
        prior_fp, prior_seq = prior
        current_fp = fingerprint(payment_id, amount_paise, speed)
        return ReplayCheck(
            is_replay=True,
            fingerprint_matches=(prior_fp == current_fp),
            first_seen_seq=prior_seq,
        )

    def record(self, key: str, payment_id: str, amount_paise: int | None, speed: str) -> int:
        seq = len(self._seen) + 1
        self._seen[key] = (fingerprint(payment_id, amount_paise, speed), seq)
        return seq
