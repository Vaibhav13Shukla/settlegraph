"""Claude-backed exception reasoner: proposes hypotheses, never decides matches.

Optional. The core package has no runtime dependencies and its whole test
suite runs without a network or a key; this module is reached only when a
merchant turns it on (``config.llm_provider == "claude"``).

    pip install -e ".[llm]"
    export ANTHROPIC_API_KEY=...

This mirrors ``refundguard/src/refundguard/judge_claude.py`` on purpose: the
shape of the problem is identical -- a language model reads unverified
material and returns an opinion against a strict schema, and deterministic
code decides what that opinion is allowed to do.

``docs/PRD.md``'s non-negotiable rule already says it plainly: *"LLMs may
parse text and summarize verified evidence only; they cannot decide matches,
perform accounting arithmetic, create records, or override policy."* The PDF
brief for this track agrees almost word for word (page 7: *"AI proposes.
Verification confirms... every AI-generated matching hypothesis must pass
the deterministic verification gate"*). Concretely, here:

**The model never decides a match.** It returns a hypothesis -- which
candidate record, if any, is the true counterpart -- and that hypothesis is
fed straight back through ``verify_settlegraph_invariants``, the exact same
deterministic gate every ``AUTO_MATCH`` already has to clear. Only if every
invariant passes does the pair get promoted, and it is promoted to
``AI_RESOLVED_MATCH``, never ``AUTO_MATCH``, so the audit trail always shows
which matches were LLM-assisted.

**Every failure path leaves the record exactly where the deterministic layer
put it.** A timeout, a rate limit, a connection error, a refusal, malformed
JSON, an out-of-range confidence, a candidate id the model was never shown,
or a hypothesis that fails an invariant -- all of them mean the record stays
``EXCEPTION``, now carrying the model's rationale as extra context for
whoever reads the queue next.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from settlegraph.config import PipelineConfig
from settlegraph.engine.exceptions import ExceptionReport
from settlegraph.engine.verify import verify_settlegraph_invariants
from settlegraph.models import NormalizedRecord

MODEL = "claude-opus-5"
MAX_TOKENS = 2_000
DEFAULT_TIMEOUT_SECONDS = 20.0

SYSTEM_PROMPT = """You investigate a settlement-reconciliation exception that \
deterministic matching could not resolve. You are an investigator, not an \
accountant: you have no tools, you cannot move money or edit records, and \
your answer is one hypothesis about which candidate record, if any, is the \
true counterpart to the unresolved record shown to you.

Decide:
- "resolve": exactly one candidate is clearly the true counterpart, given
  the evidence shown.
- "cannot_resolve": no candidate is clearly right, several are equally
  plausible, or you are not confident. This is the right answer whenever
  you are unsure -- a wrong guess here is worse than an honest "I don't
  know," because the hold stays exactly where it is either way and a wrong
  guess just wastes a reviewer's time chasing it.

Your hypothesis is not final. It is checked afterwards against the same
deterministic accounting invariants every automatic match must clear
(amount, date proximity, credit direction) before anything happens with it.
If it fails any of them, your hypothesis is discarded regardless of your
stated confidence.

Cite the exact fields you relied on. Never propose a candidate_record_id
that is not in the candidate list you were shown -- if none of them fit,
say "cannot_resolve" with candidate_record_id set to null."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string", "enum": ["resolve", "cannot_resolve"]},
        "candidate_record_id": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
        "cited_evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "hypothesis",
        "candidate_record_id",
        "confidence",
        "rationale",
        "cited_evidence",
    ],
    "additionalProperties": False,
}


class Hypothesis(str, Enum):
    RESOLVE = "resolve"
    CANNOT_RESOLVE = "cannot_resolve"


@dataclass(frozen=True)
class ReasoningRequest:
    """Everything the reasoner is shown, and nothing else -- no ground truth,
    no other merchants' records, no tools."""

    exception: ExceptionReport
    record: dict[str, Any]
    candidates: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ReasoningVerdict:
    hypothesis: Hypothesis
    candidate_record_id: str | None
    confidence: float
    rationale: str
    cited_evidence: tuple[str, ...] = ()


