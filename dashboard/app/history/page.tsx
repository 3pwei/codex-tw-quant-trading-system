import SectionShell from "../components/section-shell";
import HistoryDashboard from "./history-dashboard";

export default function HistoryPage() {
  return <SectionShell active="/history/" eyebrow="WADE QUANT LAB · HISTORY" title="回測／交易歷史" description="集中查看已保存的策略執行結果">
    <HistoryDashboard />
  </SectionShell>;
}
