"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type CurrentUser = {
  role: "trader" | "admin";
  trading_mode: "disabled" | "paper" | "live";
};
type Account = {
  realized_pnl: number;
  open_contracts: number;
  reserved_contracts: number;
  trades: number;
  kill_switch_active: boolean;
  kill_switch_reason: string | null;
  cooldown_until: string | null;
  recovery_status: "healthy" | "degraded";
  recovery_issues: string[];
};
type Position = {
  strategy_id: string;
  strategy_version: number;
  symbol: string;
  contract: string;
  quantity: number;
  average_price: number;
  opened_at: string | null;
  realized_pnl: number;
  unrealized_pnl: number;
  total_cost: number;
};
type Order = {
  order_id: string;
  submitted_at: string;
  strategy_id: string;
  strategy_version: number;
  contract: string;
  side: "buy" | "sell";
  quantity: number;
  reduce_only: boolean;
  reference_price: number;
  stop_loss_price: number | null;
  status: "pending_risk" | "approved" | "rejected" | "filled";
  status_reason: string;
};
type Fill = {
  fill_id: string;
  order_id: string;
  contract: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  commission: number;
  tax: number;
  slippage: number;
  meta: { occurred_at: string };
};
type Quote = {
  contract: string;
  close: number;
  received_time: string;
  status: "forming" | "closed";
  session: "day" | "night";
};
type MarketHealth = {
  service_status: "healthy" | "degraded" | "market_stale" | "provider_disconnected";
  connection_status: string;
  trading_block_reason: "market_stale" | "provider_disconnected" | null;
  stale_after_seconds: number;
  last_tick_time: string | null;
};

const apiBase = () => (process.env.NEXT_PUBLIC_MARKET_API_URL
  ?? (typeof window === "undefined" ? "" : window.location.origin)).replace(/\/$/, "");
const price = new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const money = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 });
const time = (value: string | null) => value
  ? new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false })
  : "—";
const signedMoney = (value: number) => `${value >= 0 ? "+" : "−"}NT$ ${money.format(Math.abs(value))}`;
const reasonLabels: Record<string, string> = {
  approved: "風控通過",
  simulated_fill: "模擬成交",
  kill_switch_active: "Kill Switch 已啟用",
  max_position_exceeded: "超過最大持倉",
  max_trade_risk_exceeded: "超過單筆風險",
  stop_loss_required: "缺少停損",
  invalid_stop_direction: "停損方向錯誤",
  paper_mode_required: "尚未啟用 Paper",
  risk_reducing_approved: "減倉核准",
};
const reasonLabel = (value: string) => reasonLabels[value] ?? value;

async function responseBody(response: Response) {
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail ?? `API 錯誤 (${response.status})`);
  return body;
}

