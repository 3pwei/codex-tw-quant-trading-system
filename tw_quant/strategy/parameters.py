from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Mapping

from ..risk import RiskConfig
from .definitions import BNFMeanReversionConfig


SUPPORTED_STRATEGIES = (
    "orb",
    "bnf",
    "ma_crossover",
    "ema_trend",
    "donchian_breakout",
    "rsi_mean_reversion",
    "bollinger_mean_reversion",
    "macd_momentum",
    "vwap_reversion",
    "atr_breakout",
    "volume_breakout",
)


RISK_FIELDS = {
    "stop_loss_pct": {
        "label": "停損", "kind": "percent", "unit": "%",
        "default": 0.006, "min": 0.0001, "max": 0.2, "step": 0.0001,
    },
    "take_profit_pct": {
        "label": "停利", "kind": "percent", "unit": "%",
        "default": 0.012, "min": 0.0001, "max": 0.5, "step": 0.0001,
    },
}


STRATEGY_DEFINITIONS: dict[str, dict[str, object]] = {
    "orb": {
        "key": "orb",
        "name": "Opening Range Breakout（ORB）",
        "category": "Breakout",
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
        "category": "Mean Reversion",
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
    "ma_crossover": {
        "key": "ma_crossover",
        "name": "MA Crossover",
        "category": "Trend",
        "description": "短期與長期簡單移動平均線交叉時辨識趨勢轉換。",
        "color": "#22d3ee",
        "fields": {
            "short_window": {"label": "短期 MA", "kind": "integer", "unit": "根 K", "default": 10, "min": 2, "max": 200, "step": 1},
            "long_window": {"label": "長期 MA", "kind": "integer", "unit": "根 K", "default": 30, "min": 3, "max": 500, "step": 1},
            **RISK_FIELDS,
        },
    },
    "ema_trend": {
        "key": "ema_trend",
        "name": "EMA Trend",
        "category": "Trend",
        "description": "利用快速與慢速指數移動平均線交叉追蹤趨勢。",
        "color": "#34d399",
        "fields": {
            "fast_period": {"label": "快速 EMA", "kind": "integer", "unit": "根 K", "default": 12, "min": 2, "max": 200, "step": 1},
            "slow_period": {"label": "慢速 EMA", "kind": "integer", "unit": "根 K", "default": 26, "min": 3, "max": 500, "step": 1},
            **RISK_FIELDS,
        },
    },
    "donchian_breakout": {
        "key": "donchian_breakout",
        "name": "Donchian Breakout",
        "category": "Breakout",
        "description": "價格突破前期 Donchian 通道高低點時順勢進場。",
        "color": "#60a5fa",
        "fields": {
            "lookback_period": {"label": "通道週期", "kind": "integer", "unit": "根 K", "default": 20, "min": 2, "max": 500, "step": 1},
            **RISK_FIELDS,
        },
    },
    "rsi_mean_reversion": {
        "key": "rsi_mean_reversion",
        "name": "RSI Mean Reversion",
        "category": "Mean Reversion",
        "description": "RSI 進入超買或超賣區後，捕捉價格回歸中性區的機會。",
        "color": "#c084fc",
        "fields": {
            "rsi_period": {"label": "RSI 週期", "kind": "integer", "unit": "根 K", "default": 14, "min": 2, "max": 200, "step": 1},
            "oversold_rsi": {"label": "超賣門檻", "kind": "number", "unit": "", "default": 30.0, "min": 0, "max": 49, "step": 1},
            "exit_rsi": {"label": "回歸出場門檻", "kind": "number", "unit": "", "default": 50.0, "min": 1, "max": 99, "step": 1},
            "overbought_rsi": {"label": "超買門檻", "kind": "number", "unit": "", "default": 70.0, "min": 51, "max": 100, "step": 1},
            **RISK_FIELDS,
        },
    },
    "bollinger_mean_reversion": {
        "key": "bollinger_mean_reversion",
        "name": "Bollinger Mean Reversion",
        "category": "Mean Reversion",
        "description": "價格觸及布林通道外側時進場，回到中軌時出場。",
        "color": "#f472b6",
        "fields": {
            "window": {"label": "布林週期", "kind": "integer", "unit": "根 K", "default": 20, "min": 2, "max": 500, "step": 1},
            "std_multiplier": {"label": "標準差倍數", "kind": "number", "unit": "σ", "default": 2.0, "min": 0.1, "max": 10, "step": 0.1},
            **RISK_FIELDS,
        },
    },
    "macd_momentum": {
        "key": "macd_momentum",
        "name": "MACD Momentum",
        "category": "Momentum",
        "description": "MACD 線穿越訊號線時追蹤動能方向。",
        "color": "#f59e0b",
        "fields": {
            "fast_period": {"label": "快速 EMA", "kind": "integer", "unit": "根 K", "default": 12, "min": 2, "max": 200, "step": 1},
            "slow_period": {"label": "慢速 EMA", "kind": "integer", "unit": "根 K", "default": 26, "min": 3, "max": 500, "step": 1},
            "signal_period": {"label": "訊號 EMA", "kind": "integer", "unit": "根 K", "default": 9, "min": 2, "max": 200, "step": 1},
            **RISK_FIELDS,
        },
    },
    "vwap_reversion": {
        "key": "vwap_reversion",
        "name": "VWAP Reversion",
        "category": "Intraday",
        "description": "價格偏離交易時段累積 VWAP 時反向進場，回歸 VWAP 附近時出場。",
        "color": "#2dd4bf",
        "fields": {
            "entry_deviation_pct": {"label": "進場偏離", "kind": "percent", "unit": "%", "default": 0.005, "min": 0.0001, "max": 0.1, "step": 0.0001},
            "exit_deviation_pct": {"label": "出場偏離", "kind": "percent", "unit": "%", "default": 0.001, "min": 0, "max": 0.0999, "step": 0.0001},
            **RISK_FIELDS,
        },
    },
    "atr_breakout": {
        "key": "atr_breakout",
        "name": "ATR Breakout",
        "category": "Volatility",
        "description": "價格單根波動超過 ATR 倍數時，沿波動擴張方向進場。",
        "color": "#fb7185",
        "fields": {
            "atr_period": {"label": "ATR 週期", "kind": "integer", "unit": "根 K", "default": 14, "min": 2, "max": 200, "step": 1},
            "atr_multiplier": {"label": "突破倍數", "kind": "number", "unit": "倍 ATR", "default": 1.5, "min": 0.1, "max": 10, "step": 0.1},
            **RISK_FIELDS,
        },
    },
    "volume_breakout": {
        "key": "volume_breakout",
        "name": "Volume Breakout",
        "category": "Momentum",
        "description": "價格突破近期高低點且成交量同步放大時進場。",
        "color": "#a3e635",
        "fields": {
            "price_lookback": {"label": "價格突破週期", "kind": "integer", "unit": "根 K", "default": 20, "min": 2, "max": 500, "step": 1},
            "volume_window": {"label": "平均成交量週期", "kind": "integer", "unit": "根 K", "default": 20, "min": 2, "max": 500, "step": 1},
            "volume_multiplier": {"label": "成交量倍數", "kind": "number", "unit": "倍", "default": 1.5, "min": 0.1, "max": 10, "step": 0.1},
            **RISK_FIELDS,
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
    if key in {"ma_crossover", "ema_trend", "macd_momentum"}:
        short_name = "short_window" if key == "ma_crossover" else "fast_period"
        long_name = "long_window" if key == "ma_crossover" else "slow_period"
        if int(result[short_name]) >= int(result[long_name]):
            raise ValueError("短期週期必須小於長期週期")
    if key == "rsi_mean_reversion" and not (
        float(result["oversold_rsi"])
        < float(result["exit_rsi"])
        < float(result["overbought_rsi"])
    ):
        raise ValueError("RSI 門檻必須滿足超賣 < 出場 < 超買")
    if key == "vwap_reversion" and float(result["exit_deviation_pct"]) >= float(
        result["entry_deviation_pct"]
    ):
        raise ValueError("VWAP 出場偏離必須小於進場偏離")
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
