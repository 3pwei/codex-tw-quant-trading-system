"use client";

import { useEffect, useMemo, useState } from "react";

type Bar = { timestamp: string; open: number; high: number; low: number; close: number; volume: number; contract?: string; session?: string; trading_date?: string };
type Trade = {
  direction: "long" | "short"; entry_time: string; exit_time: string; quantity: number;
  entry_price: number; exit_price: number; gross_pnl: number; commission: number; tax: number;
  total_cost: number; net_pnl: number; return_pct: number; holding_minutes: number;
  mfe: number; mae: number; exit_reason: string; strategy?: string; contract?: string;
  trading_date?: string; stop_loss_price?: number; take_profit_price?: number;
};
type BacktestOptions = { available_start: string | null; available_end: string | null; max_days: number; strategies: { key: string; name: string }[] };
type EquityPoint = { timestamp: string; equity: number; net_pnl: number; peak: number; drawdown: number; drawdown_pct: number };
type DashboardData = {
  metadata: { symbol: string; display_name: string; strategy: string; interval: string; date_range: string; is_synthetic: boolean; source?: string; session_start?: string; session_end?: string };
  config: { initial_capital: number; quantity: number; quantity_unit?: string; opening_range_minutes: number; bar_minutes?: number; stop_loss_pct: number; take_profit_pct: number; force_exit_time: string; commission_rate: number; commission_per_side?: number; sell_tax_rate: number; slippage_bps: number; slippage_points?: number; contract_multiplier?: number };
  summary: Record<string, number | null>;
  bars: Bar[]; trades: Trade[]; equity: EquityPoint[];
};

