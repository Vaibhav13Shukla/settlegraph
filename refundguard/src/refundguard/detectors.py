"""Deterministic detectors for instruction-shaped text.

These are pattern matchers, not a model. They exist because a large share of
indirect prompt injection in the wild is not subtle: it announces itself with a
fake system header, a policy claim, an authority claim, or a plain imperative
aimed at the agent rather than at a person. Catching that class cheaply leaves
the expensive judgement for the cases that actually need it.

Two properties matter more than coverage:

**They run only on untrusted spans.** A merchant's own refund policy may say
"as per company policy" as often as it likes. The same words inside a customer
email are a different object. The taint layer decides which text is which; this
module only ever sees the untrusted side, and that restriction is what keeps
the patterns from drowning in false positives.

**Every finding quotes the text that triggered it.** A finding a reviewer
cannot locate in the original message is an assertion, not evidence.

Kinds map to OWASP's Top 10 for Agentic Applications 2026, narrowed to what
can appear in a refund thread: ASI01 (agent goal hijack) covers instruction,
impersonation and policy assertion; ASI06 (excessive agency) covers the
authority claims used to talk an agent past its mandate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class FindingKind(str, Enum):
    SYSTEM_IMPERSONATION = "system_impersonation"
    INSTRUCTION_TO_AGENT = "instruction_to_agent"
    AUTHORITY_CLAIM = "authority_claim"
    POLICY_ASSERTION = "policy_assertion"
    HIDDEN_TEXT = "hidden_text"
    ENCODED_BLOB = "encoded_blob"


@dataclass(frozen=True)
class Finding:
    kind: FindingKind
    excerpt: str
    weight: int

    def to_dict(self) -> dict:
        return {"kind": self.kind.value, "excerpt": self.excerpt, "weight": self.weight}


# (kind, weight, pattern). Weights are a deliberate ordering, not a
# probability: impersonating the system is a stronger signal than asserting a
# policy, because a real customer has no reason to do the former and every
# reason to believe the latter.
_RULES: tuple[tuple[FindingKind, int, re.Pattern[str]], ...] = (
    (
        FindingKind.SYSTEM_IMPERSONATION,
        40,
        re.compile(
            r"^\s*(?:system|admin|developer|assistant)\s*(?:note|message|prompt|instruction)?\s*[:\-]",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        FindingKind.SYSTEM_IMPERSONATION,
        40,
        re.compile(r"\[\s*(?:system|instruction|admin)\s*\]", re.IGNORECASE),
    ),
    (
        FindingKind.INSTRUCTION_TO_AGENT,
        35,
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+"
            r"(?:instructions?|prompts?|rules?|messages?)",
            re.IGNORECASE,
        ),
    ),
    (
        FindingKind.INSTRUCTION_TO_AGENT,
        30,
        re.compile(
            r"\bdo\s+not\s+(?:ask|tell|inform|notify|contact|check\s+with)\s+"
            r"(?:the\s+)?(?:customer|user|merchant|human|agent|anyone)",
            re.IGNORECASE,
        ),
    ),
    (
        FindingKind.INSTRUCTION_TO_AGENT,
        30,
        re.compile(r"\byou\s+are\s+(?:now\s+)?(?:a|an|the)\s+\w+", re.IGNORECASE),
    ),
    (
        FindingKind.AUTHORITY_CLAIM,
        25,
        re.compile(
            r"\bI(?:'m|\s+am)\s+the\s+(?:store\s+|shop\s+|account\s+)?"
            r"(?:manager|owner|admin|administrator|supervisor|ceo|director)",
            re.IGNORECASE,
        ),
    ),
    (
        FindingKind.AUTHORITY_CLAIM,
        25,
        re.compile(r"\bmanager[\s\-]?approved\b", re.IGNORECASE),
    ),
    (
        FindingKind.AUTHORITY_CLAIM,
        20,
        re.compile(
            r"\bon\s+behalf\s+of\s+(?:the\s+)?(?:management|merchant|company)", re.IGNORECASE
        ),
    ),
    (
        FindingKind.POLICY_ASSERTION,
        20,
        re.compile(
            r"\b(?:as\s+)?per\s+(?:company|our|the|[A-Z][A-Za-z]+)\s+policy\b",
            re.IGNORECASE,
        ),
    ),
    (
        FindingKind.POLICY_ASSERTION,
        20,
        re.compile(
            r"\ball\s+[\w\s]{0,40}?\s*(?:are|must\s+be)\s+"
            r"(?:auto[\s\-]?refunded|automatically\s+refunded|refunded\s+in\s+full)",
            re.IGNORECASE,
        ),
    ),
    (
        FindingKind.HIDDEN_TEXT,
        35,
        re.compile(r"[​-‏‪-‮⁠﻿]"),
    ),
    (FindingKind.HIDDEN_TEXT, 35, re.compile(r"<!--.*?-->", re.DOTALL)),
    (
        FindingKind.HIDDEN_TEXT,
        35,
        re.compile(
            r"(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|color\s*:\s*#f{3,6}\b)",
            re.IGNORECASE,
        ),
    ),
    (FindingKind.ENCODED_BLOB, 15, re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")),
)

_MAX_EXCERPT = 80


def _excerpt(text: str, match: re.Match[str]) -> str:
    """Quote what matched, trimmed, so a reviewer can find it in the original.

    Zero-width characters match as themselves and would render as nothing at
    all, so those get a visible description of their own position instead.
    """
    raw = match.group(0)
    if not raw.strip():
        start = max(0, match.start() - 12)
        return text[start : match.end() + 12].strip()[:_MAX_EXCERPT]
    return raw.strip()[:_MAX_EXCERPT]


def scan(text: str) -> tuple[Finding, ...]:
    """Return every finding in one piece of untrusted text.

    One finding per rule that fires, deduplicated by (kind, excerpt) so a
    repeated phrase does not inflate the score.
    """
    if not text:
        return ()

    seen: set[tuple[FindingKind, str]] = set()
    findings: list[Finding] = []
    for kind, weight, pattern in _RULES:
        for match in pattern.finditer(text):
            excerpt = _excerpt(text, match)
            key = (kind, excerpt.lower())
            if key in seen:
                continue
            seen.add(key)
            findings.append(Finding(kind=kind, excerpt=excerpt, weight=weight))
    return tuple(findings)


def suspicion_score(findings: tuple[Finding, ...]) -> int:
    """Sum of weights, capped at 100.

    Deliberately not a probability. It is an ordering over how much
    instruction-shaped material is present, used to decide how hard to look --
    never on its own to decide whether money moves.
    """
    return min(100, sum(f.weight for f in findings))
