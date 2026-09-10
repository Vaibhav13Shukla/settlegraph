"""Tests for the operator review lifecycle: state machine, persistence,
optimistic concurrency, audit trail, queue, and the HTTP endpoints."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from settlegraph.engine.review import (
    CLOSED_STATUSES,
    ConcurrencyConflict,
    IllegalTransition,
    ReviewAction,
    ReviewStatus,
    ReviewStore,
    build_review_queue,
    transition,
)
from settlegraph.server import SettleGraphAPIHandler

# --------------------------------------------------------------------------- #
# Pure state machine
# --------------------------------------------------------------------------- #


def test_legal_transitions_from_open() -> None:
    assert transition(ReviewStatus.OPEN, ReviewAction.APPROVE) is ReviewStatus.APPROVED
    assert transition(ReviewStatus.OPEN, ReviewAction.REJECT) is ReviewStatus.REJECTED
    assert transition(ReviewStatus.OPEN, ReviewAction.RESOLVE) is ReviewStatus.RESOLVED
    # Reclassify keeps the case open for a later accept/reject.
    assert transition(ReviewStatus.OPEN, ReviewAction.RECLASSIFY) is ReviewStatus.OPEN


def test_closed_cases_can_only_be_reopened() -> None:
    for closed in CLOSED_STATUSES:
        assert transition(closed, ReviewAction.REOPEN) is ReviewStatus.OPEN
        for action in (ReviewAction.APPROVE, ReviewAction.REJECT, ReviewAction.RESOLVE):
            with pytest.raises(IllegalTransition):
                transition(closed, action)


def test_reopen_is_illegal_from_open() -> None:
    with pytest.raises(IllegalTransition):
        transition(ReviewStatus.OPEN, ReviewAction.REOPEN)


# --------------------------------------------------------------------------- #
# Store: persistence, versioning, audit
# --------------------------------------------------------------------------- #


def _seed() -> dict:
    return {
        "merchant_id": "merch_apollo",
        "category": "AMOUNT_MISMATCH",
        "severity": "HIGH",
        "unexplained_amount_paise": 125000,
    }


def test_apply_persists_and_audits(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review_state.json")
    case = store.apply(
        "pay_x",
        ReviewAction.APPROVE,
        actor_id="analyst_1",
        reason="verified",
        expected_version=0,
        seed=_seed(),
    )
    assert case.status is ReviewStatus.APPROVED
    assert case.version == 1
    assert case.merchant_id == "merch_apollo"

    # Persisted to disk and reloadable.
    reloaded = ReviewStore(tmp_path / "review_state.json")
    assert reloaded.status_of("pay_x") is ReviewStatus.APPROVED
    assert reloaded.version_of("pay_x") == 1

    trail = reloaded.audit_trail("pay_x")
    assert len(trail) == 1
    assert trail[0]["previous_status"] == "OPEN"
    assert trail[0]["new_status"] == "APPROVED"
    assert trail[0]["actor_id"] == "analyst_1"
    assert trail[0]["reason"] == "verified"


def test_optimistic_concurrency_rejects_stale_version(tmp_path: Path) -> None:
    """Two reviewers loaded the case at v0; the first approves (v0->v1), the
    second's reject still cites v0 and must be refused, not silently applied."""
    store = ReviewStore(tmp_path / "review_state.json")
    store.apply(
        "pay_x", ReviewAction.APPROVE, actor_id="a", reason="", expected_version=0, seed=_seed()
    )

    with pytest.raises(ConcurrencyConflict) as excinfo:
        store.apply("pay_x", ReviewAction.REOPEN, actor_id="b", reason="", expected_version=0)
    assert excinfo.value.actual == 1
    # The losing write left no trace beyond the first decision.
    assert len(store.audit_trail("pay_x")) == 1


