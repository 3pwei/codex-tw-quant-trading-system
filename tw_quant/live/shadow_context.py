from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from ..broker import (
    BrokerAccountRef,
    BrokerCapabilities,
    BrokerOrderStatus,
    BrokerTruthStore,
    LiveOrderStore,
    RecoveryLockStore,
)
from ..execution.live_models import InstrumentSpec, LiveExecutionCandidate
from ..risk.live import LiveKillSwitchScope, LiveRiskContext
from .shadow_store import SQLiteShadowExecutionRepository
from .storage import TradingRuntimeRepository


class MarketStatusView(Protocol):
    def status_message(self) -> dict[str, object]: ...


class ExecutionHealthView(Protocol):
    def snapshot(self) -> dict[str, object]: ...


class LiveExecutionTargetCatalog:
    """Resolve opaque public IDs to server-known durable broker identities."""

    def __init__(
        self,
        truth: BrokerTruthStore,
        owner_target_ids: Mapping[str, frozenset[str]],
    ):
        self.truth = truth
        self.owner_target_ids = owner_target_ids

    def resolve(self, public_id: str, owner_id: str) -> BrokerAccountRef:
        allowed = self.owner_target_ids.get(owner_id, frozenset())
        matches = [
            target for target in self.truth.targets()
            if target.public_id in allowed and target.public_id == public_id
        ]
        if len(matches) != 1:
            raise KeyError("unknown live shadow execution target")
        return matches[0]

    def targets(self, owner_id: str) -> frozenset[BrokerAccountRef]:
        """Return the server-owned account set used for portfolio aggregation."""
        allowed = self.owner_target_ids.get(owner_id, frozenset())
        return frozenset(
            target for target in self.truth.targets()
            if target.public_id in allowed
        )

    def public_targets(self, owner_id: str) -> list[dict[str, str]]:
        return [
            {
                "target_id": target.public_id,
                "broker_name": target.broker_name,
                "masked_account_id": "****" + target.account_id[-4:],
            }
            for target in self.truth.targets()
            if target in self.targets(owner_id)
        ]


@dataclass(frozen=True)
class StaticInstrumentSpecCatalog:
    specs: Mapping[tuple[str, str], InstrumentSpec]

    def resolve(self, symbol: str, contract: str) -> InstrumentSpec:
        try:
            return self.specs[(symbol, contract)]
        except KeyError as exc:
            raise KeyError("instrument specification unavailable") from exc


@dataclass(frozen=True)
class StaticBrokerCapabilityView:
    values: Mapping[BrokerAccountRef, BrokerCapabilities]

    def capabilities(self, candidate: LiveExecutionCandidate) -> BrokerCapabilities:
        try:
            return self.values[candidate.target]
        except KeyError as exc:
            raise KeyError("broker capabilities unavailable") from exc


@dataclass(frozen=True)
class ConfiguredBrokerCapabilityView:
    """Composition-layer provider map; core policy has no broker-name branch."""

    providers: Mapping[str, BrokerCapabilities]

    def capabilities(self, candidate: LiveExecutionCandidate) -> BrokerCapabilities:
        try:
            return self.providers[candidate.target.broker_name]
        except KeyError as exc:
            raise KeyError("broker capabilities unavailable") from exc


