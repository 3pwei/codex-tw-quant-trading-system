"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type ParameterField = {
  label: string;
  kind: "integer" | "number" | "percent";
  unit: string;
  default: number;
  min: number;
  max: number;
  step: number;
  value: number;
};

type Strategy = {
  key: string;
  name: string;
  description: string;
  color: string;
  parameters: Record<string, ParameterField>;
};

const apiBase = () => (
  process.env.NEXT_PUBLIC_MARKET_API_URL
  ?? (typeof window === "undefined" ? "" : window.location.origin)
).replace(/\/$/, "");

const shownValue = (field: ParameterField, value: number) =>
  field.kind === "percent" ? value * 100 : value;

const storedValue = (field: ParameterField, value: number) =>
  field.kind === "percent" ? value / 100 : value;

const draftValues = (items: Strategy[]) => Object.fromEntries(items.map(strategy => [
  strategy.key,
  Object.fromEntries(Object.entries(strategy.parameters).map(([name, field]) => [
    name,
    shownValue(field, field.value),
  ])),
]));

const fetchStrategies = async (): Promise<Strategy[]> => {
  const response = await fetch(`${apiBase()}/api/strategies`, { cache: "no-store" });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail ?? "無法取得策略參數");
  return body.strategies;
};

export default function StrategyManager() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Record<string, number>>>({});
  const [saving, setSaving] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    fetchStrategies().then(items => {
      if (!active) return;
      setStrategies(items);
      setDrafts(draftValues(items));
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : "無法取得策略參數");
    });
    return () => { active = false; };
  }, []);

  const save = async (strategy: Strategy) => {
    setSaving(strategy.key);
    setError("");
    setNotice("");
    try {
      const draft = drafts[strategy.key];
      if (!draft || Object.values(draft).some(value => !Number.isFinite(value))) {
        throw new Error("所有策略參數都必須填入有效數字");
      }
      const parameters = Object.fromEntries(
        Object.entries(strategy.parameters).map(([name, field]) => [
          name,
          storedValue(field, drafts[strategy.key]?.[name]),
        ]),
      );
      const response = await fetch(`${apiBase()}/api/strategies/${strategy.key}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parameters }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "參數儲存失敗");
      const items = await fetchStrategies();
      setStrategies(items);
      setDrafts(draftValues(items));
      setNotice(`${strategy.name} 已儲存；下一次即時分析與回測會使用新參數。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "參數儲存失敗");
    } finally {
      setSaving("");
    }
  };

  const resetDraft = (strategy: Strategy) => {
    setDrafts(current => ({
      ...current,
      [strategy.key]: Object.fromEntries(
        Object.entries(strategy.parameters).map(([name, field]) => [
          name,
          shownValue(field, field.default),
        ]),
      ),
    }));
    setNotice("已載入預設值；按「儲存參數」後才會套用。");
    setError("");
  };

  if (!strategies.length && !error) {
    return <section className="panel strategy-loading">正在讀取策略參數…</section>;
  }

  return <>
    {error && <div className="live-error">{error}</div>}
    {notice && <div className="strategy-notice">{notice}</div>}
    <section className="strategy-catalog editable">
      {strategies.map(strategy => <article className="panel" key={strategy.key}>
        <div className="strategy-editor-head">
          <div><span style={{ color: strategy.color }}>{strategy.key.toUpperCase()}</span><h2>{strategy.name}</h2></div>
          <b>即時／回測共用</b>
        </div>
        <p>{strategy.description}</p>
        <div className="strategy-fields">
          {Object.entries(strategy.parameters).map(([name, field]) => {
            const scale = field.kind === "percent" ? 100 : 1;
            return <label key={name}>
              <span>{field.label}<small>{field.unit}</small></span>
              <input
                type="number"
                required
                aria-label={`${strategy.name} ${field.label}`}
                value={drafts[strategy.key]?.[name] ?? ""}
                min={field.min * scale}
                max={field.max * scale}
                step={field.step * scale}
                onChange={event => setDrafts(current => ({
                  ...current,
                  [strategy.key]: {
                    ...current[strategy.key],
                    [name]: event.target.valueAsNumber,
                  },
                }))}
              />
            </label>;
          })}
        </div>
        <div className="strategy-actions">
          <button className="secondary" type="button" onClick={() => resetDraft(strategy)}>恢復預設</button>
          <Link href={`/composite-strategies/new/?strategy=${strategy.key}`}>用此策略建立組合</Link>
          <button type="button" disabled={saving === strategy.key} onClick={() => save(strategy)}>
            {saving === strategy.key ? "儲存中…" : "儲存參數"}
          </button>
        </div>
      </article>)}
    </section>
    <p className="page-note">參數儲存在伺服器 SQLite，不會送入 GitHub；新設定會同步套用於 Live、Replay 與 Backtest 的共用策略核心。正式下單功能仍未啟用。</p>
  </>;
}
