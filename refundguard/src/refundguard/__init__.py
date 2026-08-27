"""RefundGuard: a deterministic gate in front of agent-initiated refunds."""

from .audit_log import AuditLog, AuditRecord
from .decision_engine import EvaluationContext, RefundGuard, evaluate_refund_attempt
from .detectors import Finding, FindingKind, scan, suspicion_score
from .evidence import AmountOrigin, Evidence, EvidenceAssessment, Span, Trust, assess
from .executor import LocalRefundExecutor, RefundNotAuthorized, RefundReceipt
from .harness import AmountSource, NaiveRefundAgent, Proposal, Ticket
from .idempotency import IdempotencyLedger
from .judge import JudgeAdvice, JudgeRequest, JudgeVerdict, adjudicate_hold
from .money import format_paise, is_valid_paise_amount, rupees_to_paise
from .proxy import RefundToolProxy, ToolResult
from .types import (
    AgentMandate,
    Decision,
    Disposition,
    Payment,
    PaymentStatus,
    Policy,
    ReasonCode,
    Refund,
    RefundAttempt,
    RefundEvent,
    RefundSpeed,
    RefundStatus,
)
from .velocity import RefundHistoryLedger, VelocityLedger

__all__ = [
    "AgentMandate",
    "AmountOrigin",
    "AmountSource",
    "AuditLog",
    "AuditRecord",
    "Decision",
    "Disposition",
    "EvaluationContext",
    "Evidence",
    "EvidenceAssessment",
    "Finding",
    "FindingKind",
    "IdempotencyLedger",
    "JudgeAdvice",
    "JudgeRequest",
    "JudgeVerdict",
    "LocalRefundExecutor",
    "NaiveRefundAgent",
    "Payment",
    "PaymentStatus",
    "Policy",
    "Proposal",
    "ReasonCode",
    "Refund",
    "RefundAttempt",
    "RefundEvent",
    "RefundGuard",
    "RefundHistoryLedger",
    "RefundNotAuthorized",
    "RefundReceipt",
    "RefundSpeed",
    "RefundStatus",
    "RefundToolProxy",
    "Span",
    "Ticket",
    "ToolResult",
    "Trust",
    "VelocityLedger",
    "adjudicate_hold",
    "assess",
    "evaluate_refund_attempt",
    "format_paise",
    "is_valid_paise_amount",
    "rupees_to_paise",
    "scan",
    "suspicion_score",
]
