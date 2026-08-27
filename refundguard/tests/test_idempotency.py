"""Idempotency ledger: keys are single-use, and payload drift is visible."""

from __future__ import annotations

from refundguard import IdempotencyLedger
from refundguard.idempotency import fingerprint


def test_unseen_key_is_not_a_replay():
    ledger = IdempotencyLedger()
    assert ledger.check("rcpt_1", "pay_001", 10_000, "normal").is_replay is False


def test_recorded_key_replays_with_matching_fingerprint():
    ledger = IdempotencyLedger()
    ledger.record("rcpt_1", "pay_001", 10_000, "normal")
    check = ledger.check("rcpt_1", "pay_001", 10_000, "normal")
    assert check.is_replay is True
    assert check.fingerprint_matches is True
    assert check.first_seen_seq == 1


def test_key_reuse_with_a_different_payload_is_flagged_as_mismatched():
    """Same receipt, different amount. Either the caller lost track of its own
    keys or it is probing. Both are replays, and the mismatch is recorded."""
    ledger = IdempotencyLedger()
    ledger.record("rcpt_1", "pay_001", 10_000, "normal")
    check = ledger.check("rcpt_1", "pay_001", 90_000, "normal")
    assert check.is_replay is True
    assert check.fingerprint_matches is False


def test_distinct_keys_do_not_collide():
    ledger = IdempotencyLedger()
    ledger.record("rcpt_1", "pay_001", 10_000, "normal")
    assert ledger.check("rcpt_2", "pay_001", 10_000, "normal").is_replay is False


def test_fingerprint_is_sensitive_to_every_field():
    base = fingerprint("pay_001", 10_000, "normal")
    assert base != fingerprint("pay_002", 10_000, "normal")
    assert base != fingerprint("pay_001", 10_001, "normal")
    assert base != fingerprint("pay_001", 10_000, "optimum")
    assert base == fingerprint("pay_001", 10_000, "normal")
