# Prefer abstention over forced reconciliation

SettleGraph emits ABSTAIN when confidence or invariant checks are not strong enough, rather than forcing uncertain matches. This trades automation coverage for correctness and trust, which is the right choice for financial controls where silent false matches are costlier than explicit escalation.

