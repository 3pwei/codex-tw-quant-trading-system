import ast
from pathlib import Path
import unittest

import tw_quant.execution as execution
import tw_quant.execution.event_simulator as legacy_execution


ROOT = Path(__file__).resolve().parents[1]


class ExecutionArchitectureTests(unittest.TestCase):
    def test_execution_core_does_not_depend_on_interfaces_or_adapters(self):
        core_modules = (
            "position_ledger.py",
            "risk_gates.py",
            "signal_router.py",
            "simulated_broker.py",
            "liquidator.py",
            "pipeline.py",
        )
        forbidden = (
            "fastapi",
            "sqlite3",
            "shioaji",
            "tw_quant.live",
            "tw_quant.paper",
        )
        for filename in core_modules:
            with self.subTest(module=filename):
                path = ROOT / "tw_quant" / "execution" / filename
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imported_modules = {
                    node.module or ""
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                }
                imported_modules.update(
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                )

                self.assertFalse(
                    any(module.startswith(forbidden) for module in imported_modules),
                    imported_modules,
                )
                self.assertNotIn(".event_simulator", imported_modules)

    def test_broker_domain_does_not_depend_on_storage_sdk_or_interfaces(self):
        core_modules = ("models.py", "lifecycle.py", "ports.py", "manager.py")
        forbidden = ("fastapi", "sqlite3", "shioaji", "tw_quant.live")
        for filename in core_modules:
            with self.subTest(module=filename):
                path = ROOT / "tw_quant" / "broker" / filename
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imported_modules = {
                    node.module or ""
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                }
                imported_modules.update(
                    alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                )
                self.assertFalse(
                    any(module.startswith(forbidden) for module in imported_modules),
                    imported_modules,
                )

    def test_legacy_execution_imports_keep_the_same_public_types(self):
        public_names = (
            "DisabledRiskGate",
            "OrderRecord",
            "PassThroughRiskGate",
            "PositionKey",
            "PositionLedger",
            "PositionLiquidator",
            "PositionState",
            "RealizedTrade",
            "ResearchRiskGate",
            "RiskGate",
            "SignalOrderRouter",
            "SimulatedBroker",
            "SimulatedExecutionPipeline",
        )
        for name in public_names:
            with self.subTest(name=name):
                self.assertIs(
                    getattr(legacy_execution, name),
                    getattr(execution, name),
                )


if __name__ == "__main__":
    unittest.main()
