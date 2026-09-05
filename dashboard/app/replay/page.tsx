import SectionShell from "../components/section-shell";
import ReplayDashboard from "./replay-dashboard";

export default function ReplayPage() {
  return <SectionShell
    active="/replay/"
    eyebrow="WADE QUANT LAB · REPLAY"
    title="動態歷史回放"
    description="以歷史行情逐根推進，觀察策略訊號當下如何形成"
  >
    <ReplayDashboard />
  </SectionShell>;
}
