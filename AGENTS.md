# Repository Instructions for Coding Agents

This file applies to the entire repository. A more specific `AGENTS.md` in a
subdirectory may add local rules for that subtree, but must not weaken the live
trading safety rules below.

## Project purpose and current status

This repository implements a TMF research and Paper Trading platform with
FastAPI, Next.js, SQLite, Shioaji market data, replay, backtesting, account risk
controls, monitoring, and AWS Lightsail deployment.

The live execution foundation includes typed broker contracts, a durable
order/outbox repository, callback audit records, Recovery Lock, orders/fills/
positions reconciliation, and a persistent Execution Worker. Production still
uses `DisabledExecutionWorker`: it must not load a CA, construct a production
Shioaji execution client, expose an HTTP live-order endpoint, or submit real
orders.

Read these sources before changing their areas:

- `README.md`: product capabilities, setup, deployment, and current limitations.
- `docs/architecture.md`: module responsibilities and dependency direction.
- `docs/order-lifecycle.md`: order states, reconciliation, and live safety rules.
- `docs/event-engine.md`: shared event semantics and deterministic processing.
- `docs/account-risk.md`: Paper account risk and Kill Switch behavior.
- `docs/deployment-acceptance-checklist.md`: production acceptance gates.
- `docs/disaster-recovery.md`: backup, restore, and rollback procedures.

## Non-negotiable safety rules

- Keep production live execution disabled unless the user explicitly authorizes
  a separately reviewed live-enablement change.
- Never turn a simulation client into a production client by changing a flag.
- Never add credentials, private keys, CA material, account identifiers, or
  production `.env` values to source control, logs, tests, fixtures, or PR text.
- Tick callback functions and broker SDK callback entry points may validate,
  normalize, and enqueue only. They must not write databases, calculate
  strategies, mutate positions, or call UI code; dedicated consumers own audit
  persistence and authoritative broker refresh.
- Persist an order and outbox record before broker submission.
- Treat `UNKNOWN` as potentially accepted by the broker. Never retry it
  automatically without authoritative reconciliation.
- Only `FillEvent` may change a position.
- Recovery Lock must block both new order reservation and outbox dispatch until
  broker/account identity, orders, fills, and positions reconcile successfully.
- On a mismatch, callback loss, queue overflow, stale market data, or ambiguous
  broker response, fail closed and preserve an audit trail. Do not guess,
  synthesize fills, repair positions, or resend orders.
- Keep Paper, Replay, simulation, and future Live accounts, permissions, ID
  spaces, ledgers, and Kill Switches isolated.

## Architecture and dependency rules

- Domain code must not depend on FastAPI, React, SQLite, Shioaji, or deployment
  infrastructure.
- Interfaces and adapters depend on application/domain ports; domain modules do
  not import adapters.
- `create_app()` is the FastAPI composition root. Routes obtain dependencies
  through `ApiDependencies` and do not construct repositories or broker SDKs.
- Application services must use narrow repository protocols. Do not restore a
  dependency on an all-purpose storage interface.
- `tw_quant/market_data/` owns provider integration and normalization.
- `tw_quant/market/` owns Tick, KBar, sessions, and timeframe aggregation.
- `tw_quant/strategy/` owns strategy analysis and parameters, not account risk or
  order submission.
- `tw_quant/events/` owns stable event contracts and deterministic event flow.
- `tw_quant/risk/` owns risk decisions, not fills or broker calls.
- `tw_quant/execution/` owns simulated execution and the Position Ledger.
- `tw_quant/broker/` owns broker contracts, lifecycle, outbox, callback audit,
  reconciliation, and the Execution Worker.
- `tw_quant/paper/` and `tw_quant/replay/` remain isolated execution contexts.
- `tw_quant/live/` owns API, WebSocket, service composition, and monitoring.
- Dashboard page components compose UI. Put reusable network, formatting,
  lifecycle, and selection behavior in focused clients, hooks, or pure policy
  modules with behavior tests.
- Preserve compatibility aliases until their callers have migrated and tests
  prove removal is safe. Do not create a third execution core.

## Change workflow

1. Inspect the current branch, worktree, nearby tests, and relevant documents.
2. Preserve unrelated user changes. Do not reset, overwrite, or reformat them.
3. Make the smallest cohesive change that satisfies the request and safety
   invariants.
4. Add or update behavior, regression, architecture, and recovery tests as
   appropriate.
5. Run focused tests first, then every relevant full validation command.
6. Update documentation when architecture, behavior, configuration, API
   contracts, operations, or known limitations change.
7. Create or update a reviewable PR when the change is complete. Do not leave a
   finished deliverable only as an unpushed local commit.
8. Do not merge or deploy unless the user explicitly requests `MTP` or otherwise
   clearly authorizes production deployment.

## Validation commands

Python supports versions 3.10 through 3.12. Dependencies are locked by
`uv.lock` and uv `0.11.33`.

```bash
python -m pip install "uv==0.11.33"
uv lock --check
uv sync --locked --extra server --extra test
uv run --locked --extra server --extra test \
  python -m unittest discover -s tests -v
```

For Dashboard changes:

```bash
cd dashboard
npm ci
npm test
npm run lint
npm run build
```

For dependency, deployment, gateway, or container changes, also validate the
same Docker Compose, image, Caddy, and gateway health paths used by
`.github/workflows/ci.yml`. If Docker is unavailable locally, say so and require
the GitHub `lightsail-images` job to pass.

Do not weaken correctness, risk, recovery, or performance thresholds merely to
make CI green. Diagnose the failure first. A short soak can be affected by
shared-runner I/O; a rerun is acceptable for confirmed environmental jitter,
but repeated failure is a blocker that requires investigation.

## Pull requests and MTP

- Target `master` with a focused branch and concise commit/PR title.
- Explain behavior, safety boundaries, tests, and any remaining limitations.
- Wait for Python 3.10, Python 3.12, Dashboard, and `lightsail-images` checks.
- Never claim a PR is ready when a required check is failing or still running.

`MTP` means **Merge To Production**, not merely merge:

1. Confirm the PR head and all required CI jobs.
2. Squash merge the reviewed PR into `master`.
3. Wait for the new `master` CI run to succeed.
4. Wait for `Deploy to AWS Lightsail` verify and deploy jobs to succeed.
5. Confirm the production revision and the public `/healthz` response is
   `200` with body `ok`.

Do not report MTP complete based only on merge status, a healthy old revision,
or an in-progress workflow. If deployment fails, identify the exact job and
step, preserve the running production version, and report the blocker.

## Definition of done

A change is complete only when implementation, tests, documentation, diff
hygiene, and PR status agree. Production work is complete only after the MTP
sequence above. Real-order capability remains incomplete until production
client isolation, CA/key lifecycle, allowlists, full account risk, protective
orders, alerts, and manual mismatch/`UNKNOWN` resolution have separate review
and acceptance evidence.
