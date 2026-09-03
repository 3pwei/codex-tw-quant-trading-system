import SectionShell from "../components/section-shell";
import StrategyManager from "./strategy-manager";

export default function StrategiesPage() {
  return <SectionShell active="/strategies/" eyebrow="WADE QUANT LAB · STRATEGIES" title="策略管理" description="調整共用策略參數與風險規則">
    <StrategyManager />
  </SectionShell>;
}
