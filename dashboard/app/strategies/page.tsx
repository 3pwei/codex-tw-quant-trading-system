import SectionShell from "../components/section-shell";
import StrategyTabs from "../components/strategy-tabs";
import StrategyManager from "./strategy-manager";

export default function StrategiesPage() {
  return <SectionShell active="/strategies/" eyebrow="WADE QUANT LAB · ATOMIC STRATEGIES" title="基本策略" description="管理趨勢、突破、均值回歸、動能與波動策略的共用參數">
    <StrategyTabs active="atomic" />
    <StrategyManager />
  </SectionShell>;
}