UNRESOLVED = ReasoningVerdict(
    hypothesis=Hypothesis.CANNOT_RESOLVE,
    candidate_record_id=None,
    confidence=0.0,
    rationale="The reasoner was unavailable, so the exception stays open.",
)


def _describe(record: NormalizedRecord) -> dict[str, Any]:
    """The subset of a record's fields relevant to a matching decision.

    Deliberately excludes raw_record and provenance -- the model reasons
    over the same normalized fields the deterministic scorer uses, not over
    internal bookkeeping.
    """
    return {
        "record_id": record.record_id,
        "source": record.source,
        "record_type": record.record_type,
        "utr": record.utr,
        "order_id": record.order_id,
        "payment_id": record.payment_id,
        "amount_paise": record.amount_paise,
        "net_amount_paise": record.net_amount_paise,
        "currency": record.currency,
        "transaction_date": str(record.transaction_date),
        "settlement_date": str(record.settlement_date) if record.settlement_date else None,
        "description": record.description,
    }


def render_prompt(request: ReasoningRequest) -> tuple[str, str]:
    """Return ``(system, user)``. Pure -- no network, no client."""
    parts = [
        "## Unresolved record",
        json.dumps(request.record, indent=2),
        "",
        "## Why deterministic matching flagged it",
        f"Category: {request.exception.category}",
        f"Root cause: {request.exception.root_cause}",
        "",
        "## Candidate counterparts (this is the complete list -- there are no others)",
    ]
    for c in request.candidates:
        parts.append(json.dumps(c, indent=2))
    parts += ["", "Return your hypothesis in the required JSON format."]
    return SYSTEM_PROMPT, "\n".join(parts)


