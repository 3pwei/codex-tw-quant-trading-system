from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalSimulationPolicy:
    """Execution rules applied while turning strategy intents into signals.

    Entry intents are edge-triggered: a non-zero direction must return to neutral
    before the same direction can open another position. This prevents a sustained
    indicator condition from becoming a new order on every bar without imposing a
    research-only trade-count cap.
    """

    strategy_exit_reason: str = "strategy_exit"

    def __post_init__(self) -> None:
        if not self.strategy_exit_reason.strip():
            raise ValueError("strategy_exit_reason is required")


DEFAULT_SIGNAL_SIMULATION_POLICY = SignalSimulationPolicy()
