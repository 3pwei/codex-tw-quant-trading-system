"""Backward-compatible facade for the split execution components.

New code should import public types from :mod:`tw_quant.execution`.  The facade
remains so existing integrations do not break while the former monolith is
retired incrementally.
"""

from .liquidator import PositionLiquidator
from .pipeline import SimulatedExecutionPipeline
from .position_ledger import PositionKey, PositionLedger, PositionState, RealizedTrade
from .risk_gates import (
    DisabledRiskGate,
    PassThroughRiskGate,
    ResearchRiskGate,
    RiskGate,
)
from .signal_router import SignalOrderRouter
from .simulated_broker import OrderRecord, SimulatedBroker

__all__ = [
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
]
