# Manual Live Canary runbook

Manual Live Canary uses real money. Merging or deploying the code does not enable
it. The default is `LIVE_CANARY_ENABLED=false`; first enablement requires a
separate human readiness review with an operator present. This is an operational
safety procedure, not trading advice.

## Readiness review

1. Complete one day and one night read-only/shadow acceptance session.
2. Verify the exact broker/account target, canonical contract mapping and expiry.
   The broker account must be flat with zero external, working or UNKNOWN orders.
3. Verify Recovery READY, broker and CA ready, fresh BidAsk and reconciliation,
   zero callback drops, and reliable Live daily PnL/trade-count inputs.
4. Verify exactly one owner/account/symbol/contract is allowlisted, max quantity is
   one, and only `MarketableLimitIOCPolicy` is advertised.
5. Verify execution-worker has no public port; market-api has no CA or broker
   secret; Strategy Auto Live remains absent.
6. Set matching canary metadata in the two process environments. Deployment and
   restart must leave ARM OFF.

## Operator sequence

1. Re-check Recovery READY, account FLAT, no working/UNKNOWN orders and healthy
   market state.
2. Type `I_UNDERSTAND_MANUAL_LIVE_CANARY` to ARM the displayed target.
3. Verify broker, masked account, contract, max quantity, limits and policy again.
4. Type the exact order confirmation: `BUY 1 <contract> REAL ORDER` or `SELL ...`.
5. Verify durable order/outbox commit precedes exactly one broker call.
6. Verify broker response, callback audit, reconciliation, fill and Live position.
7. Cancel only a platform-owned working order and wait for broker truth.
8. Close via the server-derived reduce-only action; verify broker and Live ledger
   are flat, then DISARM.

## Outcomes and fault handling

- Accepted/filled/rejected/expired: wait for callback plus reconciliation.
- Partial fill: do not infer remaining quantity; use broker truth.
- Cancel/fill race: accept final broker state, including FILLED.
- UNKNOWN: **DO NOT RETRY**. Lock and manually reconcile. Never match by
  side/price/quantity/time heuristics.
- Restart: ARM is OFF. Pending unsubmitted work stays blocked; processing work is
  UNKNOWN and cannot be resubmitted.
- External order/position: do not import, cancel, flatten or overwrite it. Lock,
  audit and resolve outside this automated path.

Record ARM/request/order correlation, outbox commit time, broker-call counts and
latency, callback audit, reconciliation generations, fills, position, cancel/close
outcome, restart behavior and issue codes. Never record credentials or full account
IDs. Production fault injection for UNKNOWN uses Fake Broker/staging; do not create
an intentional ambiguous real order.
# Position Guardian and emergency FLATTEN

`LIVE_POSITION_GUARDIAN_ENABLED` remains `false` after ordinary deploys. Before
enabling it, verify the same single owner/account/contract Canary allowlists, actual
broker position truth, a fresh canonical BidAsk stream, and Recovery `READY`.

The displayed SL/TP is **platform-managed protection**, not a broker-native stop/OCO.
It depends on the execution worker, persisted market quotes, network, and broker
connectivity. If the broker is disconnected or position truth mismatches, Guardian
locks and does not guess or submit a flatten.

Emergency `FLATTEN` is asynchronous and reduce-only:

1. Activate account-scoped FLATTEN; new entry is halted immediately.
2. Confirm platform-owned working entry orders are cancelled through broker truth.
3. Confirm the reconciled broker position and no unknown orders.
4. Guardian durably reserves one reduce-only liquidation order.
5. Wait for callback/periodic reconciliation; do not infer flat from API success.
6. Verify broker and local position are flat.
7. Keep Recovery locked for operator review; do not re-arm automatically.

An `UNKNOWN` exit is never retried. Preserve the order/audit evidence and follow the
manual reconciliation procedure before any further live action.
