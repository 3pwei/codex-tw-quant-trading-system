"use client";

import { useEffect, useMemo, useState } from "react";

type ParameterField = { label: string; kind: "integer" | "number" | "percent"; unit: string; min: number; max: number; step: number; value: number };
type AtomicStrategy = { key: string; name: string; parameters: Record<string, ParameterField> };
type Rule = { id?: string; strategy: string; interval: string; parameters?: Record<string, number> };
type Group = { operator: "all" | "any"; confirmation_window_minutes: number; rules: Rule[] };
type Definition = {
  name: string; description: string; enabled: boolean; direction: "both" | "long" | "short";
  setup: Group; entry: Group; exit: Group;
  risk: { monitor_interval: "1m"; stop_loss_pct: number; take_profit_pct: number; max_holding_minutes: number };
};
type SavedComposite = { id: string; version: number; name: string; definition: Definition; created_at: string };
type ArchivedComposite = SavedComposite & { archived_at: string };

const intervals = ["1m", "5m", "10m", "15m", "30m", "1h", "1d", "1w"];
const intervalNames: Record<string, string> = { "1m":"1 分", "5m":"5 分", "10m":"10 分", "15m":"15 分", "30m":"30 分", "1h":"1 小時", "1d":"日 K", "1w":"週 K" };
const apiBase = () => (process.env.NEXT_PUBLIC_MARKET_API_URL ?? (typeof window === "undefined" ? "" : window.location.origin)).replace(/\/$/, "");
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

function RuleGroupEditor({ title, hint, value, strategies, required, onChange }: {
  title: string; hint: string; value: Group; strategies: AtomicStrategy[]; required?: boolean; onChange: (next: Group) => void;
}) {
  const updateRule = (index: number, patch: Partial<Rule>) => onChange({ ...value, rules: value.rules.map((rule, i) => i === index ? { ...rule, ...patch } : rule) });
  return <section className="composer-stage">
    <header><div><b>{title}</b><span>{hint}</span></div><div className="composer-logic"><select aria-label={`${title}組合邏輯`} value={value.operator} onChange={event => onChange({ ...value, operator: event.target.value as "all" | "any" })}><option value="all">全部符合 ALL</option><option value="any">任一符合 ANY</option></select><label>確認視窗 <input type="number" min={1} max={1440} value={value.confirmation_window_minutes} onChange={event => onChange({ ...value, confirmation_window_minutes: event.target.valueAsNumber })} /> 分</label></div></header>
    <div className="composer-rules">
      {value.rules.map((rule, index) => {
        const atomic = strategies.find(strategy => strategy.key === rule.strategy);
        return <div className="composer-rule-wrap" key={`${title}-${index}`}><div className="composer-rule">
          <span>{index + 1}</span>
          <select aria-label={`${title}規則 ${index + 1} 策略`} value={rule.strategy} onChange={event => updateRule(index, { strategy: event.target.value, parameters: {} })}>{strategies.map(strategy => <option value={strategy.key} key={strategy.key}>{strategy.name}</option>)}</select>
          <select aria-label={`${title}規則 ${index + 1} 週期`} value={rule.interval} onChange={event => updateRule(index, { interval: event.target.value })}>{intervals.map(interval => <option value={interval} key={interval}>{intervalNames[interval]}</option>)}</select>
          <button type="button" className="danger" disabled={required && value.rules.length === 1} onClick={() => onChange({ ...value, rules: value.rules.filter((_, i) => i !== index) })}>移除</button>
        </div>{atomic && <details className="rule-parameters"><summary>覆寫此積木參數（未修改則繼承基本策略）</summary><div>{Object.entries(atomic.parameters).map(([name, field]) => {
          const scale = field.kind === "percent" ? 100 : 1;
          const stored = rule.parameters?.[name] ?? field.value;
          return <label key={name}><span>{field.label}<small>{field.unit}</small></span><input type="number" min={field.min * scale} max={field.max * scale} step={field.step * scale} value={stored * scale} onChange={event => updateRule(index, { parameters: { ...rule.parameters, [name]: event.target.valueAsNumber / scale } })} /></label>;
        })}</div></details>}</div>;
      })}
      {!value.rules.length && <p>未設定規則，此階段視為直接通過。</p>}
    </div>
    <button type="button" className="secondary add-rule" onClick={() => onChange({ ...value, rules: [...value.rules, { strategy: strategies[0]?.key ?? "orb", interval: "1m" }] })}>＋ 新增條件</button>
  </section>;
}

