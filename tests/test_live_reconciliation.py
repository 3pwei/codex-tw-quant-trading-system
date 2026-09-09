from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from tw_quant.broker import (
    BrokerFillSnapshot,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderSnapshot,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
    BrokerReconciliationSnapshot,
    ExecutionMode,
    LiveOrderManager,
    LiveReconciliationService,
    RecoveryOrderGate,
    RecoveryStatus,
    SQLiteLiveOrderRepository,
    SQLiteRecoveryLockRepository,
)


NOW = datetime(2026, 9, 9, 14, tzinfo=timezone.utc)


def request(client_order_id: str = "client-1") -> BrokerOrderRequest:
    return BrokerOrderRequest(
        client_order_id=client_order_id,
        owner_id="owner-1",
        strategy_id="dow-channel",
        strategy_version=4,
        symbol="TMF",
        contract="TMF202609",
        side="buy",
        quantity=1,
        mode=ExecutionMode.LIVE,
    )


def clean_snapshot() -> BrokerReconciliationSnapshot:
    return BrokerReconciliationSnapshot(
        broker_name="shioaji",
        account_id="sim-1",
        captured_at=NOW,
        orders=(BrokerOrderSnapshot(
            broker_order_id="broker-1",
            status=BrokerOrderStatus.FILLED,
            filled_quantity=1,
        ),),
        fills=(BrokerFillSnapshot(
            fill_id="fill-1",
            broker_order_id="broker-1",
            contract="TMF202609",
            side="buy",
            quantity=1,
            price=20_000,
            occurred_at=NOW,
        ),),
        positions=(BrokerPositionSnapshot("TMF202609", 1),),
    )


class StaticBroker:
    broker_name = "shioaji"

    async def account_state(self):
        return {}

    async def positions(self):
        return []

    async def submit_order(self, order_request):
        raise AssertionError("reconciliation must never submit")

    async def cancel_order(self, order):
        raise AssertionError("reconciliation must never cancel")

    async def refresh_order(self, order):
        return order


class StaticSource:
    def __init__(self, snapshot: BrokerReconciliationSnapshot):
        self.snapshot = snapshot
        self.error: Exception | None = None

    async def reconciliation_snapshot(self):
        if self.error:
            raise self.error
        return self.snapshot


class LiveReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "live.sqlite3"
        self.orders = SQLiteLiveOrderRepository(self.path)
        self.recovery = SQLiteRecoveryLockRepository(self.path)
        self.manager = LiveOrderManager(self.orders, StaticBroker())

    def tearDown(self):
        self.recovery.close()
        self.orders.close()
        self.temp.cleanup()

    def save_filled_order(self) -> None:
        order_request = request()
        self.orders.reserve(order_request, occurred_at=NOW)
        self.assertIsNotNone(self.orders.claim_next())
        self.orders.finish_dispatch(BrokerOrder(
            request=order_request,
            status=BrokerOrderStatus.FILLED,
            updated_at=NOW,
            broker_order_id="broker-1",
            filled_quantity=1,
            average_fill_price=20_000,
        ))

    def service(self, source: StaticSource) -> LiveReconciliationService:
        return LiveReconciliationService(
            broker_name="shioaji",
            account_id="sim-1",
            order_store=self.orders,
            order_manager=self.manager,
            source=source,
            recovery_lock=self.recovery,
            now=lambda: NOW,
        )

    async def test_clean_three_way_reconciliation_releases_persistent_lock(self):
        self.save_filled_order()
        initial = self.recovery.state("shioaji", "sim-1")
        self.assertEqual(initial.status, RecoveryStatus.LOCKED)

        report = await self.service(StaticSource(clean_snapshot())).reconcile()
        self.assertEqual(report.state.status, RecoveryStatus.READY)
        self.assertEqual(report.issues, ())
        self.recovery.close()
        self.recovery = SQLiteRecoveryLockRepository(self.path)
        self.assertEqual(
            self.recovery.state("shioaji", "sim-1").status,
            RecoveryStatus.READY,
        )

    async def test_recovery_gate_blocks_reservation_until_ready(self):
        gated = LiveOrderManager(
            self.orders,
            StaticBroker(),
            RecoveryOrderGate(self.recovery, "shioaji", "sim-1"),
        )
        with self.assertRaisesRegex(RuntimeError, "recovery lock"):
            gated.create(request())
        attempt = self.recovery.begin("shioaji", "sim-1", updated_at=NOW)
        self.recovery.complete(
            "shioaji",
            "sim-1",
            (),
            expected_generation=attempt.generation,
            updated_at=NOW,
        )
        _order, created = gated.create(request())
        self.assertTrue(created)

    async def test_recovery_gate_blocks_dispatch_of_already_reserved_order(self):
        self.orders.reserve(request(), occurred_at=NOW)
        gate = RecoveryOrderGate(self.recovery, "shioaji", "sim-1")
        gated = LiveOrderManager(self.orders, StaticBroker(), gate)
        with self.assertRaisesRegex(RuntimeError, "recovery lock"):
            await gated.dispatch_once()
        self.assertIsNone(self.orders.get("owner-1", "client-1").broker_order_id)

    async def test_unsent_outbox_order_is_not_misclassified_as_unknown(self):
        self.orders.reserve(request(), occurred_at=NOW)
        empty = BrokerReconciliationSnapshot(
            "shioaji", "sim-1", NOW, (), (), ()
        )
        report = await self.service(StaticSource(empty)).reconcile()
        pending = self.orders.get("owner-1", "client-1")
        self.assertEqual(report.state.status, RecoveryStatus.READY)
        self.assertEqual(pending.status, BrokerOrderStatus.RISK_APPROVED)

    async def test_position_difference_keeps_lock_closed(self):
        self.save_filled_order()
        snapshot = clean_snapshot()
        snapshot = BrokerReconciliationSnapshot(
            broker_name=snapshot.broker_name,
            account_id=snapshot.account_id,
            captured_at=snapshot.captured_at,
            orders=snapshot.orders,
            fills=snapshot.fills,
            positions=(),
        )
        report = await self.service(StaticSource(snapshot)).reconcile()
        self.assertEqual(report.state.status, RecoveryStatus.LOCKED)
        self.assertIn("position_mismatch", report.state.issue_codes)

    async def test_unknown_broker_order_and_orphan_fill_keep_lock_closed(self):
        source = StaticSource(BrokerReconciliationSnapshot(
            broker_name="shioaji",
            account_id="sim-1",
            captured_at=NOW,
            orders=(BrokerOrderSnapshot(
                "broker-manual", BrokerOrderStatus.FILLED, 1
            ),),
            fills=(BrokerFillSnapshot(
                "fill-manual",
                "broker-orphan",
                "TMF202609",
                "buy",
                1,
                20_000,
                NOW,
            ),),
            positions=(BrokerPositionSnapshot("TMF202609", 1),),
        ))
        report = await self.service(source).reconcile()
        codes = {issue.code for issue in report.issues}
        self.assertIn("unknown_broker_order", codes)
        self.assertIn("orphan_broker_fill", codes)
        self.assertEqual(report.state.status, RecoveryStatus.LOCKED)

    async def test_ambiguous_local_order_without_broker_id_stays_locked(self):
        order_request = request()
        reserved, _created = self.orders.reserve(order_request, occurred_at=NOW)
        self.assertIsNotNone(self.orders.claim_next())
        self.orders.finish_dispatch(BrokerOrder(
            request=reserved.request,
            status=BrokerOrderStatus.UNKNOWN,
            updated_at=NOW,
            status_reason="submission_result_unknown",
        ))
        empty = BrokerReconciliationSnapshot(
            "shioaji", "sim-1", NOW, (), (), ()
        )
        report = await self.service(StaticSource(empty)).reconcile()
        self.assertIn("ambiguous_local_order", report.state.issue_codes)
        self.assertEqual(report.state.status, RecoveryStatus.LOCKED)

    async def test_snapshot_failure_is_reported_and_keeps_lock_closed(self):
        source = StaticSource(clean_snapshot())
        source.error = TimeoutError("broker unavailable")
        report = await self.service(source).reconcile()
        self.assertEqual(report.state.status, RecoveryStatus.LOCKED)
        self.assertEqual(report.state.issue_codes, ("reconciliation_failed",))
        self.assertEqual(report.issues[0].code, "reconciliation_failed")

    async def test_stale_reconciliation_cannot_release_newer_lock(self):
        older = self.recovery.begin("shioaji", "sim-1", updated_at=NOW)
        newer = self.recovery.begin("shioaji", "sim-1", updated_at=NOW)
        self.assertGreater(newer.generation, older.generation)
        with self.assertRaisesRegex(RuntimeError, "stale recovery"):
            self.recovery.complete(
                "shioaji",
                "sim-1",
                (),
                expected_generation=older.generation,
                updated_at=NOW,
            )
        self.assertEqual(
            self.recovery.state("shioaji", "sim-1").status,
            RecoveryStatus.RECONCILING,
        )


if __name__ == "__main__":
    unittest.main()