export default function PaperTradingDashboard() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [fills, setFills] = useState<Fill[]>([]);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteFresh, setQuoteFresh] = useState(false);
  const [marketHealth, setMarketHealth] = useState<MarketHealth | null>(null);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [quantity, setQuantity] = useState(1);
  const [stopLoss, setStopLoss] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (silent = false) => {
    try {
      const [meResponse, accountResponse, ordersResponse, fillsResponse, quoteResponse, healthResponse] = await Promise.all([
        fetch(`${apiBase()}/api/me`, { cache: "no-store" }),
        fetch(`${apiBase()}/api/paper/account`, { cache: "no-store" }),
        fetch(`${apiBase()}/api/paper/orders`, { cache: "no-store" }),
        fetch(`${apiBase()}/api/paper/fills?limit=100`, { cache: "no-store" }),
        fetch(`${apiBase()}/api/kbars?symbol=TMF&interval=1m&limit=1`, { cache: "no-store" }),
        fetch(`${apiBase()}/api/health`, { cache: "no-store" }),
      ]);
      const [me, accountBody, ordersBody, fillsBody, quotes, health] = await Promise.all([
        responseBody(meResponse), responseBody(accountResponse),
        responseBody(ordersResponse), responseBody(fillsResponse), responseBody(quoteResponse),
        responseBody(healthResponse),
      ]);
      const latestQuote: Quote | null = quotes.at(-1) ?? null;
      setUser(me); setAccount(accountBody.account); setPositions(accountBody.positions);
      setOrders(ordersBody.orders); setFills(fillsBody.fills); setQuote(latestQuote); setMarketHealth(health);
      setQuoteFresh(Boolean(latestQuote && Date.now() - new Date(latestQuote.received_time).getTime() <= health.stale_after_seconds * 1_000));
      if (latestQuote) setStopLoss(current => current || String(latestQuote.close - 50));
      if (!silent) setError("");
    } catch (reason) {
      if (!silent) setError(reason instanceof Error ? reason.message : "無法載入模擬帳戶");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => void load(true), 5_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [load]);

  const totalUnrealized = useMemo(
    () => positions.reduce((sum, position) => sum + position.unrealized_pnl, 0),
    [positions],
  );
  const paperEnabled = user?.trading_mode === "paper";
  const marketBlockReason = marketHealth?.trading_block_reason ?? null;
  const marketBlockCopy = marketBlockReason === "provider_disconnected"
    ? { code: "PROVIDER DISCONNECTED", title: "行情供應商連線中斷", detail: "系統已禁止建立新倉；既有持倉仍可查看。連線恢復後也不會自動補送中斷期間的委託。" }
    : marketBlockReason === "market_stale"
      ? { code: "MARKET STALE", title: "行情已停止更新", detail: `最新 Tick：${time(marketHealth?.last_tick_time ?? null)}。系統已禁止建立新倉，恢復後請重新確認價格再送單。` }
      : null;

  async function submitOrder(event: FormEvent) {
    event.preventDefault();
    setBusy("order"); setError(""); setNotice("");
    try {
      const response = await fetch(`${apiBase()}/api/paper/orders`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ side, quantity, stop_loss_price: Number(stopLoss) }),
      });
      const body = await responseBody(response);
      setNotice(body.order.status === "filled"
        ? `模擬${side === "buy" ? "買進" : "賣出"} ${quantity} 口已成交`
        : `委託未成交：${reasonLabel(body.order.status_reason)}`);
      await load(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "模擬委託失敗");
    } finally { setBusy(""); }
  }

  async function closePosition(position: Position) {
    if (!window.confirm(`確定以最新行情平倉 ${position.contract} ${Math.abs(position.quantity)} 口？`)) return;
    setBusy(`close:${position.contract}`); setError(""); setNotice("");
    try {
      const response = await fetch(`${apiBase()}/api/paper/orders`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          strategy_id: position.strategy_id, strategy_version: position.strategy_version,
          side: position.quantity > 0 ? "sell" : "buy",
          quantity: Math.abs(position.quantity), reduce_only: true,
        }),
      });
      const body = await responseBody(response);
      if (body.order.status !== "filled") throw new Error(reasonLabel(body.order.status_reason));
      setNotice(`${position.contract} 已完成模擬平倉`); await load(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "平倉失敗");
    } finally { setBusy(""); }
  }

  async function control(action: "activate" | "reset") {
    if (action === "activate" && !window.confirm("啟用 Kill Switch 後將阻止所有新增曝險，確定繼續？")) return;
    setBusy("control"); setError(""); setNotice("");
    try {
      const endpoint = action === "activate" ? "/api/paper/kill-switch" : "/api/paper/kill-switch/reset";
      await responseBody(await fetch(`${apiBase()}${endpoint}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: action === "activate" ? "manual_ui_stop" : "manual_ui_resume" }),
      }));
      setNotice(action === "activate" ? "Kill Switch 已啟用" : "Kill Switch 已解除");
      await load(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "控制操作失敗");
    } finally { setBusy(""); }
  }

  if (loading) return <section className="panel paper-loading">正在讀取模擬帳戶…</section>;

  return <div className="paper-page">
    {!paperEnabled && <section className="paper-mode-warning panel">
      <div><span>PAPER MODE DISABLED</span><h2>帳號尚未啟用模擬下單</h2><p>可以查看帳戶狀態，但送單按鈕維持停用。</p></div>
      {user?.role === "admin" && <Link href="/admin/users/">前往帳號權限啟用 Paper →</Link>}
    </section>}
    {account?.recovery_status === "degraded" && <section className="paper-mode-warning panel">
      <div><span>RECOVERY LOCK</span><h2>帳戶狀態需要檢查</h2><p>重啟復原發現資料不一致，系統已禁止新增曝險；既有部位仍可平倉。</p></div>
      {user?.role === "admin" && <Link href="/settings/">查看系統健康狀態 →</Link>}
    </section>}
    {marketBlockCopy && <section className="paper-mode-warning market-interruption panel">
      <div><span>{marketBlockCopy.code}</span><h2>{marketBlockCopy.title}</h2><p>{marketBlockCopy.detail}</p></div>
      {user?.role === "admin" && <Link href="/settings/">查看系統健康狀態 →</Link>}
    </section>}
    {error && <div className="paper-message error">{error}</div>}
    {notice && <div className="paper-message success">{notice}</div>}

    <section className="paper-metrics">
      <article><span>最新模擬報價</span><strong>{quote ? price.format(quote.close) : "—"}</strong><small>{quote?.contract ?? "等待行情"} · {quoteFresh ? "即時" : "已過期"}</small></article>
      <article><span>未實現損益</span><strong className={totalUnrealized >= 0 ? "profit" : "loss"}>{signedMoney(totalUnrealized)}</strong><small>{account?.open_contracts ?? 0} 口未平倉</small></article>
      <article><span>已實現損益</span><strong className={(account?.realized_pnl ?? 0) >= 0 ? "profit" : "loss"}>{signedMoney(account?.realized_pnl ?? 0)}</strong><small>今日成交 {account?.trades ?? 0} 筆</small></article>
      <article><span>風控狀態</span><strong className={account?.kill_switch_active ? "loss" : "profit"}>{account?.kill_switch_active ? "已停止" : "可交易"}</strong><small>{account?.kill_switch_reason ? reasonLabel(account.kill_switch_reason) : "風控閘門正常"}</small></article>
    </section>

    <div className="paper-primary">
      <form className="paper-order panel" onSubmit={submitOrder}>
        <div className="panel-head"><div><span>MANUAL ORDER</span><h2>模擬市價單</h2></div><small>成交價由伺服器決定</small></div>
        <div className="paper-side">
          <button type="button" className={side === "buy" ? "active buy" : ""} onClick={() => { setSide("buy"); if (quote) setStopLoss(String(quote.close - 50)); }}>買進／做多</button>
          <button type="button" className={side === "sell" ? "active sell" : ""} onClick={() => { setSide("sell"); if (quote) setStopLoss(String(quote.close + 50)); }}>賣出／做空</button>
        </div>
        <label><span>數量</span><select value={quantity} onChange={event => setQuantity(Number(event.target.value))}><option value={1}>1 口</option><option value={2}>2 口</option></select></label>
        <label><span>停損價</span><input required min="1" step="1" inputMode="decimal" value={stopLoss} onChange={event => setStopLoss(event.target.value)} /></label>
        <p>單筆風險與最大持倉仍由後端再次檢查。行情超過系統容許延遲時不會成交。</p>
        <button className={`paper-submit ${side}`} disabled={!paperEnabled || !quoteFresh || Boolean(marketBlockReason) || account?.kill_switch_active || Boolean(busy)}>{busy === "order" ? "送單中…" : `送出模擬${side === "buy" ? "買單" : "賣單"}`}</button>
      </form>

      <section className="paper-risk panel">
        <div className="panel-head"><div><span>ACCOUNT CONTROL</span><h2>Kill Switch</h2></div><small>只阻止新增曝險</small></div>
        <strong className={account?.kill_switch_active ? "loss" : "profit"}>{account?.kill_switch_active ? "ACTIVE" : "READY"}</strong>
        <p>{account?.kill_switch_active ? `原因：${reasonLabel(account.kill_switch_reason ?? "manual")}` : "發生異常時可立即停止所有新的模擬進場；既有部位仍可平倉。"}</p>
        {account?.kill_switch_active
          ? <button className="reset" disabled={Boolean(busy)} onClick={() => void control("reset")}>解除 Kill Switch</button>
          : <button className="activate" disabled={Boolean(busy) || !paperEnabled} onClick={() => void control("activate")}>啟用 Kill Switch</button>}
      </section>
    </div>

    <section className="paper-positions panel">
      <div className="panel-head"><div><span>OPEN POSITIONS</span><h2>目前持倉</h2></div><small>{positions.length} 筆</small></div>
      <div className="table-scroll"><table><thead><tr><th>契約</th><th>方向／口數</th><th>均價</th><th>未實現損益</th><th>建立時間</th><th></th></tr></thead><tbody>
        {positions.map(position => <tr key={`${position.strategy_id}:${position.contract}`}><td><b>{position.contract}</b><small>{position.strategy_id} · v{position.strategy_version}</small></td><td><i className={`dir ${position.quantity > 0 ? "long" : "short"}`}>{position.quantity > 0 ? "多" : "空"}</i> {Math.abs(position.quantity)} 口</td><td>{price.format(position.average_price)}</td><td className={position.unrealized_pnl >= 0 ? "profit" : "loss"}><b>{signedMoney(position.unrealized_pnl)}</b></td><td>{time(position.opened_at)}</td><td><button disabled={Boolean(busy) || !quoteFresh} onClick={() => void closePosition(position)}>{busy === `close:${position.contract}` ? "平倉中…" : "全部平倉"}</button></td></tr>)}
      </tbody></table>{!positions.length && <p className="paper-empty">目前沒有模擬持倉。</p>}</div>
    </section>

    <div className="paper-records">
      <section className="panel"><div className="panel-head"><div><span>ORDER LOG</span><h2>最近委託</h2></div><small>{orders.length} 筆</small></div><div className="paper-record-list">{orders.slice(0, 20).map(order => <article key={order.order_id}><div><b>{order.side === "buy" ? "買進" : "賣出"} {order.quantity} 口</b><span className={order.status}>{order.status === "filled" ? "已成交" : order.status === "rejected" ? "已拒絕" : "處理中"}</span></div><strong>{order.contract} · {price.format(order.reference_price)}</strong><small>{time(order.submitted_at)} · {reasonLabel(order.status_reason)}</small></article>)}{!orders.length && <p className="paper-empty">尚無委託紀錄。</p>}</div></section>
      <section className="panel"><div className="panel-head"><div><span>FILL LOG</span><h2>最近成交</h2></div><small>{fills.length} 筆</small></div><div className="paper-record-list">{fills.slice(0, 20).map(fill => <article key={fill.fill_id}><div><b>{fill.side === "buy" ? "買進" : "賣出"} {fill.quantity} 口</b><span className="filled">已成交</span></div><strong>{fill.contract} · {price.format(fill.price)}</strong><small>{time(fill.meta.occurred_at)} · 成本 NT$ {money.format(fill.commission + fill.tax)} · 滑價 {price.format(fill.slippage)} 點</small></article>)}{!fills.length && <p className="paper-empty">尚無成交紀錄。</p>}</div></section>
    </div>
  </div>;
}
