import SectionShell from "../components/section-shell";
import PaperTradingDashboard from "./paper-trading-dashboard";

export default function PaperTradingPage() {
  return (
    <SectionShell
      active="/paper/"
      eyebrow="WADE QUANT LAB · PAPER EXECUTION"
      title="模擬交易"
      description="使用即時行情驗證委託、風控、成交與持倉流程"
    >
      <PaperTradingDashboard />
    </SectionShell>
  );
}