def parse_verdict(payload: str, valid_ids: frozenset[str]) -> ReasoningVerdict:
    """Parse a model response into a verdict, failing closed on anything odd.

    ``valid_ids`` is the set of candidate_record_ids the model was actually
    shown -- a hypothesis naming any other id (a hallucinated record) is
    treated exactly like malformed JSON.
    """
    try:
        data = json.loads(payload)
        hypothesis = Hypothesis(data["hypothesis"])
        candidate_id = data.get("candidate_record_id")
        confidence = float(data["confidence"])
        if not (0.0 <= confidence <= 1.0):
            return UNRESOLVED
        if hypothesis is Hypothesis.RESOLVE:
            if candidate_id is None or candidate_id not in valid_ids:
                return UNRESOLVED
        else:
            candidate_id = None
        return ReasoningVerdict(
            hypothesis=hypothesis,
            candidate_record_id=candidate_id,
            confidence=confidence,
            rationale=str(data["rationale"]),
            cited_evidence=tuple(str(x) for x in data.get("cited_evidence", [])),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return UNRESOLVED


class ClaudeReasoner:
    """Investigator backed by the Anthropic Messages API.

    Every failure path returns ``UNRESOLVED``. An outage, a timeout, a
    malformed response, or a refusal all leave the exception exactly where
    the deterministic layer put it.
    """

    def __init__(self, client=None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on install
                raise ImportError(
                    "ClaudeReasoner needs the anthropic package: pip install -e '.[llm]'"
                ) from exc
            client = anthropic.Anthropic()
        self.client = client
        self.timeout = timeout

    def resolve(self, request: ReasoningRequest) -> ReasoningVerdict:
        if not request.candidates:
            return UNRESOLVED
        valid_ids = frozenset(c["record_id"] for c in request.candidates)
        system, user = render_prompt(request)
        try:
            response = self.client.with_options(timeout=self.timeout).messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
            )
        except Exception:
            # Fail closed. Every exception class the SDK can raise -- rate
            # limit, timeout, connection, 4xx, 5xx -- has the same correct
            # answer here, which is to leave the exception open.
            return UNRESOLVED

        if getattr(response, "stop_reason", None) == "refusal":
            return UNRESOLVED

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            return UNRESOLVED
        return parse_verdict(text, valid_ids)


def _widen_candidates(
    record: NormalizedRecord, pool: list[NormalizedRecord], max_results: int = 3
) -> list[NormalizedRecord]:
    """A loose date+amount scan, used only when the deterministic candidate
    graph produced nothing at all to reason about.

    Some of the new adversarial anomaly kinds (a reused reference number
    losing the exclusivity race, a UTR that no longer matches anything)
    leave their true record with zero candidate edges -- there is nothing
    for `investigate_exception` to have compared it against. This is not a
    scoring function, just a shortlist for the model to accept or reject,
    matching the PDF's own framing of what an AI-reasoning layer is for
    ("Tool usage: querying external datasets or internal ledgers").
    """
    record_net = record.net_amount_paise or record.amount_paise
    if record_net <= 0:
        return []
    scored: list[tuple[int, NormalizedRecord]] = []
    for other in pool:
        if other.record_id == record.record_id:
            continue
        record_date = record.settlement_date or record.transaction_date
        other_date = other.settlement_date or other.transaction_date
        date_gap = abs((other_date - record_date).days)
        if date_gap > 20:
            continue
        other_net = other.net_amount_paise or other.amount_paise
        ratio = other_net / record_net
        plausible = (
            abs(other_net - record_net) <= 200  # near-exact, within ~₹2
            or 90 <= ratio <= 110  # ~100x unit-confusion band, either direction
            or 0.009 <= ratio <= 0.011
        )
        if not plausible:
            continue
        scored.append((date_gap, other))
    scored.sort(key=lambda t: t[0])
    return [r for _, r in scored[:max_results]]


def _annotate(report: ExceptionReport, verdict: ReasoningVerdict, note: str | None = None) -> None:
    """Attach the model's opinion to the exception in place -- never the
    ledger, just the reviewer-facing evidence dict."""
    report.evidence["ai_hypothesis"] = verdict.hypothesis.value
    report.evidence["ai_confidence"] = verdict.confidence
    report.evidence["ai_rationale"] = verdict.rationale
    report.evidence["ai_cited_evidence"] = list(verdict.cited_evidence)
    if note:
        report.evidence["ai_note"] = note


def merge_ai_assignments(
    assignments: list[dict[str, Any]], ai_assignments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fold AI-resolved promotions into the deterministic assignment list.

    A promoted record already has a stale row in `assignments` -- whatever
    Phase 5 labeled it (LIKELY_MATCH or EXCEPTION) before the AI reasoner
    touched it. Simply concatenating the two lists, as an earlier version
    of this pipeline did, leaves the same record_id with two conflicting
    rows in assignments.csv: one still saying EXCEPTION, one saying
    AI_RESOLVED_MATCH. Found by code review rather than the offline test
    suite -- this path only runs with config.llm_provider set, which the
    suite never turns on. Pulled out as its own pure function specifically
    so it's directly unit-testable without needing a real (or faked)
    reasoner in the loop.
    """
    if not ai_assignments:
        return assignments
    promoted_ids = {a["source_a_id"] for a in ai_assignments}
    kept = [
        a
        for a in assignments
        if a["source_a_id"] not in promoted_ids and a["source_b_id"] not in promoted_ids
    ]
    return kept + ai_assignments


def resolve_exceptions_with_ai(
    exception_reports: list[ExceptionReport],
    scored_candidates: list[tuple[NormalizedRecord, NormalizedRecord, float]],
    norm_map: dict[str, NormalizedRecord],
    bank_pool: list[NormalizedRecord],
    config: PipelineConfig,
    reasoner: ClaudeReasoner | None = None,
) -> tuple[list[dict[str, Any]], list[ExceptionReport], list[dict[str, Any]]]:
    """Attempt AI-assisted resolution of exceptions the deterministic layer
    could not clear.

    Scoped to Razorpay-side exceptions with a Razorpay<->Bank counterpart:
    `verify_settlegraph_invariants` only knows how to check that leg, and it
    is where every currently-zero-recall anomaly kind actually lives.

    Returns ``(new_assignments, updated_exception_reports, resolution_log)``.
    ``new_assignments`` has the exact same shape as `global_assign`'s output
    (so it can simply be concatenated onto it) except ``label`` is always
    ``"AI_RESOLVED_MATCH"``. ``resolution_log`` is the full detail (model
    rationale, candidates shown, invariant outcome) for every attempt,
    successful or not -- written to its own results file rather than
    wedged into assignments.csv, so the audit trail stays complete without
    changing that file's schema.
    """
    if reasoner is None:
        reasoner = ClaudeReasoner()

    candidate_map: dict[str, list[NormalizedRecord]] = {}
    for a, b, _score in scored_candidates:
        candidate_map.setdefault(a.record_id, []).append(b)
        candidate_map.setdefault(b.record_id, []).append(a)

    new_assignments: list[dict[str, Any]] = []
    updated_reports: list[ExceptionReport] = []
    resolution_log: list[dict[str, Any]] = []

    for report in exception_reports:
        record = norm_map.get(report.record_id)
        if record is None or record.source != "razorpay":
            updated_reports.append(report)
            continue

        candidates = [c for c in candidate_map.get(record.record_id, []) if c.source == "bank"]
        if not candidates:
            candidates = _widen_candidates(record, bank_pool)
        candidates = candidates[:3]

        if not candidates:
            updated_reports.append(report)
            continue

        request = ReasoningRequest(
            exception=report,
            record=_describe(record),
            candidates=tuple(_describe(c) for c in candidates),
        )
        verdict = reasoner.resolve(request)

        log_entry: dict[str, Any] = {
            "record_id": record.record_id,
            "candidates_shown": [c.record_id for c in candidates],
            "hypothesis": verdict.hypothesis.value,
            "candidate_record_id": verdict.candidate_record_id,
            "confidence": verdict.confidence,
            "rationale": verdict.rationale,
            "outcome": "cannot_resolve",
        }

        if verdict.hypothesis is not Hypothesis.RESOLVE or verdict.candidate_record_id is None:
            _annotate(report, verdict)
            updated_reports.append(report)
            resolution_log.append(log_entry)
            continue

        chosen = next((c for c in candidates if c.record_id == verdict.candidate_record_id), None)
        if chosen is None:
            _annotate(report, UNRESOLVED, note="Model named a candidate it wasn't shown.")
            updated_reports.append(report)
            log_entry["outcome"] = "rejected_unknown_candidate"
            resolution_log.append(log_entry)
            continue

        violations = verify_settlegraph_invariants(record, chosen, config)
        if violations:
            _annotate(
                report,
                verdict,
                note=f"Hypothesis failed deterministic invariant verification: {violations[0]}",
            )
            updated_reports.append(report)
            log_entry["outcome"] = "rejected_invariant_failure"
            log_entry["invariant_failure"] = str(violations[0])
            resolution_log.append(log_entry)
            continue

        new_assignments.append(
            {
                "source_a": record.source,
                "source_a_id": record.record_id,
                "source_b": chosen.source,
                "source_b_id": chosen.record_id,
                "confidence": round(verdict.confidence, 4),
                "label": "AI_RESOLVED_MATCH",
                "a_amount_paise": record.amount_paise,
                "b_amount_paise": chosen.amount_paise,
                "a_utr": record.utr,
                "b_utr": chosen.utr,
                "a_order_id": record.order_id,
                "b_order_id": chosen.order_id,
            }
        )
        log_entry["outcome"] = "promoted"
        resolution_log.append(log_entry)

    return new_assignments, updated_reports, resolution_log