const money = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 });
const apiBase = () => (process.env.NEXT_PUBLIC_MARKET_API_URL ?? (typeof window === "undefined" ? "" : window.location.origin)).replace(/\/$/, "");
const decimal = new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signedMoney = (n: number) => `${n >= 0 ? "+" : "−"}NT$ ${money.format(Math.abs(n))}`;
const signedPct = (n: number) => `${n >= 0 ? "+" : "−"}${decimal.format(Math.abs(n))}%`;
const hhmm = (s: string) => s.slice(11, 16);
const mmdd = (s: string) => s.slice(5, 10).replace("-", "/");
const reason = (s: string) => ({ stop_loss: "停損", take_profit: "停利", force_exit: "收盤前平倉", end_of_data: "資料結束", mean_reversion: "回歸均線" }[s] ?? s);
const addDays = (iso: string, days: number) => { const d = new Date(`${iso}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); };

function Metric({ label, value, note, tone = "plain" }: { label: string; value: string; note: string; tone?: string }) {
  return <article className={`metric ${tone}`}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>;
}

function CandleChart({ bars, trade, config }: { bars: Bar[]; trade: Trade; config: DashboardData["config"] }) {
  const W = 1100, H = 430, left = 20, right = 86, top = 28, bottom = 42;
  const pw = W - left - right, ph = H - top - bottom;
  const stop = trade.stop_loss_price ?? trade.entry_price * (trade.direction === "long" ? 1 - config.stop_loss_pct : 1 + config.stop_loss_pct);
  const target = trade.take_profit_price ?? trade.entry_price * (trade.direction === "long" ? 1 + config.take_profit_pct : 1 - config.take_profit_pct);
  const rawLow = Math.min(...bars.map(b => b.low), stop, target);
  const rawHigh = Math.max(...bars.map(b => b.high), stop, target);
  const pad = Math.max((rawHigh - rawLow) * .08, rawHigh * .001);
  const low = rawLow - pad, high = rawHigh + pad;
  const x = (i: number) => left + i / Math.max(bars.length - 1, 1) * pw;
  const y = (p: number) => top + (high - p) / Math.max(high - low, .0001) * ph;
  const entryIndex = Math.max(0, bars.findIndex(b => b.timestamp.slice(0, 16) === trade.entry_time.slice(0, 16)));
  const exitIndex = Math.max(0, bars.findIndex(b => b.timestamp.slice(0, 16) === trade.exit_time.slice(0, 16)));
  const cw = Math.max(1.2, Math.min(4, pw / bars.length * .7));

  return <div className="chart-box">
    <div className="legend"><span className="lg-entry">● 進場</span><span className="lg-exit">● 出場</span><span className="lg-stop">┄ 停損</span><span className="lg-target">┄ 停利</span></div>
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="分鐘 K 線與進出場位置">
      <rect width={W} height={H} rx="12" className="chart-bg" />
      <rect x={left} y={top} width={(config.opening_range_minutes / (config.bar_minutes ?? 1)) / Math.max(bars.length - 1, 1) * pw} height={ph} className="opening-zone" />
      {Array.from({ length: 6 }, (_, i) => {
        const p = low + (high - low) * i / 5;
        return <g key={i}><line x1={left} x2={W-right} y1={y(p)} y2={y(p)} className="grid-line" /><text x={W-right+10} y={y(p)+4} className="axis">{decimal.format(p)}</text></g>;
      })}
      {bars.map((b, i) => {
        const up = b.close >= b.open, cx = x(i), bodyTop = y(Math.max(b.open, b.close)), bodyH = Math.max(1, Math.abs(y(b.open)-y(b.close)));
        return <g key={b.timestamp} className={up ? "candle up" : "candle down"}><line x1={cx} x2={cx} y1={y(b.high)} y2={y(b.low)} /><rect x={cx-cw/2} y={bodyTop} width={cw} height={bodyH} /></g>;
      })}
      <line x1={left} x2={W-right} y1={y(stop)} y2={y(stop)} className="risk-line stop" /><text x={left+8} y={y(stop)-7} className="risk-text stop-text">停損 {decimal.format(stop)}</text>
      <line x1={left} x2={W-right} y1={y(target)} y2={y(target)} className="risk-line target" /><text x={left+8} y={y(target)-7} className="risk-text target-text">停利 {decimal.format(target)}</text>
      <g transform={`translate(${x(entryIndex)},${y(trade.entry_price)})`}><path d="M0 -11 L9 7 L-9 7 Z" className="entry-marker" /><text y="-17" textAnchor="middle" className="marker-label">進</text></g>
      <g transform={`translate(${x(exitIndex)},${y(trade.exit_price)})`}><circle r="8" className="exit-marker" /><path d="M-3 -3 L3 3 M3 -3 L-3 3" className="exit-x" /><text y="-15" textAnchor="middle" className="marker-label">出</text></g>
      {[0,.25,.5,.75,1].map(r => { const i = Math.round((bars.length-1)*r); return <text key={r} x={x(i)} y={H-16} textAnchor="middle" className="axis">{hhmm(bars[i].timestamp)}</text>; })}
    </svg>
  </div>;
}

function EquityChart({ points }: { points: EquityPoint[] }) {
  const W=1000,H=250,L=72,R=20,T=18,B=30,pw=W-L-R,ph=H-T-B;
  const min=Math.min(...points.map(p=>p.equity)),max=Math.max(...points.map(p=>p.equity)),pad=Math.max((max-min)*.12,1000),lo=min-pad,hi=max+pad;
  const x=(i:number)=>L+i/Math.max(points.length-1,1)*pw, y=(v:number)=>T+(hi-v)/(hi-lo)*ph;
  const path=points.map((p,i)=>`${i?"L":"M"}${x(i)} ${y(p.equity)}`).join(" ");
  return <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="累積權益曲線">
    <defs><linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#42d6a4" stopOpacity=".25"/><stop offset="100%" stopColor="#42d6a4" stopOpacity="0"/></linearGradient></defs>
    {Array.from({length:4},(_,i)=>{const v=lo+(hi-lo)*i/3;return <g key={i}><line x1={L} x2={W-R} y1={y(v)} y2={y(v)} className="grid-line"/><text x={L-10} y={y(v)+4} textAnchor="end" className="axis">{(v/1e6).toFixed(3)}M</text></g>})}
    <path d={`${path} L${x(points.length-1)} ${H-B} L${x(0)} ${H-B} Z`} fill="url(#eqfill)"/><path d={path} className="equity-line"/>
    {points.map((p,i)=><circle key={p.timestamp} cx={x(i)} cy={y(p.equity)} r="4" className={p.net_pnl>=0?"eq-win":"eq-loss"}/>)}
    {[0,Math.floor((points.length-1)/2),points.length-1].map(i=><text key={i} x={x(i)} y={H-8} textAnchor="middle" className="axis">{mmdd(points[i].timestamp)}</text>)}
  </svg>;
}

export default function Dashboard() {
  const [data,setData]=useState<DashboardData|null>(null),[error,setError]=useState(""),[selected,setSelected]=useState(0),[loading,setLoading]=useState(true);
  const [options,setOptions]=useState<BacktestOptions|null>(null),[strategy,setStrategy]=useState("orb"),[start,setStart]=useState(""),[end,setEnd]=useState("");
  const runBacktest=async(nextStrategy=strategy,nextStart=start,nextEnd=end)=>{
    if(!nextStart||!nextEnd)return;
    const days=(Date.parse(`${nextEnd}T00:00:00Z`)-Date.parse(`${nextStart}T00:00:00Z`))/86400000+1;
    if(days<1||days>(options?.max_days??31)){setError(`回測日期需由早到晚，且最多 ${options?.max_days??31} 天`);return;}
    setLoading(true);setError("");
    try{const q=new URLSearchParams({symbol:"TMF",strategy:nextStrategy,start:nextStart,end:nextEnd});const r=await fetch(`${apiBase()}/api/backtest?${q}`);const body=await r.json();if(!r.ok)throw new Error(body.detail??"回測執行失敗");setData(body);setSelected(0);}catch(e){setError(e instanceof Error?e.message:"回測執行失敗");}finally{setLoading(false);}
  };
  // Initial loading deliberately runs once; later backtests are user-triggered.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(()=>{(async()=>{try{const r=await fetch(`${apiBase()}/api/backtest/options?symbol=TMF`);if(!r.ok)throw new Error("無法取得回測日期範圍");const value:BacktestOptions=await r.json();setOptions(value);if(!value.available_start||!value.available_end)throw new Error("目前尚無已收盤的歷史 1 分 K");const last=value.available_end,first=value.available_start,initialStart=first>addDays(last,-6)?first:addDays(last,-6);setStart(initialStart);setEnd(last);await runBacktest("orb",initialStart,last);}catch(e){setError(e instanceof Error?e.message:"回測資料載入失敗");setLoading(false);}})();},[]);
  const risk=useMemo(()=>{if(!data)return null;const wins=data.trades.filter(t=>t.net_pnl>0),losses=data.trades.filter(t=>t.net_pnl<0),avgWin=wins.reduce((s,t)=>s+t.net_pnl,0)/Math.max(wins.length,1),avgLoss=losses.reduce((s,t)=>s+t.net_pnl,0)/Math.max(losses.length,1);return{best:data.trades.length?data.trades.reduce((a,b)=>b.net_pnl>a.net_pnl?b:a):null,worst:data.trades.length?data.trades.reduce((a,b)=>b.net_pnl<a.net_pnl?b:a):null,payoff:avgWin/Math.max(Math.abs(avgLoss),1),costToNet:Number(data.summary.total_cost)/Math.max(Math.abs(Number(data.summary.net_profit)),1)}} , [data]);
  if(!data&&error)return <main className="state"><div><strong>Dashboard 無法載入</strong><p>{error}</p><button onClick={()=>location.reload()}>重新載入</button></div></main>;
  if(!data||!risk)return <main className="state"><div className="pulse">正在取得回測資料…</div></main>;
  const s=data.summary,t=data.trades[selected],bars=t?.trading_date?data.bars.filter(b=>b.trading_date===t.trading_date):data.bars;
  const wins=data.trades.filter(x=>x.net_pnl>0).length,profitable=Number(s.net_profit)>=0,valueOrNA=(value:number|null|undefined)=>value==null?"N/A":decimal.format(Number(value));
  const maximumEnd=start?[addDays(start,(options?.max_days??31)-1),options?.available_end??"9999-12-31"].sort()[0]:(options?.available_end??undefined);

  return <main className="shell">
    <header className="topbar"><div className="brand"><b>WQ</b><div><span>WADE QUANT LAB · BACKTEST 03</span><h1>微型臺指期貨策略回測</h1></div></div><div className="headmeta"><a className="live-link" href="live/">即時 1 分 K</a><em>● {loading?"回測中":"回測完成"}</em><span>{data.metadata.date_range}</span></div></header>
    <section className="backtest-controls"><label><span>交易策略</span><select value={strategy} onChange={e=>setStrategy(e.target.value)}>{options?.strategies.map(item=><option key={item.key} value={item.key}>{item.name}</option>)}</select></label><label><span>開始日期</span><input type="date" value={start} min={options?.available_start??undefined} max={end||(options?.available_end??undefined)} onChange={e=>setStart(e.target.value)}/></label><label><span>結束日期</span><input type="date" value={end} min={start||(options?.available_start??undefined)} max={maximumEnd} onChange={e=>setEnd(e.target.value)}/></label><button disabled={loading} onClick={()=>runBacktest()}>{loading?"執行中…":"執行回測"}</button><small>已收盤 1 分 K · 單次最多 {options?.max_days??31} 天</small></section>
    {error&&<div className="live-error">{error}</div>}
    <section className="instrument"><div><strong>{data.metadata.symbol}</strong><span>{data.metadata.display_name}</span></div><div className="tags"><span>{data.metadata.strategy}</span><span>{data.metadata.interval}</span><span>每筆 {money.format(data.config.quantity)} {data.config.quantity_unit??"單位"}</span><span className="official">即時資料庫 · 非投資建議</span></div></section>
    <section className="metrics"><Metric label="淨利" value={signedMoney(Number(s.net_profit))} note={`交易成本 NT$ ${money.format(Number(s.total_cost))}`} tone={profitable?"positive":"negative"}/><Metric label="總報酬" value={signedPct(Number(s.return_pct))} note={`期末資產 NT$ ${money.format(Number(s.ending_equity))}`} tone={profitable?"positive":"negative"}/><Metric label="最大回撤" value={`−NT$ ${money.format(Number(s.max_drawdown))}`} note={`${decimal.format(Number(s.max_drawdown_pct))}% of peak`} tone="negative"/><Metric label="勝率" value={`${decimal.format(Number(s.win_rate_pct))}%`} note={`${wins} 勝 / ${data.trades.length-wins} 敗`} tone="positive"/><Metric label="Profit Factor" value={valueOrNA(s.profit_factor)} note={`共 ${data.trades.length} 筆交易`}/><Metric label="日頻 Sharpe" value={valueOrNA(s.daily_sharpe)} note="日數不足時不估計" tone="warning"/></section>
    {t?<section className="panel trade-panel"><div className="panel-head"><div><span>TRADE EXPLORER</span><h2>進出場與價格路徑</h2></div><div className={t.net_pnl>=0?"result profit":"result loss"}><small>第 {selected+1} 筆 · {t.direction==="long"?"做多":"做空"}</small><strong>{signedMoney(t.net_pnl)}</strong></div></div><div className="trade-tabs">{data.trades.map((x,i)=><button key={`${x.entry_time}-${i}`} onClick={()=>setSelected(i)} className={selected===i?"active":""}><span>#{i+1} · {mmdd(x.entry_time)}</span><strong className={x.net_pnl>=0?"profit":"loss"}>{signedMoney(x.net_pnl)}</strong></button>)}</div><CandleChart bars={bars} trade={t} config={data.config}/><div className="execution"><div><span>進場</span><b>{hhmm(t.entry_time)} · {decimal.format(t.entry_price)}</b></div><div><span>出場</span><b>{hhmm(t.exit_time)} · {decimal.format(t.exit_price)}</b></div><div><span>持有</span><b>{money.format(t.holding_minutes)} 分鐘</b></div><div><span>原因</span><b>{reason(t.exit_reason)}</b></div><div><span>停損價</span><b className="loss">{decimal.format(t.stop_loss_price??0)}</b></div><div><span>停利價</span><b className="profit">{decimal.format(t.take_profit_price??0)}</b></div><div><span>最大有利 MFE</span><b className="profit">{signedMoney(t.mfe)}</b></div><div><span>最大不利 MAE</span><b className="loss">{signedMoney(t.mae)}</b></div></div></section>:<section className="panel empty-result"><strong>此區間沒有符合策略條件的交易</strong><p>K 棒已有資料，但策略沒有產生完整的進出場組合。可調整策略或日期後重試。</p></section>}
    <div className="analysis"><section className="panel equity-panel"><div className="panel-head"><div><span>EQUITY CURVE</span><h2>累積權益與虧損節奏</h2></div><strong className={profitable?"profit":"loss"}>{signedPct(Number(s.return_pct))}</strong></div><div className="equity-chart"><EquityChart points={data.equity}/></div></section><aside className="panel risk-panel"><span className="kicker">RISK CHECK</span><h2>承擔風險</h2><div className="risk-score"><div className="risk-ring"><b>{decimal.format(Number(s.max_drawdown_pct))}%</b><small>MAX DD</small></div><div><strong>最多 31 天仍不代表長期績效</strong><p>請跨月份、波動環境與換月週期持續驗證。</p></div></div><dl><div><dt>單筆最大虧損</dt><dd className="loss">{risk.worst?signedMoney(risk.worst.net_pnl):"N/A"}</dd></div><div><dt>單筆最大獲利</dt><dd className="profit">{risk.best?signedMoney(risk.best.net_pnl):"N/A"}</dd></div><div><dt>平均賺賠比</dt><dd>{decimal.format(risk.payoff)}×</dd></div><div><dt>策略停利／停損</dt><dd>{decimal.format(data.config.take_profit_pct/data.config.stop_loss_pct)}×</dd></div><div><dt>成本／淨利</dt><dd className="warning">{decimal.format(risk.costToNet*100)}%</dd></div></dl><div className="alert"><b>成本模型</b><p>每邊 NT$ {money.format(data.config.commission_per_side??0)}、滑價 {data.config.slippage_points??0} 點及期貨交易稅；實盤前請換成實際券商費率。</p></div></aside></div>
    <section className="panel ledger"><div className="panel-head"><div><span>TRADE LEDGER</span><h2>逐筆交易明細</h2></div><small>共 {data.trades.length} 筆</small></div><div className="table-scroll"><table><thead><tr><th>#</th><th>交易日</th><th>策略</th><th>方向</th><th>進場</th><th>出場</th><th>停損／停利</th><th>淨損益</th><th>原因</th></tr></thead><tbody>{data.trades.map((x,i)=><tr key={`${x.entry_time}-${i}`} onClick={()=>setSelected(i)} className={selected===i?"selected":""}><td>{i+1}</td><td>{x.trading_date??x.entry_time.slice(0,10)}</td><td>{x.strategy?.toUpperCase()}</td><td><i className={`dir ${x.direction}`}>{x.direction==="long"?"多":"空"}</i></td><td>{hhmm(x.entry_time)}<small>{decimal.format(x.entry_price)}</small></td><td>{hhmm(x.exit_time)}<small>{decimal.format(x.exit_price)}</small></td><td><span className="loss">{decimal.format(x.stop_loss_price??0)}</span><small className="profit">{decimal.format(x.take_profit_price??0)}</small></td><td className={x.net_pnl>=0?"profit":"loss"}><b>{signedMoney(x.net_pnl)}</b></td><td>{reason(x.exit_reason)}</td></tr>)}</tbody></table></div></section>
    <footer><p>資料來源：{data.metadata.source??"回測資料"}。回測與即時頁共用同一套 ORB／BNF 訊號核心；結果不代表未來績效。</p><p>停損 {data.config.stop_loss_pct*100}% · 停利 {data.config.take_profit_pct*100}% · 每點 NT$ {data.config.contract_multiplier} · {data.config.force_exit_time}</p></footer>
  </main>;
}
