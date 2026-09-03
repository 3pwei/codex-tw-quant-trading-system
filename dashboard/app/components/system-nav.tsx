import Link from "next/link";

const routes = [
  { href: "/", label: "總覽" },
  { href: "/live/", label: "即時行情" },
  { href: "/backtest/", label: "歷史回測" },
  { href: "/replay/", label: "行情回放" },
  { href: "/history/", label: "執行紀錄" },
  { href: "/strategies/", label: "策略管理" },
  { href: "/settings/", label: "系統設定" },
] as const;

export type SystemRoute = (typeof routes)[number]["href"];

export default function SystemNav({ active }: { active: SystemRoute }) {
  return (
    <nav className="system-nav" aria-label="系統功能">
      {routes.map(route => (
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
