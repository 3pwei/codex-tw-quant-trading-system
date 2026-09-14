# Production Manual Live Canary acceptance

This document is the release gate for the first real-money canary. Passing CI is
necessary but does not authorize a production order. `LIVE_CANARY_ENABLED=false`
remains the default, deployment never ARM's the account, and Strategy Auto Live
has no route to the execution sink.

## Readiness checklist

Record time, operator, release SHA and masked target. Every item must be PASS:

- one allowlisted broker, owner, account, symbol and contract; quantity limit `1`;
- execution process connected, CA ready and callback registered;
- Recovery `READY` after a fresh orders/fills/positions reconciliation;
- broker identity and account identity exactly match the durable target;
- broker truth exists; no `UNKNOWN`, unknown external order, orphan fill or
  unresolved position mismatch;
- account is flat, or its full broker-confirmed quantity is managed and protected
  by a healthy Guardian;
- market state is `healthy`; callback drop/failure counters are zero;
- Guardian is enabled with zero locked/unknown managed positions;
- no active Kill Switch; HALT_ENTRY is nevertheless available before ARM;
- market-api has no execution secrets/CA and execution service has no public port;
- API returns only a masked account; owner-scoped permissions and live rate limit
  are verified; audit records contain no secrets;
- rollback owner and broker-side emergency contact/path are known.

`GET /api/live/canary` exposes `readiness.ready`, named boolean `checks`, and
`blockers`. It is a cached/durable safety summary: it never calls the broker and
never returns a full account or credential. ARM re-evaluates the same pre-flight
and fails with `live_canary_preflight_failed:<check>`.

## Controlled acceptance flow

1. Operator confirms one broker/account/contract, flat truth and all readiness
   checks PASS. Activate HALT_ENTRY first if any doubt exists.
2. ARM using the explicit phrase. Capture ARM audit and expiry; never copy secrets.
3. Submit exactly one 1-lot Manual Live Canary order with a unique idempotency key
   and exact destructive confirmation.
4. Verify order plus outbox are durable before exactly one worker claim and broker
   call. Record request-to-reserve, reserve-to-claim and broker-response latency.
5. Verify broker acceptance/fill, callback audit and periodic reconciliation. The
   broker fill—not the request or callback alone—must update the position ledger.
6. Verify Guardian protects the complete fill quantity. For a partial fill, it
   must protect only broker-confirmed quantity and expand after later truth.
7. Perform a reduce-only manual close or controlled protective close. Verify broker
   and local ledgers are flat; reconciliation has no issue.
8. DISARM. Confirm ARM OFF and retain masked evidence.

Acceptance targets are all zero: duplicate real order, UNKNOWN auto retry,
unmatched fill, unresolved mismatch, stale-market entry, cross-routing, secret
leakage, restart auto-ARM, Strategy Auto real submit and unprotected managed
quantity. Callback drops must be zero or have explicit reconciliation evidence.

## Simulated fault matrix

Use Fake Broker/CI only for ambiguous or race faults; never manufacture a real
production UNKNOWN.

| Scenario | Required result |
|---|---|
| restart flat/open/pending/Guardian active | ARM OFF; Recovery LOCKED first; reconcile before dispatch/protection |
| restart processing or after UNKNOWN | outbox blocked; no resend; operator reconciliation required |
| broker or market disconnect | entry blocked; Recovery/health degraded; reconnect then reconcile |
| callback interruption/drop | lock and recover through authoritative periodic reconciliation |
| cancel/fill race | final broker FILLED truth wins; no synthetic cancel/fill |
| UNKNOWN submit/cancel/Guardian exit | blocked outbox, visible warning, zero automatic retry |
| HALT_ENTRY | new exposure blocked; risk-reducing actions remain possible |
| CANCEL_WORKING | only platform-owned working entry orders are cancelled |
| FLATTEN | cancel entries, reconcile, one reduce-only Guardian exit, verify flat, remain LOCKED |

## Evidence and performance

Preserve sanitized API snapshots, canary/Guardian audit rows, order/outbox state,
broker-call counters, callback audit, reconciliation generation and durations, and
Guardian evaluation evidence. Health signals cover Recovery lock, position
mismatch, UNKNOWN, Guardian degradation, broker disconnect, callback loss and
reconciliation failure. Current metrics expose request-to-durable reserve,
broker-response and reconciliation latency; worker/Guardian/callback counters and
timestamps allow acceptance correlation. Exact cross-process reserve-to-claim and
callback wire latency require an external trace backend and are a known limitation.

## Production result

Do not mark this checklist complete from CI. A human must append a sanitized
production evidence reference after one controlled 1-lot run. Until then the
software acceptance is PASS/production exercise is PENDING.

