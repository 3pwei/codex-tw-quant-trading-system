# Manual Live Canary rollback and emergency procedure

1. Activate `HALT_ENTRY`; if an order is working, activate `CANCEL_WORKING`.
2. DISARM. Restart is allowed and also clears ARM, but is not a substitute for
   checking broker truth.
3. If a managed position exists, use `FLATTEN` only with Guardian healthy,
   Recovery READY and authoritative broker position truth. Every exit is
   reduce-only. After confirmed flat, keep the account LOCKED.
4. For UNKNOWN, mismatch, disconnect or failed reconciliation, do not resubmit,
   cancel an unidentified external order, or edit SQLite. Inspect the broker UI,
   preserve audit evidence, and reconcile under operator control.
5. Set `LIVE_CANARY_ENABLED=false` in both process configurations, redeploy the
   previously accepted image/SHA, and verify health reports ordering disabled.
6. Confirm broker and local flat, zero pending outbox dispatch, ARM OFF, Strategy
   Auto Live zero, and masked logs. Record incident time, release SHA and actions.

If broker truth cannot be established, stop automation and follow the broker's
human emergency procedure. Safety takes priority over restoring availability.

