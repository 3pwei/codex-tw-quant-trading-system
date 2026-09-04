"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

type Role = "researcher" | "trader" | "admin";
type Status = "active" | "suspended" | "revoked";
type TradingMode = "disabled" | "paper" | "live";
type User = {
  user_id: string;
  email: string;
  role: Role;
  status: Status;
  trading_mode: TradingMode;
  registered: boolean;
  identity_bound: boolean;
};

export default function UserManagement() {
  const [users, setUsers] = useState<User[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("researcher");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const response = await fetch("/api/admin/users", { cache: "no-store" });
    if (!response.ok) throw new Error("沒有權限讀取帳號，或服務暫時不可用");
    const body: { users: User[] } = await response.json();
    setUsers(body.users);
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => {
      void load().catch(reason => setError(reason instanceof Error ? reason.message : "讀取失敗"));
    }, 0);
    return () => window.clearTimeout(initial);
  }, [load]);

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, role, status: "active", trading_mode: "disabled" }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "新增失敗");
      setEmail(""); setNotice(`已新增 ${body.email}`); await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "新增失敗");
    } finally { setBusy(false); }
  }

  async function updateUser(user: User, changes: Partial<User>) {
    const next = { ...user, ...changes };
    const tradingMode = next.role === "trader" ? next.trading_mode : "disabled";
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`/api/admin/users/${user.user_id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: next.role, status: next.status, trading_mode: tradingMode }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "更新失敗");
      setNotice(`已更新 ${body.email}`); await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新失敗");
    } finally { setBusy(false); }
  }

  return <>
    <form className="admin-user-create panel" onSubmit={createUser}>
      <div><span>新增核准帳號</span><strong>使用者第一次通過 Cloudflare Access 後，系統會綁定其驗證身分。</strong></div>
      <input type="email" required value={email} onChange={event => setEmail(event.target.value)} placeholder="user@example.com" aria-label="Email" />
      <select value={role} onChange={event => setRole(event.target.value as Role)} aria-label="角色">
        <option value="researcher">研究／回測</option><option value="trader">交易者</option><option value="admin">系統管理員</option>
      </select>
      <button disabled={busy}>{busy ? "處理中" : "新增帳號"}</button>
    </form>
    {error && <div className="admin-message error">{error}</div>}
    {notice && <div className="admin-message success">{notice}</div>}
    <section className="admin-users panel">
      <div className="panel-head"><div><span>AUTHORIZED USERS</span><h2>平台帳號</h2></div><small>{users.length} 個帳號</small></div>
      <div className="table-scroll"><table><thead><tr><th>Email</th><th>角色</th><th>帳號狀態</th><th>交易模式</th><th>身分綁定</th></tr></thead><tbody>
        {users.map(user => <tr key={user.user_id}>
          <td>{user.email}</td>
          <td><select disabled={busy} value={user.role} onChange={event => void updateUser(user, { role: event.target.value as Role })}><option value="researcher">研究／回測</option><option value="trader">交易者</option><option value="admin">系統管理員</option></select></td>
          <td><select disabled={busy} value={user.status} onChange={event => void updateUser(user, { status: event.target.value as Status })}><option value="active">啟用</option><option value="suspended">暫停</option><option value="revoked">撤銷</option></select></td>
          <td><select disabled={busy || user.role !== "trader"} value={user.trading_mode} onChange={event => void updateUser(user, { trading_mode: event.target.value as TradingMode })}><option value="disabled">不允許下單</option><option value="paper">模擬下單</option><option value="live">實盤下單</option></select></td>
          <td><span className={user.identity_bound ? "bound" : "pending"}>{user.identity_bound ? "已綁定" : "等待首次登入"}</span></td>
        </tr>)}
      </tbody></table></div>
      <p className="admin-footnote">管理員不具備下單權限；只有 Trader 角色可設定 paper／live。此階段尚未提供任何真實下單 API。</p>
    </section>
  </>;
}
