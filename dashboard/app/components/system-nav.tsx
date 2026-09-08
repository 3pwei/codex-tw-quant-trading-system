"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

export type SystemRoute =
  | "/"
  | "/trade/"
  | "/backtest/"
  | "/replay/"
  | "/history/"
  | "/strategies/"
  | "/composite-strategies/"
  | "/settings/"
  | "/admin/users/";

type SystemNavRoute = {
  href: SystemRoute;
  label: string;
  permission?: string;
  admin?: boolean;
};

const routes: readonly SystemNavRoute[] = [
  { href: "/", label: "總覽" },
  { href: "/trade/", label: "交易工作台" },
  { href: "/backtest/", label: "歷史回測" },
  { href: "/replay/", label: "行情回放" },
  { href: "/history/", label: "執行紀錄" },
  { href: "/strategies/", label: "基本策略" },
  { href: "/composite-strategies/", label: "組合策略" },
  { href: "/settings/", label: "系統設定", admin: true },
  { href: "/admin/users/", label: "帳號權限", admin: true },
];

type CurrentUser = {
  email: string;
  role: string;
  permissions: string[];
};

export default function SystemNav({ active }: { active: SystemRoute }) {
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    let activeRequest = true;
    fetch("/api/me", { cache: "no-store" })
      .then(response => response.ok ? response.json() : Promise.reject())
      .then(user => { if (activeRequest) setCurrentUser(user); })
      .catch(() => { if (activeRequest) setCurrentUser(null); });
    return () => { activeRequest = false; };
  }, []);

  return (
    <nav className="system-nav" aria-label="系統功能">
      <div className="system-nav-routes">
        {routes.filter(route =>
          (!route.admin || currentUser?.role === "admin")
          && (!route.permission
            || currentUser?.permissions.includes(route.permission))
        ).map(route => (
          <Link
          key={route.href}
          href={route.href}
          className={route.href === active ? "active" : undefined}
          aria-current={route.href === active ? "page" : undefined}
        >
          {route.label}
          </Link>
        ))}
      </div>
      <div className="system-account">
        {currentUser && (
          <span title={currentUser.email}>
            {currentUser.email}
            <small>{currentUser.role}</small>
          </span>
        )}
        <a className="logout-link" href="/cdn-cgi/access/logout">
          登出
        </a>
      </div>
      <details className="system-account-mobile">
        <summary aria-label="開啟帳號選單">帳號</summary>
        <div className="system-account-menu">
          {currentUser && (
            <span title={currentUser.email}>
              {currentUser.email}
              <small>{currentUser.role}</small>
            </span>
          )}
          <a className="logout-link" href="/cdn-cgi/access/logout">
            登出
          </a>
        </div>
      </details>
    </nav>
  );
}
