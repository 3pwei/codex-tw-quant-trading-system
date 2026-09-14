# Broker Secret Management

`ExecutionTarget` stores ownership and routing metadata only. The isolated execution service uses
the server-controlled `LIVE_BROKER_SECRET_ROOT` to resolve one exact target:

```text
ExecutionTarget
  -> file:broker-secrets/<target_id>
  -> /run/live-secrets/brokers/<target_id>/
  -> BrokerSecretMaterial
  -> Broker Runtime / Registry / Account Worker
```

The first production rollout permits at most one active target. More than one active target,
duplicate `BrokerAccountRef`, a disabled/locked target, or a target/connection mismatch fails
before broker client construction. Recovery, reconciliation and callbacks remain keyed by the
exact `broker_name + account_id`.

## Host layout

```text
/opt/tw-quant/secrets/brokers/<target_id>/
├── credentials.env
└── shioaji-ca.pfx
```

`credentials.env` contains exactly `SJ_API_KEY`, `SJ_SECRET_KEY`, and `SJ_CA_PASSWORD`, one per
line. Create directories as `0700` and both files as `0600`. The secret root, target directory and
files must not be symlinks. Never put account IDs, credentials, passwords or CA content in Git,
APIs, health documents, logs, screenshots, tests or PR descriptions.

## Resolution rules

- Only `file:broker-secrets/<opaque-target-id>` is accepted for file storage.
- The ref target ID must equal the selected `ExecutionTarget.target_id`.
- Paths are joined below the configured root and checked after canonical resolution.
- Missing, malformed, non-regular, symlinked or group/other-readable sensitive files fail closed.
- A file-backed target never falls back to `environment:primary`.
- `environment:primary` remains an explicit legacy backend only.

## Provision and rotate

1. Keep `LIVE_CANARY_ENABLED=false`, `LIVE_AUTO_ENABLED=false` and
   `LIVE_AUTO_PRODUCTION_ACCEPTANCE_PASSED=false`.
2. Create the target directory and files with restrictive permissions.
3. Persist the exact `secret_ref` as `file:broker-secrets/<target_id>`.
4. Restart execution service. Restart creates a Recovery Lock generation and never ARM's.
5. Confirm health remains locked until exact login identity and reconciliation succeed.
6. Rotate by atomically replacing files with the same ownership/mode, then restart and reconcile.

This does not authorize real orders. Canary and Strategy Auto retain independent feature flags,
production acceptance, Recovery, Risk, Kill Switch and time-limited ARM gates.
