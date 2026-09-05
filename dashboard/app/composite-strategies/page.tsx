import SectionShell from "../components/section-shell";
import StrategyTabs from "../components/strategy-tabs";
import CompositeCatalog from "./composite-catalog";

export default function CompositeStrategiesPage() {
  return <SectionShell active="/composite-strategies/" eyebrow="WADE QUANT LAB · COMPOSITE STRATEGIES" title="組合策略" description="管理多週期、版本化與巢狀策略">
    <StrategyTabs active="composite" />
    <CompositeCatalog />
  </SectionShell>;
}
