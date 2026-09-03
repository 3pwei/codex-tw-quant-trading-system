"""Feed-independent ORB and BNF strategy definitions and engine."""

from .definitions import (
    BNFMeanReversion,
    BNFMeanReversionConfig,
    OpeningRangeBreakout,
    OpeningRangeBreakoutConfig,
)
from .engine import analyze_strategies
from .parameters import (
    SUPPORTED_STRATEGIES,
    default_strategy_parameters,
    strategy_catalog,
    validate_strategy_parameters,
)

__all__ = [
    "BNFMeanReversion",
    "BNFMeanReversionConfig",
    "OpeningRangeBreakout",
    "OpeningRangeBreakoutConfig",
    "SUPPORTED_STRATEGIES",
    "analyze_strategies",
    "default_strategy_parameters",
    "strategy_catalog",
    "validate_strategy_parameters",
]
