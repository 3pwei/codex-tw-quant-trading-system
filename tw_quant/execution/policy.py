from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalSimulationPolicy:
    """Execution rules applied while turning strategy intents into signals.

    A group is defined by the strategy engine. Intraday strategies currently use
    contract, session and trading date; daily and weekly strategies use contract.
    Keeping the limit explicit prevents an emitted signal list from accidentally
    becoming execution state.
    """

    max_entries_per_group: int = 1
    strategy_exit_reason: str = "strategy_exit"

    def __post_init__(self) -> None:
        if self.max_entries_per_group < 1:
            raise ValueError("max_entries_per_group must be positive")
        if not self.strategy_exit_reason.strip():
            raise ValueError("strategy_exit_reason is required")


DEFAULT_SIGNAL_SIMULATION_POLICY = SignalSimulationPolicy()
