export type CurrentUser = {
  role: "researcher" | "trader" | "admin";
  trading_mode: "disabled" | "paper" | "live";
  permissions: string[];
};

export type Account = {
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

export type PaperPosition = {
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

export type PaperOrder = {
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

export type PaperFill = {
  fill_id: string;
  order_id: string;
  strategy_id: string;
  strategy_version: number;
  symbol: string;
  contract: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  commission: number;
  tax: number;
  slippage: number;
  purpose: "entry" | "exit" | "liquidation";
  meta: { occurred_at: string };
};

export type PaperQuote = {
  contract: string;
  close: number;
  received_time: string;
  status: "forming" | "closed";
  session: "day" | "night";
};

export type MarketHealth = {
  service_status: "healthy" | "degraded" | "market_stale" | "provider_disconnected";
  connection_status: string;
  trading_block_reason: "market_stale" | "provider_disconnected" | null;
  stale_after_seconds: number;
  last_tick_time: string | null;
};

export type PaperOverlaySnapshot = {
  positions: PaperPosition[];
  orders: PaperOrder[];
  fills: PaperFill[];
};
