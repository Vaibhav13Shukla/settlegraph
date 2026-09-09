"""Operator review lifecycle: a persisted, auditable decision layer.

The pipeline's batch artifacts (`assignments.csv`, `exceptions.json`) are
*evidence* and are never mutated by a human decision -- re-running the pipeline
must always reproduce them byte-for-byte (see replay consistency). A
reviewer's decisions live here instead, in a separate `review_state.json`
overlay keyed by case id, with:

- an explicit state machine (OPEN -> APPROVED / REJECTED / RESOLVED, plus
  RECLASSIFY which re-triages in place and REOPEN which un-closes), so an
  illegal transition is rejected rather than silently applied;
- an append-only audit trail recording who did what, when, why, and the
  before/after status of every transition;
- optimistic concurrency: each case carries a version, and a decision must
  cite the version it believes it is acting on, so two reviewers cannot
  silently overwrite one another -- the second gets a conflict, not a lost
  write.

This is deliberately file-backed and single-writer, matching the rest of the
system's bounded-batch design; the store is small and its shape is a clean
seam for a database-backed repository later (see docs/SCALING.md). Nothing
here can move money or change a match: it records human judgement about the
engine's output, it does not re-run the engine.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from settlegraph.engine.atomic_io import write_json


class ReviewStatus(str, Enum):
    OPEN = "OPEN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RESOLVED = "RESOLVED"


class ReviewAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    RESOLVE = "resolve"
    RECLASSIFY = "reclassify"
    REOPEN = "reopen"


# A case in one of these statuses has left the open work queue.
CLOSED_STATUSES: frozenset[ReviewStatus] = frozenset(
    {ReviewStatus.APPROVED, ReviewStatus.REJECTED, ReviewStatus.RESOLVED}
)

# Legal (status, action) -> resulting status. Anything not listed is illegal
# and is refused, so the set of reachable states is provable from this table
# rather than implied by scattered `if`s.
_TRANSITIONS: dict[ReviewStatus, dict[ReviewAction, ReviewStatus]] = {
    ReviewStatus.OPEN: {
        ReviewAction.APPROVE: ReviewStatus.APPROVED,
        ReviewAction.REJECT: ReviewStatus.REJECTED,
        ReviewAction.RESOLVE: ReviewStatus.RESOLVED,
        # Reclassify re-triages the case (its category/severity may change) but
        # keeps it open for a subsequent accept/reject decision.
        ReviewAction.RECLASSIFY: ReviewStatus.OPEN,
    },
    ReviewStatus.APPROVED: {ReviewAction.REOPEN: ReviewStatus.OPEN},
    ReviewStatus.REJECTED: {ReviewAction.REOPEN: ReviewStatus.OPEN},
    ReviewStatus.RESOLVED: {ReviewAction.REOPEN: ReviewStatus.OPEN},
}


class IllegalTransition(Exception):
    """A review action is not legal from the case's current status."""

    def __init__(self, status: ReviewStatus, action: ReviewAction) -> None:
        super().__init__(f"Cannot {action.value} a case that is {status.value}.")
        self.status = status
        self.action = action


class ConcurrencyConflict(Exception):
    """The decision cited a version other than the case's current one -- another
    reviewer (or tab) acted on it in between. The write is refused so nothing
    is silently overwritten."""

    def __init__(self, case_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Version conflict on case {case_id}: decision expected v{expected}, "
            f"current is v{actual}. Reload the case and retry."
        )
        self.case_id = case_id
        self.expected = expected
        self.actual = actual


def transition(status: ReviewStatus, action: ReviewAction) -> ReviewStatus:
    """Pure state-machine step. Raises `IllegalTransition` for any move not in
    the transition table."""
    allowed = _TRANSITIONS.get(status, {})
    if action not in allowed:
        raise IllegalTransition(status, action)
    return allowed[action]


