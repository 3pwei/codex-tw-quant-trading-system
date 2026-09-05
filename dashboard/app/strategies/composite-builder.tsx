"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

type ParameterField = { label: string; kind: "integer" | "number" | "percent"; unit: string; min: number; max: number; step: number; value: number };
type AtomicStrategy = { key: string; name: string; parameters: Record<string, ParameterField> };
type Rule = { id?: string; source?: "atomic" | "composite"; strategy?: string; interval?: string; parameters?: Record<string, number>; strategy_id?: string; version?: number; name?: string; definition?: Definition };
type Group = { operator: "all" | "any"; confirmation_window_minutes: number; rules: Rule[] };
type Definition = { name: string; description: string; enabled: boolean; direction: "both" | "long" | "short"; setup: Group; entry: Group; exit: Group; risk: { monitor_interval: "1m"; stop_loss_pct: number; take_profit_pct: number; max_holding_minutes: number } };
type SavedComposite = { id: string; version: number; name: string; definition: Definition; created_at: string };
type CompositeReferenceOption = { id: string; name: string; versions: { version: number; name: string }[] };
type CatalogResponse = { template: Definition; reference_strategies?: CompositeReferenceOption[] };

const intervals = ["1m", "5m", "10m", "15m", "30m", "1h", "1d", "1w"];
const intervalNames: Record<string, string> = { "1m":"1 分", "5m":"5 分", "10m":"10 分", "15m":"15 分", "30m":"30 分", "1h":"1 小時", "1d":"日 K", "1w":"週 K" };
const apiBase = () => (process.env.NEXT_PUBLIC_MARKET_API_URL ?? (typeof window === "undefined" ? "" : window.location.origin)).replace(/\/$/, "");
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

function RuleGroupEditor({ title, hint, value, strategies, compositeOptions, excludedCompositeId, required, onChange }: {
  title: string; hint: string; value: Group; strategies: AtomicStrategy[]; compositeOptions: CompositeReferenceOption[]; excludedCompositeId: string | null; required?: boolean; onChange: (next: Group) => void;
}) {
  const updateRule = (index: number, patch: Partial<Rule>) => onChange({ ...value, rules: value.rules.map((rule, i) => i === index ? { ...rule, ...patch } : rule) });
  return <section className="composer-stage">
    <header><div><b>{title}</b><span>{hint}</span></div><div className="composer-logic"><select aria-label={`${title}組合邏輯`} value={value.operator} onChange={event => onChange({ ...value, operator: event.target.value as "all" | "any" })}><option value="all">全部符合 ALL</option><option value="any">任一符合 ANY</option></select><label>確認視窗 <input type="number" min={1} max={1440} value={value.confirmation_window_minutes} onChange={event => onChange({ ...value, confirmation_window_minutes: event.target.valueAsNumber })} /> 分</label></div></header>
    <div className="composer-rules">
      {value.rules.map((rule, index) => {
        const isComposite = rule.source === "composite";
        const atomic = isComposite ? undefined : strategies.find(strategy => strategy.key === rule.strategy);
        const selectedValue = isComposite ? `composite:${rule.strategy_id}` : `atomic:${rule.strategy}`;
        const selectableComposites = compositeOptions.filter(item => item.id !== excludedCompositeId);
        const selectedComposite = selectableComposites.find(item => item.id === rule.strategy_id);
        return <div className="composer-rule-wrap" key={`${title}-${index}`}><div className="composer-rule">
          <span>{index + 1}</span>
          <select aria-label={`${title}規則 ${index + 1} 策略`} value={selectedValue} onChange={event => {
            const selected = event.target.value;
            if (selected.startsWith("atomic:")) {
              updateRule(index, { source: "atomic", strategy: selected.slice(7), interval: "1m", parameters: {}, strategy_id: undefined, version: undefined, name: undefined, definition: undefined });
              return;
            }
            const item = selectableComposites.find(candidate => `composite:${candidate.id}` === selected);
            const selectedVersion = item?.versions[0];
            if (item && selectedVersion) updateRule(index, { source: "composite", strategy: undefined, interval: undefined, parameters: undefined, strategy_id: item.id, version: selectedVersion.version, name: selectedVersion.name, definition: undefined });
          }}>
            <optgroup label="基本策略">{strategies.map(strategy => <option value={`atomic:${strategy.key}`} key={strategy.key}>{strategy.name}</option>)}</optgroup>
            {(selectableComposites.length > 0 || isComposite) && <optgroup label="組合策略（固定版本）">
              {isComposite && !selectedComposite && <option value={selectedValue}>{rule.name ?? "既有組合策略"}（已封存）</option>}
              {selectableComposites.map(item => <option value={`composite:${item.id}`} key={item.id}>{item.name}</option>)}
            </optgroup>}
          </select>
          {isComposite ? (selectedComposite ? <select aria-label={`${title}規則 ${index + 1} 組合策略版本`} value={rule.version} onChange={event => {
            const selectedVersion = selectedComposite.versions.find(item => item.version === Number(event.target.value));
            if (selectedVersion) updateRule(index, { version: selectedVersion.version, name: selectedVersion.name, definition: undefined });
          }}>{selectedComposite.versions.map(item => <option value={item.version} key={item.version}>固定 v{item.version}</option>)}</select> : <div className="composite-version-lock">固定 v{rule.version} · 已封存</div>) : <select aria-label={`${title}規則 ${index + 1} 週期`} value={rule.interval} onChange={event => updateRule(index, { interval: event.target.value })}>{intervals.map(interval => <option value={interval} key={interval}>{intervalNames[interval]}</option>)}</select>}
          <button type="button" className="danger" disabled={required && value.rules.length === 1} onClick={() => onChange({ ...value, rules: value.rules.filter((_, i) => i !== index) })}>移除</button>
        </div>{atomic && <details className="rule-parameters"><summary>覆寫此積木參數（未修改則繼承基本策略）</summary><div>{Object.entries(atomic.parameters).map(([name, field]) => {
          const scale = field.kind === "percent" ? 100 : 1;
          const stored = rule.parameters?.[name] ?? field.value;
          return <label key={name}><span>{field.label}<small>{field.unit}</small></span><input type="number" min={field.min * scale} max={field.max * scale} step={field.step * scale} value={stored * scale} onChange={event => updateRule(index, { parameters: { ...rule.parameters, [name]: event.target.valueAsNumber / scale } })} /></label>;
        })}</div></details>}</div>;
      })}
      {!value.rules.length && <p>未設定規則，此階段視為直接通過。</p>}
    </div>
    <button type="button" className="secondary add-rule" onClick={() => onChange({ ...value, rules: [...value.rules, { source: "atomic", strategy: strategies[0]?.key ?? "orb", interval: "1m" }] })}>＋ 新增條件</button>
  </section>;
}

