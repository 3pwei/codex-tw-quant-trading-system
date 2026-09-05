import { Suspense } from "react";
import SectionShell from "../../components/section-shell";
import CompositeBuilder from "../../strategies/composite-builder";

export default function NewCompositeStrategyPage() {
  return <SectionShell active="/composite-strategies/" eyebrow="WADE QUANT LAB · NEW COMPOSITE" title="建立組合策略" description="將基本策略與其他組合策略編排成新版本">
    <Suspense fallback={<section className="panel strategy-loading">正在準備策略編輯器…</section>}><CompositeBuilder mode="new" /></Suspense>
  </SectionShell>;
}
