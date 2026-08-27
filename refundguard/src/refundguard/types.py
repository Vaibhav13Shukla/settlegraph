"""Domain types for the refund decision path.

Scope note: RefundGuard governs exactly one money action -- creating a refund
against a captured Razorpay payment. Payouts, payment links and subscription
charges are deliberately out of scope.

Razorpay's refund endpoint accepts ``amount``, ``speed``, ``notes`` and
``receipt`` only. A refund always returns to the source instrument, so there
is no destination parameter and therefore no destination-substitution attack
on this action. Any check involving a payee address belongs to a different
money action and is not modelled here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .evidence import Evidence


class PaymentStatus(str, Enum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RefundStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class RefundSpeed(str, Enum):
    NORMAL = "normal"
    OPTIMUM = "optimum"


class Disposition(str, Enum):
    ALLOW = "ALLOW"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


class ReasonCode(str, Enum):
    # Hard blocks -- the action is invalid regardless of who asked for it.
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
    PAYMENT_NOT_CAPTURED = "PAYMENT_NOT_CAPTURED"
    PAYMENT_FULLY_REFUNDED = "PAYMENT_FULLY_REFUNDED"
    ACTIVE_DISPUTE = "ACTIVE_DISPUTE"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    AMOUNT_INTENT_MISMATCH = "AMOUNT_INTENT_MISMATCH"
    DECLARED_AMOUNT_UNPARSEABLE = "DECLARED_AMOUNT_UNPARSEABLE"
    AMOUNT_EXCEEDS_REFUNDABLE = "AMOUNT_EXCEEDS_REFUNDABLE"
    UNSUPPORTED_REFUND_SPEED = "UNSUPPORTED_REFUND_SPEED"
    MISSING_IDEMPOTENCY_KEY = "MISSING_IDEMPOTENCY_KEY"
    IDEMPOTENCY_REPLAY = "IDEMPOTENCY_REPLAY"

    # Holds -- the action may well be legitimate but needs a human to say so.
    INSTRUCTION_SHAPED_TEXT_IN_THREAD = "INSTRUCTION_SHAPED_TEXT_IN_THREAD"
    UNCORROBORATED_UNTRUSTED_AMOUNT = "UNCORROBORATED_UNTRUSTED_AMOUNT"
    REFUND_WINDOW_EXCEEDED = "REFUND_WINDOW_EXCEEDED"
    SPEED_UPGRADE_REQUIRES_APPROVAL = "SPEED_UPGRADE_REQUIRES_APPROVAL"
    PER_CALL_APPROVAL_THRESHOLD_EXCEEDED = "PER_CALL_APPROVAL_THRESHOLD_EXCEEDED"
    PAYMENT_CUMULATIVE_THRESHOLD_EXCEEDED = "PAYMENT_CUMULATIVE_THRESHOLD_EXCEEDED"
    CUSTOMER_CUMULATIVE_THRESHOLD_EXCEEDED = "CUSTOMER_CUMULATIVE_THRESHOLD_EXCEEDED"
    AGENT_VELOCITY_EXCEEDED = "AGENT_VELOCITY_EXCEEDED"

    # Judge outcomes. JUDGE_CLEARED is the only reason code in this enum
    # that turns a refusal into a payment, and it is reachable only through
    # adjudicate_hold, under an explicit merchant opt-in and a rupee ceiling.
    JUDGE_ESCALATED = "JUDGE_ESCALATED"
    JUDGE_CLEARED = "JUDGE_CLEARED"

    # Clean pass.
    ALL_CHECKS_PASSED = "ALL_CHECKS_PASSED"


CONSUMING_REFUND_STATUSES = (RefundStatus.PENDING, RefundStatus.PROCESSED)


@dataclass(frozen=True)
class Refund:
    """A refund that already exists against a payment."""

    refund_id: str
    amount_paise: int
    status: RefundStatus
    created_at: datetime


@dataclass(frozen=True)
class Payment:
    payment_id: str
    customer_id: str
    amount_paise: int
    status: PaymentStatus
    created_at: datetime
    captured_at: datetime | None = None
    has_active_dispute: bool = False
    refunds: tuple[Refund, ...] = ()

    @property
    def consumed_paise(self) -> int:
        """Paise already committed to refunds.

        ``failed`` refunds are excluded -- that money never left. ``pending``
        refunds are included: treating them as free balance is how a retrying
        agent double-refunds a payment.
        """
        return sum(r.amount_paise for r in self.refunds if r.status in CONSUMING_REFUND_STATUSES)

    @property
    def refundable_paise(self) -> int:
        return self.amount_paise - self.consumed_paise

    @property
    def refund_window_anchor(self) -> datetime:
        return self.captured_at or self.created_at


@dataclass(frozen=True)
class AgentMandate:
    """The delegated authority a merchant granted to one agent."""

    agent_id: str
    can_use_optimum_refunds: bool = False


@dataclass(frozen=True)
class RefundAttempt:
    """A refund an agent is proposing. Nothing has been sent to Razorpay yet.

    ``idempotency_key`` maps to Razorpay's ``receipt`` field.

    ``amount_paise = None`` means a full refund of the remaining balance,
    matching Razorpay's behaviour when ``amount`` is omitted.

    ``declared_intent_paise`` is the amount the agent states, in its own
    structured output, that it believes it is refunding. Requiring the agent
    to declare the figure separately from the wire argument turns a whole
    class of unit-conversion defects into a deterministic equality check
    rather than a semantic judgement call.

    ``raw_speed`` preserves the string the agent actually sent. The parsed
    ``speed`` enum is a convenience; the raw value is what gets audited, so
    the log records the request that was made rather than the one the parser
    was able to make sense of.

    ``declared_amount_unparseable`` marks a declaration that was present but
    could not be read. Absent and unreadable are different situations: absent
    means the agent opted out of the cross-check, unreadable means the
    cross-check was attempted and failed, and only one of those should pass.
    """

    idempotency_key: str
    payment_id: str
    agent_id: str
    mandate: AgentMandate
    requested_at: datetime
    amount_paise: int | None = None
    speed: RefundSpeed = RefundSpeed.NORMAL
    declared_intent_paise: int | None = None
    raw_speed: str | None = None
    declared_amount_unparseable: bool = False
    # Where the agent looked, and where it got the figure. Declared by whoever
    # assembled the request, because that is the only party that knows.
    evidence: Evidence = field(default_factory=Evidence)

    @property
    def audited_speed(self) -> str:
        return self.raw_speed if self.raw_speed is not None else self.speed.value


@dataclass(frozen=True)
class RefundEvent:
    """A refund RefundGuard previously allowed. Feeds cumulative checks."""

    customer_id: str
    payment_id: str
    amount_paise: int
    at: datetime


@dataclass(frozen=True)
class Policy:
    """Merchant-configured bounds. Every value here is enforced deterministically."""

    refund_window_days: int = 30
    per_call_approval_threshold_paise: int = 1_000_000  # Rs 10,000
    payment_cumulative_threshold_paise: int = 2_000_000  # Rs 20,000
    customer_cumulative_threshold_paise: int = 5_000_000  # Rs 50,000
    customer_cumulative_window_hours: int = 24
    velocity_max_attempts: int = 5
    velocity_window_minutes: int = 10

    # Judge. Off by default: a merchant who has never heard of this feature
    # does not get a model releasing their refunds.
    judge_may_clear_holds: bool = False
    judge_clear_ceiling_paise: int = 200_000  # Rs 2,000
    judge_min_confidence: float = 0.8


@dataclass(frozen=True)
class Decision:
    """A verdict about one specific action.

    ``bound_to`` is set on ALLOW and identifies the exact attempt that was
    approved. It exists so that an approval cannot be carried to a different
    action: a decision is permission to do one thing, not a token of general
    creditworthiness.
    """

    disposition: Disposition
    reason_code: ReasonCode
    features: dict = field(default_factory=dict)
    bound_to: str | None = None

    @property
    def reaches_razorpay(self) -> bool:
        """Only ALLOW is permitted to touch the payment API."""
        return self.disposition is Disposition.ALLOW