class LiveShadowRiskContextProvider:
    """Join cached health with durable broker truth; never calls a broker SDK."""

    def __init__(self, *, truth: BrokerTruthStore, recovery: RecoveryLockStore,
                 orders: LiveOrderStore, runtimes: TradingRuntimeRepository,
                 market: MarketStatusView, execution_health: ExecutionHealthView,
                 shadow_store: SQLiteShadowExecutionRepository,
                 targets: LiveExecutionTargetCatalog,
                 now=None):
        self.truth = truth
        self.recovery = recovery
        self.orders = orders
        self.runtimes = runtimes
        self.market = market
        self.execution_health = execution_health
        self.shadow_store = shadow_store
        self.targets = targets
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _connected(self, target: BrokerAccountRef) -> bool:
        accounts = self.execution_health.snapshot().get("broker_accounts", [])
        return any(
            isinstance(item, Mapping)
            and item.get("target_id") == target.public_id
            and item.get("broker_connected") is True
            for item in accounts
        )

    def _owner_targets(self, owner_id: str) -> set[BrokerAccountRef]:
        return set(self.targets.targets(owner_id))

    def _daily_stats(self, candidate, snapshot, now):
        fills = [item for item in snapshot.fills if item.occurred_at.date() == now.date()]
        matched = []
        for fill in fills:
            order = self.orders.get_by_broker_order_id(candidate.target, fill.broker_order_id)
            if order is None:
                return None, None
            if order.request.owner_id == candidate.owner_id:
                matched.append(fill)
        # Broker reconciliation guarantees identity/order completeness before READY.
        # A flat no-fill day is therefore a reliable zero. Non-flat PnL accounting
        # remains unavailable until a canonical Live realized-PnL ledger exists.
        if not matched:
            return 0.0, 0
        return None, len({item.broker_order_id for item in matched})

    def context(self, candidate, account_reservation, portfolio_reservation):
        now = self.now()
        recovery = self.recovery.state(
            candidate.target.broker_name, candidate.target.account_id
        )
        snapshot = self.truth.get(candidate.target)
        account_position = None
        captured_at = None
        daily_pnl = daily_trades = None
        if snapshot is not None:
            captured_at = snapshot.captured_at
            account_position = sum(
                item.quantity for item in snapshot.positions
                if item.contract == candidate.contract
            )
            daily_pnl, daily_trades = self._daily_stats(candidate, snapshot, now)
        portfolio_position = 0
        portfolio_known = True
        owner_targets = self._owner_targets(candidate.owner_id)
        for target in owner_targets:
            owner_snapshot = self.truth.get(target)
            if owner_snapshot is None:
                portfolio_known = False
                break
            portfolio_position += sum(
                item.quantity for item in owner_snapshot.positions
                if item.contract == candidate.contract
            )
        working = [
            order for order in self.orders.orders(candidate.owner_id, target=candidate.target)
            if not order.status.terminal
            and order.status is not BrokerOrderStatus.CREATED
            and order.request.contract == candidate.contract
        ]
        working_quantity = sum(
            order.request.quantity if order.request.side == "buy" else -order.request.quantity
            for order in working
        )
        owner_working_quantity = 0
        for target in owner_targets:
            owner_working_quantity += sum(
                order.request.quantity if order.request.side == "buy" else -order.request.quantity
                for order in self.orders.orders(candidate.owner_id, target=target)
                if not order.status.terminal
                and order.status is not BrokerOrderStatus.CREATED
                and order.request.contract == candidate.contract
            )
        runtime_count = sum(
            runtime.get("mode") == "live_shadow" and runtime.get("status") != "stopped"
            for runtime in self.runtimes.trading_runtimes(candidate.owner_id)
        )
        account_scope = f"{candidate.target.broker_name}:{candidate.target.account_id}"
        kill_switches = self.shadow_store.kill_switches({
            (LiveKillSwitchScope.GLOBAL.value, "global"),
            (LiveKillSwitchScope.OWNER.value, candidate.owner_id),
            (LiveKillSwitchScope.BROKER_ACCOUNT.value, account_scope),
        })
        return LiveRiskContext(
            now=now,
            recovery_status=recovery.status.value,
            broker_connected=self._connected(candidate.target),
            broker_truth_captured_at=captured_at,
            market_status=str(self.market.status_message().get("service_status") or "market_unhealthy"),
            account_position=account_position,
            owner_portfolio_position=(portfolio_position if portfolio_known else None),
            working_order_quantity=working_quantity,
            owner_working_order_quantity=owner_working_quantity,
            pending_order_count=len(working),
            daily_realized_pnl=daily_pnl,
            daily_trade_count=daily_trades,
            active_live_runtimes=int(runtime_count),
            account_reservation_quantity=account_reservation,
            portfolio_reservation_quantity=portfolio_reservation,
            kill_switches=kill_switches,
        )
