"""Feed-independent atomic and composite strategy definitions and engine."""

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
from .composite import (
    default_composite_definition,
    generate_composite_signals,
    new_composite_id,
    validate_composite_dependencies,
    validate_composite_definition,
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
    "default_composite_definition",
    "generate_composite_signals",
    "new_composite_id",
    "validate_composite_dependencies",
    "validate_composite_definition",
]
