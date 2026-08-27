"""Monetary primitives.

Every amount in RefundGuard is an integer count of paise. Floats are never
permitted anywhere in the monetary path: ``0.1 + 0.2 != 0.3`` is not an
acceptable property for a system that decides whether money leaves an account.

Razorpay's Refund API takes ``amount`` in the smallest currency unit, so
Rs 500.00 is transmitted as ``50000``. The most common integration defect is
passing ``500`` (Rs 5.00) or ``5000000`` (Rs 50,000.00) instead. Those two
mistakes are worth two orders of magnitude in either direction, so unit
handling gets its own module and its own tests.
"""

from __future__ import annotations

MINOR_UNITS_PER_RUPEE = 100


def rupees_to_paise(rupees: int) -> int:
    """Convert whole rupees to paise. Test-fixture convenience only."""
    if isinstance(rupees, bool) or not isinstance(rupees, int):
        raise TypeError(f"rupees must be an int, got {type(rupees).__name__}")
    return rupees * MINOR_UNITS_PER_RUPEE


def format_paise(paise: int) -> str:
    """Render paise as a human-readable rupee string. Display only."""
    sign = "-" if paise < 0 else ""
    magnitude = abs(paise)
    return f"{sign}{magnitude // MINOR_UNITS_PER_RUPEE}.{magnitude % MINOR_UNITS_PER_RUPEE:02d}"


def is_valid_paise_amount(value: object) -> bool:
    """A refundable amount must be a strictly positive Python ``int``.

    Rejects, deliberately:
      * ``float`` -- decimal rupees leaking into the paise path
      * ``str``   -- unparsed JSON payloads
      * ``bool``  -- ``True`` is an ``int`` subclass in Python and would
                     otherwise be silently accepted as 1 paisa
      * ``0``     -- a zero refund is a no-op; a full refund is expressed by
                     omitting the amount, not by sending zero
      * negatives -- a refund is never a debit
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, int):
        return False
    return value > 0
