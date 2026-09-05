from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Callable, Mapping
from uuid import uuid4

from ..market import SUPPORTED_TIMEFRAMES, KBar, aggregate_kbars, validate_timeframe
from ..risk import RiskConfig, RiskLevels, calculate_levels, triggered_exit
from .engine import analyze_strategies
from .parameters import SUPPORTED_STRATEGIES, validate_strategy_parameters


ROLES = ("setup", "entry", "exit")
OPERATORS = ("all", "any")
DIRECTIONS = ("both", "long", "short")
MAX_COMPOSITE_DEPTH = 3
CompositeResolver = Callable[[str, int], Mapping[str, object] | None]


def default_composite_definition() -> dict[str, object]:
    return {
        "name": "30 分 K 趨勢、1 分 K 進場",
        "description": "以高週期確認方向，再由低週期尋找進場與出場時機。",
        "enabled": True,
        "direction": "both",
        "setup": {
            "operator": "all",
            "confirmation_window_minutes": 60,
            "rules": [{"strategy": "orb", "interval": "30m"}],
        },
        "entry": {
            "operator": "all",
            "confirmation_window_minutes": 15,
            "rules": [{"strategy": "bnf", "interval": "1m"}],
        },
        "exit": {
            "operator": "any",
            "confirmation_window_minutes": 5,
            "rules": [{"strategy": "bnf", "interval": "1m"}],
        },
        "risk": {
            "monitor_interval": "1m",
            "stop_loss_pct": 0.006,
            "take_profit_pct": 0.012,
            "max_holding_minutes": 240,
        },
    }


def _number(value: object, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必須是數字")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{label}必須介於 {minimum} 與 {maximum}")
    return result


