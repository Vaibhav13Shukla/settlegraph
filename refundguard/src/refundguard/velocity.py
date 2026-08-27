"""Rolling-window counters.

Per-call limits are the checks an attacker structures around: three refunds of
Rs 9,000 each slip past a Rs 10,000 cap that is only ever evaluated one call at
a time. Aggregation over a window is what closes that gap, so the counters are
first-class state rather than a property of any single request.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .types import RefundEvent


class VelocityLedger:
    """Counts refund attempts per agent, regardless of disposition."""

    def __init__(self) -> None:
        self._attempts: dict[str, list[datetime]] = {}

    def record(self, agent_id: str, at: datetime) -> None:
        self._attempts.setdefault(agent_id, []).append(at)

    def count_in_window(self, agent_id: str, now: datetime, window_minutes: int) -> int:
        cutoff = now - timedelta(minutes=window_minutes)
        return sum(1 for ts in self._attempts.get(agent_id, []) if ts > cutoff)

    def exceeds(self, agent_id: str, now: datetime, max_attempts: int, window_minutes: int) -> bool:
        return self.count_in_window(agent_id, now, window_minutes) >= max_attempts


class RefundHistoryLedger:
    """Tracks refunds RefundGuard previously allowed, for cumulative checks."""

    def __init__(self) -> None:
        self._events: list[RefundEvent] = []

    def record(self, event: RefundEvent) -> None:
        self._events.append(event)

    def customer_total_in_window(self, customer_id: str, now: datetime, window_hours: int) -> int:
        cutoff = now - timedelta(hours=window_hours)
        return sum(
            e.amount_paise for e in self._events if e.customer_id == customer_id and e.at > cutoff
        )