export default function CompositeBuilder({ mode }: { mode: "new" | "edit" }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedId = mode === "edit" ? searchParams.get("strategy_id") : null;
  const sourceId = mode === "new" ? searchParams.get("source_id") : null;
  const sourceVersion = Number(searchParams.get("version") || 0);
  const requestedAtomic = mode === "new" ? searchParams.get("strategy") : null;
  const [strategies, setStrategies] = useState<AtomicStrategy[]>([]);
  const [referenceOptions, setReferenceOptions] = useState<CompositeReferenceOption[]>([]);
  const [draft, setDraft] = useState<Definition | null>(null);
  const [editing, setEditing] = useState<SavedComposite | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const load = async () => {
      if (mode === "edit" && !requestedId) throw new Error("缺少要編輯的組合策略 ID");
      const [strategyResponse, catalogResponse] = await Promise.all([
        fetch(`${apiBase()}/api/strategies`, { cache: "no-store" }),
        fetch(`${apiBase()}/api/composite-strategies`, { cache: "no-store" }),
      ]);
      const strategyBody = await strategyResponse.json();
      const catalog: CatalogResponse = await catalogResponse.json();
      if (!strategyResponse.ok || !catalogResponse.ok) throw new Error("無法取得策略資料");
      let nextDraft = copy(catalog.template);
      let nextEditing: SavedComposite | null = null;
      const loadId = requestedId ?? sourceId;
      if (loadId) {
        const versionQuery = sourceId && sourceVersion > 0 ? `?version=${sourceVersion}` : "";
        const response = await fetch(`${apiBase()}/api/composite-strategies/${loadId}${versionQuery}`, { cache: "no-store" });
        const item = await response.json();
        if (!response.ok) throw new Error(item.detail ?? "找不到指定的組合策略");
        nextDraft = copy(item.definition);
        if (requestedId) nextEditing = item;
        else nextDraft.name = `${item.name} 複本`;
      } else if (requestedAtomic && strategyBody.strategies.some((item: AtomicStrategy) => item.key === requestedAtomic)) {
        nextDraft.entry.rules[0] = { source: "atomic", strategy: requestedAtomic, interval: "1m" };
      }
      if (active) {
        setStrategies(strategyBody.strategies);
        setReferenceOptions(catalog.reference_strategies ?? []);
        setDraft(nextDraft);
        setEditing(nextEditing);
        setNotice(sourceId ? "已複製歷史版本並建立草稿；請確認名稱後儲存為新策略 v1。" : "");
      }
    };
    void load().catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "無法取得策略資料"); });
    return () => { active = false; };
  }, [mode, requestedAtomic, requestedId, sourceId, sourceVersion]);

  const renamed = useMemo(() => Boolean(editing && draft && draft.name.trim() !== editing.name), [draft, editing]);
  if (!draft) return <section className="panel strategy-loading">{error || "正在讀取組合策略…"}</section>;
  const setGroup = (key: "setup" | "entry" | "exit", value: Group) => setDraft({ ...draft, [key]: value });
  const save = async () => {
    setSaving(true); setError(""); setNotice("");
    try {
      const url = editing ? `${apiBase()}/api/composite-strategies/${editing.id}` : `${apiBase()}/api/composite-strategies`;
      const response = await fetch(url, { method: editing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ definition: draft }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "組合策略儲存失敗");
      setEditing(body); setDraft(copy(body.definition));
      setNotice(renamed ? `${body.name} 已另存為全新策略 v1；原策略仍保留。` : `${body.name} v${body.version} 已儲存。`);
      router.replace(`/composite-strategies/editor/?strategy_id=${body.id}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "組合策略儲存失敗"); }
    finally { setSaving(false); }
  };

  return <section className="composite-editor-page">
    <div className="editor-toolbar"><div><Link href="/composite-strategies/">← 返回組合策略</Link><span>{editing ? `正在編輯 ${editing.name} v${editing.version}` : sourceId ? "從歷史版本建立新策略" : "建立全新組合策略"}</span></div><Link href="/composite-strategies/" className="editor-cancel">取消</Link></div>
    {error && <div className="live-error">{error}</div>}{notice && <div className="strategy-notice">{notice}</div>}
    <div className="panel composite-editor">
      <div className="composer-basics"><label><span>策略名稱</span><input value={draft.name} maxLength={80} onChange={event => setDraft({ ...draft, name: event.target.value })} /></label><label><span>交易方向</span><select value={draft.direction} onChange={event => setDraft({ ...draft, direction: event.target.value as Definition["direction"] })}><option value="both">多空皆可</option><option value="long">只做多</option><option value="short">只做空</option></select></label><label className="wide"><span>策略說明</span><input value={draft.description} maxLength={500} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label></div>
      <RuleGroupEditor title="1 · SETUP" hint="高週期背景／啟動條件，可留空" value={draft.setup} strategies={strategies} compositeOptions={referenceOptions} excludedCompositeId={editing?.id ?? null} onChange={value => setGroup("setup", value)} />
      <RuleGroupEditor title="2 · ENTRY" hint="真正觸發進場，至少一條" value={draft.entry} strategies={strategies} compositeOptions={referenceOptions} excludedCompositeId={editing?.id ?? null} required onChange={value => setGroup("entry", value)} />
      <RuleGroupEditor title="3 · EXIT" hint="策略出場條件；ANY 適合先到先出" value={draft.exit} strategies={strategies} compositeOptions={referenceOptions} excludedCompositeId={editing?.id ?? null} onChange={value => setGroup("exit", value)} />
      <section className="composer-stage risk"><header><div><b>4 · RISK</b><span>必填；永遠用 1 分 K 監控價格風險</span></div></header><div className="composer-risk-grid"><label>停損 <input type="number" min={0.01} max={20} step={0.01} value={draft.risk.stop_loss_pct * 100} onChange={event => setDraft({ ...draft, risk: { ...draft.risk, stop_loss_pct: event.target.valueAsNumber / 100 } })} /> %</label><label>停利 <input type="number" min={0.01} max={50} step={0.01} value={draft.risk.take_profit_pct * 100} onChange={event => setDraft({ ...draft, risk: { ...draft.risk, take_profit_pct: event.target.valueAsNumber / 100 } })} /> %</label><label>最長持有 <input type="number" min={1} max={10080} value={draft.risk.max_holding_minutes} onChange={event => setDraft({ ...draft, risk: { ...draft.risk, max_holding_minutes: event.target.valueAsNumber } })} /> 分</label></div></section>
      <div className="composer-save"><span>{editing ? (renamed ? `名稱已變更；將另存為全新策略 v1，並保留 ${editing.name}` : `儲存後建立 ${editing.name} v${editing.version + 1}`) : "儲存後建立全新組合策略 v1"}</span><button type="button" disabled={saving} onClick={save}>{saving ? "儲存中…" : renamed ? "另存為新策略" : editing ? "儲存為新版本" : "建立策略"}</button></div>
    </div>
  </section>;
}
