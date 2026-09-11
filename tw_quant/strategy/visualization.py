from __future__ import annotations

from math import isfinite
from typing import Mapping

import pandas as pd

from .parameters import STRATEGY_DEFINITIONS


SCHEMA_VERSION = 1


def _timestamp(value: object) -> str:
    return pd.Timestamp(value).isoformat(timespec="milliseconds")


def series(
    bars: pd.DataFrame,
    values: pd.Series,
    *,
    key: str,
    label: str,
    panel: str,
    series_type: str = "line",
    color: str,
    groups: pd.Series | None = None,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    points: list[dict[str, object]] = []
    for index, raw in values.items():
        if pd.isna(raw):
            continue
        value = float(raw)
        if not isfinite(value):
            continue
        point: dict[str, object] = {
            "time": _timestamp(bars.loc[index, "timestamp"]),
            "value": round(value, 8),
        }
        if groups is not None and not pd.isna(groups.loc[index]):
            point["group"] = str(groups.loc[index])
        points.append(point)
    return {
        "key": key,
        "label": label,
        "panel": panel,
        "type": series_type,
        "color": color,
        "points": points,
        "metadata": dict(metadata or {}),
    }


def threshold(
    bars: pd.DataFrame,
    value: float,
    *,
    key: str,
    label: str,
    color: str = "#64748b",
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    values = pd.Series(float("nan"), index=bars.index)
    if not bars.empty:
        values.iloc[0] = float(value)
        values.iloc[-1] = float(value)
    return series(
        bars,
        values,
        key=key,
        label=label,
        panel="strategy",
        series_type="threshold",
        color=color,
        metadata={
            "value": float(value), "line_style": "dashed", **dict(metadata or {})
        },
    )


def parameter_summary(
    strategy: str, parameters: Mapping[str, int | float]
) -> list[dict[str, object]]:
    definition = STRATEGY_DEFINITIONS[strategy]
    fields = definition["fields"]
    assert isinstance(fields, dict)
    result: list[dict[str, object]] = []
    important_names = {
        "entry_z_score", "exit_z_score", "oversold_rsi", "exit_rsi",
        "overbought_rsi", "atr_multiplier", "volume_multiplier",
        "boundary_tolerance_atr", "std_multiplier", "stop_loss_pct",
        "take_profit_pct", "entry_deviation_pct", "exit_deviation_pct",
    }
    for key, value in parameters.items():
        field = fields.get(key)
        if not isinstance(field, dict):
            continue
        unit = str(field.get("unit") or "")
        display_value: object = value
        if field.get("kind") == "percent":
            display_value = round(float(value) * 100, 4)
        result.append({
            "key": key,
            "label": str(field.get("label") or key),
            "value": value,
            "display_value": display_value,
            "unit": unit,
            "important": key in important_names,
        })
    return result


def visualization(
    strategy: str,
    name: str,
    parameters: Mapping[str, int | float],
    *,
    overlays: list[dict[str, object]] | None = None,
    diagnostics: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    diagnostic_series = diagnostics or []
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": {"key": strategy, "name": name},
        "parameters": parameter_summary(strategy, parameters),
        "panels": [
            {"key": "price", "label": "價格", "order": 0},
            {"key": "volume", "label": "成交量", "order": 1},
            {
                "key": "strategy",
                "label": "策略診斷",
                "order": 2,
                "collapsible": True,
                "default_visible": bool(diagnostic_series),
            },
        ],
        "overlays": overlays or [],
        "diagnostics": diagnostic_series,
    }


def diagnostic_context(
    frame: pd.DataFrame, named_values: Mapping[str, pd.Series]
) -> pd.DataFrame:
    result = pd.DataFrame(index=frame.index)
    for key, values in named_values.items():
        result[key] = values
    return result
