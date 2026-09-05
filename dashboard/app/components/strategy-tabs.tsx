import Link from "next/link";

export default function StrategyTabs({ active }: { active: "atomic" | "composite" }) {
  return <nav className="strategy-tabs" aria-label="策略類型">
    <Link href="/strategies/" className={active === "atomic" ? "active" : undefined} aria-current={active === "atomic" ? "page" : undefined}>
      <span>ATOMIC</span>
      <strong>基本策略</strong>
      <small>調整單一策略的共用參數</small>
    </Link>
    <Link href="/composite-strategies/" className={active === "composite" ? "active composite" : "composite"} aria-current={active === "composite" ? "page" : undefined}>
      <span>COMPOSITE</span>
      <strong>組合策略</strong>
      <small>編排多週期與巢狀策略</small>
    </Link>
  </nav>;
}
