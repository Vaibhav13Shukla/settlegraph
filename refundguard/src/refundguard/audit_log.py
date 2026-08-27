"""Append-only, hash-chained decision log.

Every decision -- allow, hold and block alike -- is recorded before the action
is executed or refused. The chain gives tamper-evidence: altering any historic
record invalidates every hash after it.

This is deliberately modest cryptography. The goal is an auditor being able to
prove the log was not quietly rewritten, not a distributed ledger.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    timestamp: str
    action_type: str
    agent_id: str
    payment_id: str
    amount_paise: int | None
    speed: str
    disposition: str
    reason_code: str
    features: dict
    prev_hash: str
    hash: str

    def to_dict(self) -> dict:
        return asdict(self)


def _canonical(payload: dict) -> str:
    """Deterministic serialisation; key order must not depend on insertion order."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(
    *,
    seq: int,
    timestamp: str,
    action_type: str,
    normalized_request: dict,
    disposition: str,
    reason_code: str,
    features: dict,
    prev_hash: str,
) -> str:
    """Hash covers the evidence as well as the verdict.

    Features are the numbers a reviewer relies on when they audit a decision --
    the refundable balance, the running totals, the payment age. A chain that
    protects the verdict but leaves the evidence rewritable would let someone
    manufacture a plausible justification for a decision after the fact, so the
    features go under the hash too.
    """
    payload = {
        "seq": seq,
        "timestamp": timestamp,
        "action_type": action_type,
        "normalized_request": normalized_request,
        "disposition": disposition,
        "reason_code": reason_code,
        "features": features,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)

    @property
    def head_hash(self) -> str:
        return self._records[-1].hash if self._records else GENESIS_HASH

    def append(
        self,
        *,
        action_type: str,
        agent_id: str,
        payment_id: str,
        amount_paise: int | None,
        speed: str,
        disposition: str,
        reason_code: str,
        features: dict,
        at: datetime,
    ) -> AuditRecord:
        seq = len(self._records) + 1
        timestamp = at.isoformat()
        prev_hash = self.head_hash
        normalized_request = {
            "agent_id": agent_id,
            "payment_id": payment_id,
            "amount_paise": amount_paise,
            "speed": speed,
        }
        digest = compute_hash(
            seq=seq,
            timestamp=timestamp,
            action_type=action_type,
            normalized_request=normalized_request,
            disposition=disposition,
            reason_code=reason_code,
            features=dict(features),
            prev_hash=prev_hash,
        )
        record = AuditRecord(
            seq=seq,
            timestamp=timestamp,
            action_type=action_type,
            agent_id=agent_id,
            payment_id=payment_id,
            amount_paise=amount_paise,
            speed=speed,
            disposition=disposition,
            reason_code=reason_code,
            features=dict(features),
            prev_hash=prev_hash,
            hash=digest,
        )
        self._records.append(record)
        return record

    def verify_chain(self) -> tuple[bool, int | None]:
        """Return (is_intact, first_bad_seq)."""
        expected_prev = GENESIS_HASH
        for index, record in enumerate(self._records, start=1):
            if record.seq != index or record.prev_hash != expected_prev:
                return False, record.seq
            recomputed = compute_hash(
                seq=record.seq,
                timestamp=record.timestamp,
                action_type=record.action_type,
                normalized_request={
                    "agent_id": record.agent_id,
                    "payment_id": record.payment_id,
                    "amount_paise": record.amount_paise,
                    "speed": record.speed,
                },
                disposition=record.disposition,
                reason_code=record.reason_code,
                features=record.features,
                prev_hash=record.prev_hash,
            )
            if recomputed != record.hash:
                return False, record.seq
            expected_prev = record.hash
        return True, None
