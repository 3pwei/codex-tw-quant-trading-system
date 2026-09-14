# Live Position Guardian

## Strategy Auto coordination

After an Auto fill, Guardian must report the complete broker position protected
before new exposure. Strategy Exit is an intent delivered to Guardian, never a
competing order. Pause, disarm, stop, or Strategy failure preserves protection;
Emergency Flatten has highest priority.

The Guardian is independent of Strategy Runtime and survives strategy pause,
stop or crash. It rebuilds managed positions only from reconciled broker fills and
position truth. Every Guardian order is owner/account scoped and reduce-only.

Before ARM, readiness requires Guardian enabled, zero locked/UNKNOWN positions and
protection quantity equal to the broker-confirmed open quantity. A mismatch,
missing truth, disconnect, stale quote or UNKNOWN exit locks the path; the Guardian
never guesses, synthesizes a fill or retries an ambiguous order.

Stop Loss and Take Profit use fresh platform quotes. Strategy Exit coordinates
through the same single durable exit reservation. Session End, Contract Roll and
Emergency Flatten only reduce current exposure and never open the next contract.
Exit priority may escalate, but one managed position cannot have two active exits.

`FLATTEN` means halt entry, cancel platform-owned working entries, reconcile,
reserve one reduce-only liquidation, verify broker/local flat, then remain locked
for operator review. See [Production Canary Acceptance](live-production-canary-acceptance.md)
and [Canary Rollback](live-canary-rollback.md).
