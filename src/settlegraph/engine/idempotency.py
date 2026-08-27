"""Idempotency and deduplication shield for financial ingestion."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from settlegraph.models import NormalizedRecord


class IdempotencyShield:
    """Cryptographic deduplication gate preventing duplicate settlement ingestion and double-matching.

    Financial records are fingerprinted using canonical payload attributes:
    `hash = SHA-256(source + source_record_id + amount_paise + transaction_date)`
    """

    def __init__(self) -> None:
        self._seen_fingerprints: dict[str, str] = {}  # hash -> record_id
        self._duplicate_events: list[dict[str, Any]] = []

    @staticmethod
    def compute_fingerprint(record: NormalizedRecord) -> str:
        """Compute deterministic cryptographic fingerprint for a financial record."""
        payload = {
            "source": record.source,
            "source_record_id": record.source_record_id,
            "amount_paise": record.amount_paise,
            "transaction_date": str(record.transaction_date),
            "currency": record.currency,
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def filter_duplicates(
        self, records: list[NormalizedRecord]
    ) -> tuple[list[NormalizedRecord], list[NormalizedRecord]]:
        """Filter out duplicate records while capturing audit log of intercepted events.

        Returns:
            tuple[unique_records, duplicate_records]
        """
        unique: list[NormalizedRecord] = []
        duplicates: list[NormalizedRecord] = []

        for record in records:
            fp = self.compute_fingerprint(record)
            if fp in self._seen_fingerprints:
                duplicates.append(record)
                self._duplicate_events.append(
                    {
                        "record_id": record.record_id,
                        "original_record_id": self._seen_fingerprints[fp],
                        "fingerprint": fp,
                        "source": record.source,
                        "amount_paise": record.amount_paise,
                        "reason": "Duplicate ingestion event intercepted by Idempotency Shield",
                    }
                )
            else:
                self._seen_fingerprints[fp] = record.record_id
                unique.append(record)

        return unique, duplicates

    @property
    def duplicate_count(self) -> int:
        return len(self._duplicate_events)

    @property
    def intercepted_duplicates(self) -> list[dict[str, Any]]:
        return list(self._duplicate_events)
