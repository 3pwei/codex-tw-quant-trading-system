from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Mapping

from ..risk import RiskConfig
from .definitions import BNFMeanReversionConfig


SUPPORTED_STRATEGIES = ("orb", "bnf")


STRATEGY_DEFINITIONS: dict[str, dict[str, object]] = {
    "orb": {
        "key": "orb",
        "name": "ORB 開盤區間突破",
        "description": "依交易時段開盤區間與量能確認突破方向。",
        "color": "#38bdf8",
        "fields": {
            "opening_range_minutes": {
                "label": "開盤區間",
                "kind": "integer",
                "unit": "分鐘",
                "default": 15,
                "min": 1,
                "max": 120,
                "step": 1,
            },
            "volume_window": {
                "label": "平均成交量週期",
                "kind": "integer",
                "unit": "根 K",
                "default": 5,
                "min": 1,
                "max": 120,
                "step": 1,
            },
            "volume_multiplier": {
                "label": "成交量倍數",
                "kind": "number",
                "unit": "倍",
                "default": 1.2,
                "min": 0,
                "max": 10,
                "step": 0.1,
            },
            "stop_loss_pct": {
                "label": "停損",
                "kind": "percent",
                "unit": "%",
                "default": 0.006,
                "min": 0.0001,
                "max": 0.2,
                "step": 0.0001,
            },
            "take_profit_pct": {
                "label": "停利",
                "kind": "percent",
                "unit": "%",
                "default": 0.012,
                "min": 0.0001,
                "max": 0.5,
                "step": 0.0001,
            },
        },
    },
    "bnf": {
        "key": "bnf",
        "name": "BNF 均值回歸",
        "description": "以均值、Z-score 與 RSI 辨識價格偏離後的回歸機會。",
        "color": "#a78bfa",
        "fields": {
            "mean_window": {
                "label": "均值週期",
                "kind": "integer",
                "unit": "根 K",
                "default": 20,
                "min": 2,
                "max": 500,
                "step": 1,
            },
            "std_window": {
                "label": "標準差週期",
                "kind": "integer",
                "unit": "根 K",
                "default": 20,
                "min": 2,
                "max": 500,
                "step": 1,
            },
            "entry_z_score": {
                "label": "進場 Z-score",
                "kind": "number",
                "unit": "σ",
                "default": 2.0,
                "min": 0.1,
                "max": 10,
                "step": 0.1,
            },
            "exit_z_score": {
                "label": "出場 Z-score",
                "kind": "number",
                "unit": "σ",
                "default": 0.5,
                "min": 0,
                "max": 9.9,
                "step": 0.1,
            },
            "rsi_period": {
                "label": "RSI 週期",
                "kind": "integer",
                "unit": "根 K",
                "default": 14,
                "min": 2,
                "max": 200,
                "step": 1,
            },
            "oversold_rsi": {
                "label": "RSI 超賣門檻",
                "kind": "number",
                "unit": "",
                "default": 30.0,
                "min": 0,
                "max": 99,
                "step": 1,
            },
            "overbought_rsi": {
                "label": "RSI 超買門檻",
                "kind": "number",
                "unit": "",
                "default": 70.0,
                "min": 1,
                "max": 100,
                "step": 1,
            },
            "stop_loss_pct": {
                "label": "停損",
                "kind": "percent",
                "unit": "%",
                "default": 0.006,
                "min": 0.0001,
                "max": 0.2,
                "step": 0.0001,
            },
            "take_profit_pct": {
                "label": "停利",
                "kind": "percent",
                "unit": "%",
                "default": 0.012,
                "min": 0.0001,
                "max": 0.5,
                "step": 0.0001,
            },
        },
    },
}


def default_strategy_parameters(strategy: str) -> dict[str, int | float]:
    definition = STRATEGY_DEFINITIONS.get(strategy.lower())
    if definition is None:
        raise ValueError(f"unsupported strategy: {strategy}")
    fields = definition["fields"]
    assert isinstance(fields, dict)
    return {key: field["default"] for key, field in fields.items()}


def validate_strategy_parameters(
    strategy: str,
    values: Mapping[str, object] | None = None,
) -> dict[str, int | float]:
    key = strategy.lower()
    definition = STRATEGY_DEFINITIONS.get(key)
    if definition is None:
        raise ValueError(f"unsupported strategy: {strategy}")
    fields = definition["fields"]
    assert isinstance(fields, dict)
    supplied = dict(values or {})
    unknown = sorted(set(supplied) - set(fields))
    if unknown:
        raise ValueError(f"不支援的參數：{', '.join(unknown)}")

    result = default_strategy_parameters(key)
    for name, raw in supplied.items():
        field = fields[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{field['label']} 必須是數字")
        value = float(raw)
        if not isfinite(value):
            raise ValueError(f"{field['label']} 必須是有限數字")
        if field["kind"] == "integer":
            if not value.is_integer():
                raise ValueError(f"{field['label']} 必須是整數")
            normalized: int | float = int(value)
        else:
            normalized = value
        if normalized < field["min"] or normalized > field["max"]:
            raise ValueError(
                f"{field['label']} 必須介於 {field['min']} 與 {field['max']}"
            )
        result[name] = normalized

    RiskConfig(
        stop_loss_pct=float(result["stop_loss_pct"]),
        take_profit_pct=float(result["take_profit_pct"]),
    )
    if key == "bnf":
        BNFMeanReversionConfig(
            mean_window=int(result["mean_window"]),
            std_window=int(result["std_window"]),
            entry_z_score=float(result["entry_z_score"]),
            exit_z_score=float(result["exit_z_score"]),
            rsi_period=int(result["rsi_period"]),
            oversold_rsi=float(result["oversold_rsi"]),
            overbought_rsi=float(result["overbought_rsi"]),
            direction="both",
        )
    return result


def strategy_catalog(
    overrides: Mapping[str, Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    saved = overrides or {}
    result = []
    for key in SUPPORTED_STRATEGIES:
        definition = deepcopy(STRATEGY_DEFINITIONS[key])
        values = validate_strategy_parameters(key, saved.get(key))
        fields = definition.pop("fields")
        assert isinstance(fields, dict)
        definition["parameters"] = {
            name: {**field, "value": values[name]}
            for name, field in fields.items()
        }
        result.append(definition)
    return result