def _validate_group(
    role: str,
    raw: object,
    atomic_parameters: Mapping[str, Mapping[str, object]],
    composite_resolver: CompositeResolver | None = None,
) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{role} 必須是規則群組")
    operator = str(raw.get("operator", "any" if role == "exit" else "all")).lower()
    if operator not in OPERATORS:
        raise ValueError(f"{role}.operator 僅支援 all 或 any")
    window = int(_number(
        raw.get("confirmation_window_minutes", 15),
        f"{role} 確認視窗",
        1,
        1440,
    ))
    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError(f"{role}.rules 必須是陣列")
    if role == "entry" and not raw_rules:
        raise ValueError("entry 至少需要一條規則")
    rules = []
    for index, item in enumerate(raw_rules):
        if not isinstance(item, Mapping):
            raise ValueError(f"{role} 第 {index + 1} 條規則格式錯誤")
        source = str(item.get("source", "atomic")).lower()
        if source == "composite":
            if composite_resolver is None:
                raise ValueError("組合策略引用需要版本解析器")
            strategy_id = str(item.get("strategy_id", "")).strip()
            try:
                version = int(item.get("version", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("組合策略引用版本必須是正整數") from exc
            if not strategy_id or version < 1:
                raise ValueError("組合策略引用必須包含 strategy_id 與 version")
            resolved = composite_resolver(strategy_id, version)
            if resolved is None:
                raise ValueError(f"找不到被引用的組合策略版本：{strategy_id} v{version}")
            child_definition = resolved.get("definition")
            if not isinstance(child_definition, Mapping):
                raise ValueError("被引用的組合策略版本內容無效")
            rules.append({
                "id": str(item.get("id") or f"{role}-{index + 1}"),
                "source": "composite",
                "strategy_id": strategy_id,
                "version": version,
                "name": str(resolved.get("name", child_definition.get("name", ""))),
                "definition": deepcopy(dict(child_definition)),
            })
            continue
        if source != "atomic":
            raise ValueError(f"{role} 第 {index + 1} 條規則來源無效")
        strategy = str(item.get("strategy", "")).lower()
        if strategy not in SUPPORTED_STRATEGIES:
            raise ValueError(f"{role} 使用不支援的策略：{strategy}")
        interval = validate_timeframe(str(item.get("interval", "1m")))
        overrides = item.get("parameters")
        if overrides is not None and not isinstance(overrides, Mapping):
            raise ValueError(f"{role} 第 {index + 1} 條規則參數格式錯誤")
        merged = dict(atomic_parameters.get(strategy, {}))
        merged.update(dict(overrides or {}))
        rules.append({
            "id": str(item.get("id") or f"{role}-{index + 1}"),
            "source": "atomic",
            "strategy": strategy,
            "interval": interval,
            "parameters": validate_strategy_parameters(strategy, merged),
        })
    return {
        "operator": operator,
        "confirmation_window_minutes": window,
        "rules": rules,
    }


def validate_composite_definition(
    raw: Mapping[str, object],
    atomic_parameters: Mapping[str, Mapping[str, object]] | None = None,
    composite_resolver: CompositeResolver | None = None,
) -> dict[str, object]:
    defaults = default_composite_definition()
    name = str(raw.get("name", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("策略名稱必須為 1～80 個字元")
    direction = str(raw.get("direction", "both")).lower()
    if direction not in DIRECTIONS:
        raise ValueError("direction 僅支援 both、long 或 short")
    saved = atomic_parameters or {}
    risk_raw = raw.get("risk")
    if not isinstance(risk_raw, Mapping):
        raise ValueError("risk 為必填")
    stop = _number(risk_raw.get("stop_loss_pct"), "停損", 0.0001, 0.2)
    take = _number(risk_raw.get("take_profit_pct"), "停利", 0.0001, 0.5)
    RiskConfig(stop_loss_pct=stop, take_profit_pct=take)
    holding = int(_number(
        risk_raw.get("max_holding_minutes", 240), "最長持有時間", 1, 10080
    ))
    monitor_interval = validate_timeframe(str(risk_raw.get("monitor_interval", "1m")))
    if monitor_interval != "1m":
        raise ValueError("第一版風險監控固定使用 1 分 K，避免漏過盤中停損")
    result = {
        "name": name,
        "description": str(raw.get("description", "")).strip()[:500],
        "enabled": bool(raw.get("enabled", True)),
        "direction": direction,
        "setup": _validate_group(
            "setup", raw.get("setup", defaults["setup"]), saved,
            composite_resolver,
        ),
        "entry": _validate_group(
            "entry", raw.get("entry"), saved, composite_resolver,
        ),
        "exit": _validate_group(
            "exit", raw.get("exit", defaults["exit"]), saved,
            composite_resolver,
        ),
        "risk": {
            "monitor_interval": monitor_interval,
            "stop_loss_pct": stop,
            "take_profit_pct": take,
            "max_holding_minutes": holding,
        },
    }
    return result


def validate_composite_dependencies(
    definition: Mapping[str, object],
    strategy_id: str,
    max_depth: int = MAX_COMPOSITE_DEPTH,
) -> None:
    """Reject self/cyclic references and excessively deep composite trees."""

    def walk(
        current: Mapping[str, object], path: tuple[str, ...], depth: int
    ) -> None:
        if depth > max_depth:
            raise ValueError(f"組合策略最多支援 {max_depth} 層引用")
        for role in ROLES:
            group = current.get(role)
            if not isinstance(group, Mapping):
                continue
            rules = group.get("rules", [])
            if not isinstance(rules, list):
                continue
            for rule in rules:
                if not isinstance(rule, Mapping) or rule.get("source") != "composite":
                    continue
                child_id = str(rule.get("strategy_id", ""))
                if child_id in path:
                    raise ValueError("組合策略不可循環引用或引用自己")
                child = rule.get("definition")
                if isinstance(child, Mapping):
                    walk(child, (*path, child_id), depth + 1)

    walk(definition, (strategy_id,), 1)


def new_composite_id() -> str:
    return uuid4().hex


def _rule_events(
    bars: list[KBar],
    role: str,
    group: Mapping[str, object],
    cache: dict[tuple[str, int], tuple[list[dict[str, object]], list[dict[str, object]]]],
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for rule in group["rules"]:  # type: ignore[index]
        if rule.get("source") == "composite":
            key = (str(rule["strategy_id"]), int(rule["version"]))
            if key not in cache:
                cache[key] = generate_composite_signals(
                    bars, rule["definition"], _cache=cache
                )
            signals = cache[key][0]
            interval = "multi"
        else:
            interval = str(rule["interval"])
            strategy = str(rule["strategy"])
            signals = analyze_strategies(
                aggregate_kbars(bars, interval),
                [strategy],
                force_close_last=False,
                parameters={strategy: rule["parameters"]},
                interval=interval,
            )["strategies"][0]["signals"]
        wanted = "exit" if role == "exit" else "entry"
        for signal in signals:
            if signal["event"] == wanted:
                events.append({
                    **signal,
                    "role": role,
                    "rule_id": rule["id"],
                    "interval": interval,
                })
    return events


def _group_match(
    group: Mapping[str, object],
    latest: Mapping[tuple[str, str], dict[str, object]],
    direction: str,
    now: datetime,
    minimum_time: datetime | None = None,
) -> tuple[bool, list[dict[str, object]]]:
    rules = group["rules"]  # type: ignore[index]
    if not rules:
        return True, []
    window = float(group["confirmation_window_minutes"]) * 60
    matches = []
    for rule in rules:
        event = latest.get((str(rule["id"]), direction))
        if (
            event
            and (minimum_time is None or event["at"] >= minimum_time)
            and 0 <= (now - event["at"]).total_seconds() <= window
        ):
            matches.append(event)
    satisfied = bool(matches) if group["operator"] == "any" else len(matches) == len(rules)
    return satisfied, matches


def generate_composite_signals(
    source_bars: list[KBar],
    definition: Mapping[str, object],
    *,
    _cache: dict[
        tuple[str, int],
        tuple[list[dict[str, object]], list[dict[str, object]]],
    ] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Run one composite definition on canonical closed 1-minute bars.

    Atomic rules are evaluated by the same strategy engine used by Live and CSV
    backtests. Composite fills occur on the following 1-minute open; risk checks
    remain on 1-minute OHLC and stop-loss wins if SL/TP occur in one bar.
    """
    bars = sorted((bar for bar in source_bars if bar.status == "closed"), key=lambda b: b.time)
    if not bars:
        return [], []
    cache = _cache if _cache is not None else {}
    events = []
    for role in ROLES:
        events.extend(
            _rule_events(bars, role, definition[role], cache)  # type: ignore[index]
        )
    events.sort(key=lambda item: str(item["time"]))
    by_time: dict[datetime, list[dict[str, object]]] = {}
    for event in events:
        at = datetime.fromisoformat(str(event["time"]))
        by_time.setdefault(at, []).append({**event, "at": at})

    risk_raw = definition["risk"]  # type: ignore[index]
    risk = RiskConfig(float(risk_raw["stop_loss_pct"]), float(risk_raw["take_profit_pct"]))
    max_holding = int(risk_raw["max_holding_minutes"])
    allowed = ("long", "short") if definition["direction"] == "both" else (str(definition["direction"]),)
    latest: dict[tuple[str, str], dict[str, object]] = {}
    signals: list[dict[str, object]] = []
    trace: list[dict[str, object]] = []
    position = 0
    entry_at: datetime | None = None
    levels = RiskLevels(0.0, 0.0)
    pending_entry: tuple[str, list[dict[str, object]]] | None = None
    pending_exit: str | None = None
    last_signature: tuple[object, ...] | None = None

    def emit(event: str, bar: KBar, price: float, reason: str) -> None:
        signals.append({
            "strategy": "composite",
            "event": event,
            "direction": "long" if position == 1 else "short",
            "time": bar.time.isoformat(timespec="milliseconds"),
            "price": round(price, 4),
            "stop_loss_price": round(levels.stop_loss_price, 4),
            "take_profit_price": round(levels.take_profit_price, 4),
            "reason": reason,
            "contract": bar.contract,
            "session": bar.session,
            "trading_date": bar.trading_date.isoformat(),
        })

    for index, bar in enumerate(bars):
        if position and pending_exit:
            emit("exit", bar, bar.open, pending_exit)
            position = 0
            entry_at = None
            pending_exit = None
        if position == 0 and pending_entry:
            direction, matched = pending_entry
            if any(
                item["contract"] != bar.contract or item["session"] != bar.session
                for item in matched
            ):
                pending_entry = None
                continue
            position = 1 if direction == "long" else -1
            entry_at = bar.time
            levels = calculate_levels(bar.open, position, risk)
            emit("entry", bar, bar.open, "composite_confirmed")
            trace.append({
                "time": bar.time.isoformat(timespec="milliseconds"),
                "event": "entry",
                "direction": direction,
                "matched_rules": [item["rule_id"] for item in matched],
            })
            pending_entry = None

        if position:
            risk_exit = triggered_exit(
                direction=position,
                open_price=bar.open,
                high=bar.high,
                low=bar.low,
                levels=levels,
            )
            if risk_exit:
                emit("exit", bar, float(risk_exit[0]), str(risk_exit[1]))
                position = 0
                entry_at = None
            elif entry_at and (bar.time - entry_at).total_seconds() >= max_holding * 60:
                emit("exit", bar, bar.close, "max_holding_time")
                position = 0
                entry_at = None

        current_events = by_time.get(bar.time, [])
        for event in current_events:
            latest[(str(event["rule_id"]), str(event["direction"]))] = event

        if position:
            direction = "long" if position == 1 else "short"
            matched, exit_events = _group_match(
                definition["exit"], latest, direction, bar.time, entry_at
            )  # type: ignore[arg-type]
            if matched and exit_events:
                signature = tuple((item["rule_id"], item["time"]) for item in exit_events)
                if signature != last_signature:
                    pending_exit = "composite_exit"
                    last_signature = signature
        elif pending_entry is None:
            for direction in allowed:
                setup_ok, setup_events = _group_match(definition["setup"], latest, direction, bar.time)  # type: ignore[arg-type]
                entry_ok, entry_events = _group_match(definition["entry"], latest, direction, bar.time)  # type: ignore[arg-type]
                matched_events = setup_events + entry_events
                signature = tuple((item["rule_id"], item["time"]) for item in matched_events)
                if setup_ok and entry_ok and entry_events and signature != last_signature:
                    pending_entry = (direction, matched_events)
                    last_signature = signature
                    break

        if position and index + 1 < len(bars):
            next_bar = bars[index + 1]
            if next_bar.contract != bar.contract or next_bar.session != bar.session:
                emit("exit", bar, bar.close, "session_end")
                position = 0
                entry_at = None
                pending_exit = None

    if position:
        emit("exit", bars[-1], bars[-1].close, "range_end")
    return signals, trace