def test_reopen_then_resolve_round_trip(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review_state.json")
    store.apply(
        "pay_x", ReviewAction.REJECT, actor_id="a", reason="", expected_version=0, seed=_seed()
    )
    store.apply("pay_x", ReviewAction.REOPEN, actor_id="a", reason="mistake", expected_version=1)
    case = store.apply(
        "pay_x", ReviewAction.RESOLVE, actor_id="a", reason="fixed", expected_version=2
    )
    assert case.status is ReviewStatus.RESOLVED
    assert case.version == 3
    assert len(store.audit_trail("pay_x")) == 3


def test_illegal_transition_does_not_mutate_or_persist(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review_state.json")
    store.apply(
        "pay_x", ReviewAction.APPROVE, actor_id="a", reason="", expected_version=0, seed=_seed()
    )
    with pytest.raises(IllegalTransition):
        store.apply("pay_x", ReviewAction.APPROVE, actor_id="a", reason="", expected_version=1)
    # Still APPROVED at v1, one audit event.
    assert store.status_of("pay_x") is ReviewStatus.APPROVED
    assert store.version_of("pay_x") == 1
    assert len(store.audit_trail("pay_x")) == 1


def test_reclassify_changes_category_but_keeps_open(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review_state.json")
    case = store.apply(
        "pay_x",
        ReviewAction.RECLASSIFY,
        actor_id="a",
        reason="really a timing diff",
        expected_version=0,
        seed=_seed(),
        new_category="TIMING_DIFFERENCE",
    )
    assert case.status is ReviewStatus.OPEN
    assert case.category == "TIMING_DIFFERENCE"
    assert case.version == 1


# --------------------------------------------------------------------------- #
# Queue
# --------------------------------------------------------------------------- #


def _exceptions() -> list[dict]:
    return [
        {
            "record_id": "pay_a",
            "merchant_id": "merch_apollo",
            "severity": "HIGH",
            "unexplained_amount_paise": 500000,
            "category": "AMOUNT_MISMATCH",
        },
        {
            "record_id": "pay_b",
            "merchant_id": "merch_apollo",
            "severity": "LOW",
            "unexplained_amount_paise": 100,
            "category": "TIMING_DIFFERENCE",
        },
        {
            "record_id": "pay_c",
            "merchant_id": "merch_zomato",
            "severity": "HIGH",
            "unexplained_amount_paise": 900000,
            "category": "MISSING_COUNTERPART",
        },
    ]


def test_queue_ranks_by_severity_then_amount_and_defaults_open(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review_state.json")
    queue = build_review_queue(_exceptions(), store)
    # Two HIGH first (by amount desc: pay_c 900k, pay_a 500k), then LOW.
    assert [r["case_id"] for r in queue] == ["pay_c", "pay_a", "pay_b"]
    assert all(r["status"] == "OPEN" and r["version"] == 0 for r in queue)


def test_resolved_case_leaves_the_open_queue_and_returns_with_include_closed(
    tmp_path: Path,
) -> None:
    store = ReviewStore(tmp_path / "review_state.json")
    store.apply(
        "pay_a", ReviewAction.RESOLVE, actor_id="a", reason="", expected_version=0, seed=_seed()
    )

    open_queue = build_review_queue(_exceptions(), store)
    assert "pay_a" not in {r["case_id"] for r in open_queue}

    full = build_review_queue(_exceptions(), store, include_closed=True)
    pay_a = next(r for r in full if r["case_id"] == "pay_a")
    assert pay_a["status"] == "RESOLVED"
    assert pay_a["is_closed"] is True


def test_queue_filters_by_merchant_severity_and_amount(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review_state.json")
    assert {
        r["case_id"] for r in build_review_queue(_exceptions(), store, merchant_id="merch_zomato")
    } == {"pay_c"}
    assert {r["case_id"] for r in build_review_queue(_exceptions(), store, severity="LOW")} == {
        "pay_b"
    }
    assert {
        r["case_id"] for r in build_review_queue(_exceptions(), store, min_amount_paise=600000)
    } == {"pay_c"}


# --------------------------------------------------------------------------- #
# HTTP endpoints
# --------------------------------------------------------------------------- #


def _write_exceptions(results_dir: Path, exceptions: list[dict]) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "exceptions.json").write_text(json.dumps(exceptions), encoding="utf-8")


def _invoke_get(path: str, results_dir: Path) -> tuple[int, object]:
    handler = SettleGraphAPIHandler.__new__(SettleGraphAPIHandler)
    handler.path = path
    handler.server = SimpleNamespace(data_dir=results_dir, results_dir=results_dir)
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.do_GET()
    status = handler.send_response.call_args[0][0]
    return status, json.loads(handler.wfile.getvalue() or b"null")


def _invoke_post(
    path: str, results_dir: Path, body: dict, client: str = "127.0.0.1"
) -> tuple[int, object]:
    handler = SettleGraphAPIHandler.__new__(SettleGraphAPIHandler)
    handler.path = path
    handler.server = SimpleNamespace(
        data_dir=results_dir, results_dir=results_dir, allow_remote_mutations=False
    )
    handler.client_address = (client, 5000)
    raw = json.dumps(body).encode("utf-8")
    handler.headers = {"Content-Length": str(len(raw))}
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.do_POST()
    status = handler.send_response.call_args[0][0]
    return status, json.loads(handler.wfile.getvalue() or b"null")


def test_endpoint_approve_persists_and_updates_queue(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write_exceptions(results, _exceptions())

    status, body = _invoke_post(
        "/api/exceptions/pay_a/approve",
        results,
        {"actor_id": "analyst_1", "reason": "checked", "expected_version": 0},
    )
    assert status == 200
    assert body["case"]["status"] == "APPROVED"

    # Case has left the open queue.
    status, q = _invoke_get("/api/review-queue", results)
    assert status == 200
    assert "pay_a" not in {r["case_id"] for r in q["queue"]}

    # And it shows in the audit trail.
    status, audit = _invoke_get("/api/audit?case_id=pay_a", results)
    assert status == 200
    assert audit["audit"][0]["new_status"] == "APPROVED"


def test_endpoint_stale_version_returns_409(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write_exceptions(results, _exceptions())
    _invoke_post(
        "/api/exceptions/pay_a/approve",
        results,
        {"actor_id": "a", "reason": "", "expected_version": 0},
    )

    status, body = _invoke_post(
        "/api/exceptions/pay_a/reopen",
        results,
        {"actor_id": "b", "reason": "", "expected_version": 0},
    )
    assert status == 409
    assert body["current_version"] == 1


def test_endpoint_unknown_case_returns_404(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write_exceptions(results, _exceptions())
    status, _ = _invoke_post(
        "/api/exceptions/pay_missing/approve",
        results,
        {"actor_id": "a", "reason": "", "expected_version": 0},
    )
    assert status == 404


def test_endpoint_requires_actor_and_version(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write_exceptions(results, _exceptions())
    status, _ = _invoke_post(
        "/api/exceptions/pay_a/approve", results, {"reason": "x", "expected_version": 0}
    )
    assert status == 400
    status, _ = _invoke_post(
        "/api/exceptions/pay_a/approve", results, {"actor_id": "a", "reason": "x"}
    )
    assert status == 400


def test_endpoint_review_actions_are_loopback_gated(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write_exceptions(results, _exceptions())
    status, _ = _invoke_post(
        "/api/exceptions/pay_a/approve",
        results,
        {"actor_id": "a", "reason": "", "expected_version": 0},
        client="203.0.113.9",
    )
    assert status == 403
