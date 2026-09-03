"""Backward-compatible strategy imports.

The strategy engine is feed-independent and now lives in :mod:`tw_quant.strategy`.
"""

from ..strategy import SUPPORTED_STRATEGIES, analyze_strategies

analyze_live_strategies = analyze_strategies

__all__ = [
    "SUPPORTED_STRATEGIES",
    "analyze_live_strategies",
    "analyze_strategies",
]
