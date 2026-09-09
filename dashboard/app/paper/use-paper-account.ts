import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "../lib/api-client";
import type {
  Account,
  CurrentUser,
  PaperFill,
  PaperOrder,
  PaperOverlaySnapshot,
  PaperPosition,
} from "./paper-trading-dashboard";

type PaperAccountResponse = {
  account: Account;
  positions: PaperPosition[];
};

type PaperOrdersResponse = { orders: PaperOrder[] };
type PaperFillsResponse = { fills: PaperFill[] };

export function usePaperAccount(
  onOverlayChange: (snapshot: PaperOverlaySnapshot) => void,
) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [orders, setOrders] = useState<PaperOrder[]>([]);
  const [fills, setFills] = useState<PaperFill[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (silent = false) => {
    try {
      const me = await apiRequest<CurrentUser>(
        "/api/me",
        { cache: "no-store" },
        "API 錯誤",
      );
      setUser(me);
      if (!me.permissions.includes("positions.read.own")) {
        setAccount(null);
        setPositions([]);
        setOrders([]);
        setFills([]);
        onOverlayChange({ positions: [], orders: [], fills: [] });
        if (!silent) setError("");
        return;
      }
      const [accountBody, ordersBody, fillsBody] = await Promise.all([
        apiRequest<PaperAccountResponse>(
          "/api/paper/account",
          { cache: "no-store" },
          "API 錯誤",
        ),
        apiRequest<PaperOrdersResponse>(
          "/api/paper/orders",
          { cache: "no-store" },
          "API 錯誤",
        ),
        apiRequest<PaperFillsResponse>(
          "/api/paper/fills?limit=100",
          { cache: "no-store" },
          "API 錯誤",
        ),
      ]);
      setAccount(accountBody.account);
      setPositions(accountBody.positions);
      setOrders(ordersBody.orders);
      setFills(fillsBody.fills);
      onOverlayChange({
        positions: accountBody.positions,
        orders: ordersBody.orders,
        fills: fillsBody.fills,
      });
      if (!silent) setError("");
    } catch (reason) {
      if (!silent) {
        setError(reason instanceof Error ? reason.message : "無法載入模擬帳戶");
      }
    } finally {
      setLoading(false);
    }
  }, [onOverlayChange]);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => void load(true), 5_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [load]);

  return {
    user,
    account,
    positions,
    orders,
    fills,
    error,
    setError,
    loading,
    load,
  };
}