export default function CompositeBuilder({ strategies }: { strategies: AtomicStrategy[] }) {
  const [template, setTemplate] = useState<Definition | null>(null);
  const [saved, setSaved] = useState<SavedComposite[]>([]);
  const [archivedSaved, setArchivedSaved] = useState<ArchivedComposite[]>([]);
  const [selectedArchived, setSelectedArchived] = useState<string[]>([]);
  const [historyId, setHistoryId] = useState<string | null>(null);
  const [history, setHistory] = useState<SavedComposite[]>([]);
  const [draft, setDraft] = useState<Definition | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [archiving, setArchiving] = useState("");
  const [purging, setPurging] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    const response = await fetch(`${apiBase()}/api/composite-strategies`, { cache: "no-store" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail ?? "無法取得組合策略");
    setTemplate(body.template); setSaved(body.strategies); setArchivedSaved(body.archived_strategies ?? []);
    setDraft(current => current ?? copy(body.template));
  };
  // Initial remote hydration runs once; later refreshes are user-triggered.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load().catch(reason => setError(reason instanceof Error ? reason.message : "無法取得組合策略")); }, []);
  const editing = useMemo(() => saved.find(item => item.id === editingId), [saved, editingId]);

  if (!draft) return <section className="panel strategy-loading">正在讀取組合策略…</section>;
  const setGroup = (key: "setup" | "entry" | "exit", value: Group) => setDraft({ ...draft, [key]: value });
  const startNew = () => { if (template) setDraft(copy(template)); setEditingId(null); setNotice(""); setError(""); };
  const edit = (item: SavedComposite) => { setDraft(copy(item.definition)); setEditingId(item.id); setNotice(`正在編輯 ${item.name} v${item.version}；儲存後會建立新版本。`); setError(""); };
  const copyVersion = (item: SavedComposite) => { setDraft(copy(item.definition)); setEditingId(null); setNotice(`已載入 ${item.name} v${item.version}；儲存後會建立一個全新策略，不會修改原版本。`); setError(""); };
  const toggleHistory = async (item: SavedComposite) => {
    if (historyId === item.id) { setHistoryId(null); setHistory([]); return; }
    setError("");
    try {
      const response = await fetch(`${apiBase()}/api/composite-strategies/${item.id}/versions`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "無法取得策略版本");
      setHistoryId(item.id); setHistory(body.versions);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "無法取得策略版本"); }
  };
  const save = async () => {
    setSaving(true); setError(""); setNotice("");
    try {
      const renamed = Boolean(editing && draft.name.trim() !== editing.name);
      const url = editingId ? `${apiBase()}/api/composite-strategies/${editingId}` : `${apiBase()}/api/composite-strategies`;
      const response = await fetch(url, { method: editingId ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ definition: draft }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "組合策略儲存失敗");
      setEditingId(body.id); await load();
      setNotice(renamed
        ? `${body.name} 已另存為全新策略 v1；原策略仍保留。`
        : `${body.name} v${body.version} 已儲存，可到歷史回測選取。`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "組合策略儲存失敗"); }
    finally { setSaving(false); }
  };
  const archive = async (item: SavedComposite) => {
    if (!window.confirm(`確定刪除「${item.name}」？\n\n策略會從清單與回測選單移除，但 v1～v${item.version} 會保留供歷史追溯。`)) return;
    setArchiving(item.id); setError(""); setNotice("");
    try {
      const response = await fetch(`${apiBase()}/api/composite-strategies/${item.id}`, { method: "DELETE" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "策略刪除失敗");
      if (editingId === item.id) startNew();
      await load(); setNotice(`${item.name} 已刪除；既有版本仍保留供歷史追溯。`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "策略刪除失敗"); }
    finally { setArchiving(""); }
  };
  const toggleArchived = (strategyId: string) => setSelectedArchived(current => current.includes(strategyId) ? current.filter(item => item !== strategyId) : [...current, strategyId]);
  const purgeArchived = async () => {
    if (!selectedArchived.length) return;
    const selected = archivedSaved.filter(item => selectedArchived.includes(item.id));
    const versionCount = selected.reduce((total, item) => total + item.version, 0);
    const confirmation = window.prompt(`將永久刪除 ${selected.length} 個策略及 ${versionCount} 個版本。\n此操作無法復原。\n\n請輸入「永久刪除」確認：`);
    if (confirmation !== "永久刪除") return;
    setPurging(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${apiBase()}/api/composite-strategies/purge`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ strategy_ids: selectedArchived }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "永久刪除失敗");
      if (historyId && selectedArchived.includes(historyId)) { setHistoryId(null); setHistory([]); }
      setSelectedArchived([]); await load(); setNotice(`已永久刪除 ${body.deleted_strategies} 個策略及 ${body.deleted_versions} 個版本。`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "永久刪除失敗"); }
    finally { setPurging(false); }
  };

  const strategyItem = (item: SavedComposite, isArchived = false) => <div className={`composite-list-item-wrap ${editingId === item.id ? "active" : ""}`} key={`${isArchived ? "archived" : "active"}-${item.id}`}>
    <div className="composite-list-item">
      {isArchived && <label className="archive-select" title="選取以永久刪除"><input aria-label={`選取 ${item.name}`} type="checkbox" checked={selectedArchived.includes(item.id)} onChange={() => toggleArchived(item.id)} /></label>}
      <button type="button" className="select-composite" onClick={() => isArchived ? copyVersion(item) : edit(item)}><strong>{item.name}</strong><span>最新版 v{item.version} · {item.definition.direction === "both" ? "多空" : item.definition.direction === "long" ? "只做多" : "只做空"}</span></button>
      {!isArchived && <button type="button" className="archive-composite" disabled={archiving === item.id} aria-label={`刪除 ${item.name}`} onClick={() => void archive(item)}>{archiving === item.id ? "…" : "刪除"}</button>}
    </div>
    <button type="button" className="version-toggle" onClick={() => void toggleHistory(item)}>{historyId === item.id ? "收合版本紀錄" : `查看 v1–v${item.version}`}</button>
    {historyId === item.id && <div className="version-history">{history.map(version => <button type="button" key={version.version} onClick={() => copyVersion(version)}><b>v{version.version}</b><span>{new Date(version.created_at).toLocaleString("zh-TW", { timeZone: "Asia/Taipei" })}</span><em>複製</em></button>)}</div>}
  </div>;

  return <section className="composite-area">
    <div className="composite-title"><div><span>NO-CODE COMPOSER</span><h2>多週期策略組合器</h2><p>將基本策略當成條件積木；策略邏輯仍由後端共用核心執行，不產生任意程式碼。</p></div><button type="button" className="secondary" onClick={startNew}>＋ 新策略</button></div>
    {error && <div className="live-error">{error}</div>}{notice && <div className="strategy-notice">{notice}</div>}
    <div className="composite-layout">
      <aside className="panel composite-list"><b>可用策略</b>{saved.length ? saved.map(item => strategyItem(item)) : <p>尚未建立組合策略。</p>}{archivedSaved.length > 0 && <details className="archived-composites"><summary>封存庫（{archivedSaved.length}）</summary><div className="archive-actions"><button type="button" onClick={() => setSelectedArchived(selectedArchived.length === archivedSaved.length ? [] : archivedSaved.map(item => item.id))}>{selectedArchived.length === archivedSaved.length ? "取消全選" : "全選"}</button><button type="button" className="purge-selected" disabled={!selectedArchived.length || purging} onClick={() => void purgeArchived()}>{purging ? "刪除中…" : `永久刪除（${selectedArchived.length}）`}</button></div>{archivedSaved.map(item => strategyItem(item, true))}</details>}</aside>
      <div className="panel composite-editor">
        <div className="composer-basics"><label><span>策略名稱</span><input value={draft.name} maxLength={80} onChange={event => setDraft({ ...draft, name: event.target.value })} /></label><label><span>交易方向</span><select value={draft.direction} onChange={event => setDraft({ ...draft, direction: event.target.value as Definition["direction"] })}><option value="both">多空皆可</option><option value="long">只做多</option><option value="short">只做空</option></select></label><label className="wide"><span>策略說明</span><input value={draft.description} maxLength={500} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label></div>
        <RuleGroupEditor title="1 · SETUP" hint="高週期背景／啟動條件，可留空" value={draft.setup} strategies={strategies} onChange={value => setGroup("setup", value)} />
        <RuleGroupEditor title="2 · ENTRY" hint="真正觸發進場，至少一條" value={draft.entry} strategies={strategies} required onChange={value => setGroup("entry", value)} />
        <RuleGroupEditor title="3 · EXIT" hint="策略出場條件；ANY 適合先到先出" value={draft.exit} strategies={strategies} onChange={value => setGroup("exit", value)} />
        <section className="composer-stage risk"><header><div><b>4 · RISK</b><span>必填；永遠用 1 分 K 監控價格風險</span></div></header><div className="composer-risk-grid"><label>停損 <input type="number" min={0.01} max={20} step={0.01} value={draft.risk.stop_loss_pct * 100} onChange={event => setDraft({ ...draft, risk: { ...draft.risk, stop_loss_pct: event.target.valueAsNumber / 100 } })} /> %</label><label>停利 <input type="number" min={0.01} max={50} step={0.01} value={draft.risk.take_profit_pct * 100} onChange={event => setDraft({ ...draft, risk: { ...draft.risk, take_profit_pct: event.target.valueAsNumber / 100 } })} /> %</label><label>最長持有 <input type="number" min={1} max={10080} value={draft.risk.max_holding_minutes} onChange={event => setDraft({ ...draft, risk: { ...draft.risk, max_holding_minutes: event.target.valueAsNumber } })} /> 分</label></div></section>
        <div className="composer-save"><span>{editing ? (draft.name.trim() !== editing.name ? `名稱已變更；將另存為全新策略 v1，並保留 ${editing.name}` : `基於 ${editing.name} v${editing.version} 建立下一版`) : "建立全新組合策略 v1"}</span><button type="button" disabled={saving} onClick={save}>{saving ? "儲存中…" : editingId ? (editing && draft.name.trim() !== editing.name ? "另存為新策略" : "儲存為新版本") : "建立策略"}</button></div>
      </div>
    </div>
  </section>;
}
