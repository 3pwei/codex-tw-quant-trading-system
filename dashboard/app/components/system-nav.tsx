"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const routes = [
  { href: "/", label: "總覽" },
  { href: "/live/", label: "即時行情" },
  { href: "/backtest/", label: "歷史回測" },
  { href: "/replay/", label: "行情回放" },
  { href: "/history/", label: "執行紀錄" },
  { href: "/strategies/", label: "策略管理" },
  { href: "/settings/", label: "系統設定", admin: true },
  { href: "/admin/users/", label: "帳號權限", admin: true },
] as const;

export type SystemRoute = (typeof routes)[number]["href"];

export default function SystemNav({ active }: { active: SystemRoute }) {
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    let activeRequest = true;
    fetch("/api/me", { cache: "no-store" })
      .then(response => response.ok ? response.json() : Promise.reject())
      .then(user => { if (activeRequest) setIsAdmin(user.role === "admin"); })
      .catch(() => { if (activeRequest) setIsAdmin(false); });
    return () => { activeRequest = false; };
  }, []);

  return (
    <nav className="system-nav" aria-label="系統功能">
      {routes.filter(route => !("admin" in route) || isAdmin).map(route => (
        <Link
          key={route.href}
          href={route.href}
          className={route.href === active ? "active" : undefined}
          aria-current={route.href === active ? "page" : undefined}
        >
          {route.label}
        </Link>
      ))}
    </nav>
  );
}