@dataclass
class AuditEvent:
    event_id: str
    case_id: str
    merchant_id: str
    actor_id: str
    action: str
    previous_status: str
    new_status: str
    reason: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "case_id": self.case_id,
            "merchant_id": self.merchant_id,
            "actor_id": self.actor_id,
            "action": self.action,
            "previous_status": self.previous_status,
            "new_status": self.new_status,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class ReviewCase:
    case_id: str
    merchant_id: str
    category: str
    severity: str
    unexplained_amount_paise: int
    status: ReviewStatus = ReviewStatus.OPEN
    version: int = 0
    updated_at: str | None = None
    last_actor: str | None = None
    last_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "merchant_id": self.merchant_id,
            "category": self.category,
            "severity": self.severity,
            "unexplained_amount_paise": self.unexplained_amount_paise,
            "status": self.status.value,
            "version": self.version,
            "updated_at": self.updated_at,
            "last_actor": self.last_actor,
            "last_reason": self.last_reason,
            "is_closed": self.status in CLOSED_STATUSES,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ReviewStore:
    """File-backed store of review cases and their audit trail.

    A case that has never been touched does not exist in the store; it is
    implicitly `OPEN` at `version` 0. The first decision on it creates it. This
    keeps the overlay proportional to reviewer activity rather than to batch
    size, and means an untouched batch has an empty overlay.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._cases: dict[str, ReviewCase] = {}
        self._audit: list[AuditEvent] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        import json

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for c in raw.get("cases", []):
            self._cases[c["case_id"]] = ReviewCase(
                case_id=c["case_id"],
                merchant_id=c.get("merchant_id", "merch_unknown"),
                category=c.get("category", "UNKNOWN"),
                severity=c.get("severity", "MEDIUM"),
                unexplained_amount_paise=int(c.get("unexplained_amount_paise", 0)),
                status=ReviewStatus(c.get("status", "OPEN")),
                version=int(c.get("version", 0)),
                updated_at=c.get("updated_at"),
                last_actor=c.get("last_actor"),
                last_reason=c.get("last_reason"),
            )
        for e in raw.get("audit", []):
            self._audit.append(
                AuditEvent(
                    event_id=e["event_id"],
                    case_id=e["case_id"],
                    merchant_id=e.get("merchant_id", "merch_unknown"),
                    actor_id=e["actor_id"],
                    action=e["action"],
                    previous_status=e["previous_status"],
                    new_status=e["new_status"],
                    reason=e.get("reason", ""),
                    timestamp=e["timestamp"],
                    metadata=e.get("metadata", {}),
                )
            )

    def _save(self) -> None:
        write_json(
            self.path,
            {
                "cases": [c.to_dict() for c in self._cases.values()],
                "audit": [e.to_dict() for e in self._audit],
            },
        )

    def get(self, case_id: str) -> ReviewCase | None:
        return self._cases.get(case_id)

    def status_of(self, case_id: str) -> ReviewStatus:
        case = self._cases.get(case_id)
        return case.status if case else ReviewStatus.OPEN

    def version_of(self, case_id: str) -> int:
        case = self._cases.get(case_id)
        return case.version if case else 0

    def apply(
        self,
        case_id: str,
        action: ReviewAction,
        *,
        actor_id: str,
        reason: str,
        expected_version: int,
        seed: dict[str, Any] | None = None,
        new_category: str | None = None,
    ) -> ReviewCase:
        """Apply one reviewer decision, atomically and audited.

        `expected_version` must equal the case's current version (0 for an
        untouched case) or `ConcurrencyConflict` is raised. `seed` supplies the
        case metadata (merchant_id, category, severity, unexplained amount) the
        first time a case is touched, taken from its exception evidence.
        """
        existing = self._cases.get(case_id)
        current_status = existing.status if existing else ReviewStatus.OPEN
        current_version = existing.version if existing else 0

        if expected_version != current_version:
            raise ConcurrencyConflict(case_id, expected_version, current_version)

        # Raises IllegalTransition before any state is mutated or persisted.
        new_status = transition(current_status, action)

        if existing is None:
            seed = seed or {}
            existing = ReviewCase(
                case_id=case_id,
                merchant_id=seed.get("merchant_id", "merch_unknown"),
                category=seed.get("category", "UNKNOWN"),
                severity=seed.get("severity", "MEDIUM"),
                unexplained_amount_paise=int(seed.get("unexplained_amount_paise", 0)),
            )
            self._cases[case_id] = existing

        previous_status = existing.status
        if action is ReviewAction.RECLASSIFY and new_category:
            existing.category = new_category
        existing.status = new_status
        existing.version = current_version + 1
        existing.updated_at = _now()
        existing.last_actor = actor_id
        existing.last_reason = reason

        self._audit.append(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                case_id=case_id,
                merchant_id=existing.merchant_id,
                actor_id=actor_id,
                action=action.value,
                previous_status=previous_status.value,
                new_status=new_status.value,
                reason=reason,
                timestamp=existing.updated_at,
                metadata={"new_category": new_category} if new_category else {},
            )
        )
        self._save()
        return existing

    def audit_trail(self, case_id: str | None = None) -> list[dict[str, Any]]:
        events = (
            self._audit if case_id is None else [e for e in self._audit if e.case_id == case_id]
        )
        return [e.to_dict() for e in events]


def _severity_rank(severity: str) -> int:
    return {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(str(severity).upper(), 3)


def build_review_queue(
    exceptions: list[dict[str, Any]],
    store: ReviewStore,
    *,
    merchant_id: str | None = None,
    severity: str | None = None,
    min_amount_paise: int = 0,
    include_closed: bool = False,
) -> list[dict[str, Any]]:
    """Join the batch's exceptions with their review-overlay status, filter,
    and rank most-urgent-first (severity, then amount at risk).

    An exception with no stored decision shows as OPEN at version 0. Closed
    cases are excluded by default -- resolving a case removes it from the open
    queue, which is the whole point of a lifecycle.
    """
    rows: list[dict[str, Any]] = []
    for exc in exceptions:
        case_id = exc.get("record_id", "")
        case = store.get(case_id)
        status = case.status if case else ReviewStatus.OPEN
        version = case.version if case else 0
        is_closed = status in CLOSED_STATUSES

        if not include_closed and is_closed:
            continue
        exc_merchant = (case.merchant_id if case else exc.get("merchant_id")) or "merch_unknown"
        if merchant_id and exc_merchant != merchant_id:
            continue
        if severity and str(exc.get("severity", "")).upper() != severity.upper():
            continue
        amount = int(exc.get("unexplained_amount_paise", 0))
        if amount < min_amount_paise:
            continue

        rows.append(
            {
                **exc,
                "case_id": case_id,
                "merchant_id": exc_merchant,
                "status": status.value,
                "version": version,
                "is_closed": is_closed,
                "last_actor": case.last_actor if case else None,
                "last_reason": case.last_reason if case else None,
                "updated_at": case.updated_at if case else None,
            }
        )

    rows.sort(
        key=lambda r: (
            _severity_rank(r.get("severity", "")),
            -int(r.get("unexplained_amount_paise", 0)),
        )
    )
    return rows
