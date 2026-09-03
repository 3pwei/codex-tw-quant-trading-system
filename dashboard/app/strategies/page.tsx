import SectionShell from "../components/section-shell";

const strategies = [
  { code: "ORB", name: "開盤區間突破", description: "依交易時段開盤區間確認突破方向，訊號於下一根 K 棒開盤成立。", stop: "0.6%", target: "1.2%" },
  { code: "BNF", name: "均值回歸", description: "辨識價格偏離均值後的回歸機會，使用與即時頁、回測頁相同的共用核心。", stop: "0.6%", target: "1.2%" },
];

export default function StrategiesPage() {
  return <SectionShell active="/strategies/" eyebrow="WADE QUANT LAB · STRATEGIES" title="策略管理" description="檢視目前啟用的策略與風險規則">
    <section className="strategy-catalog">
      {strategies.map(strategy => <article className="panel" key={strategy.code}><span>{strategy.code}</span><h2>{strategy.name}</h2><p>{strategy.description}</p><dl><div><dt>停損</dt><dd className="loss">{strategy.stop}</dd></div><div><dt>停利</dt><dd className="profit">{strategy.target}</dd></div><div><dt>策略核心</dt><dd>即時／回測共用</dd></div></dl></article>)}
    </section>
    <p className="page-note">目前此頁為唯讀策略目錄；策略參數仍由後端版本控制，避免瀏覽器任意修改正式規則。</p>
  </SectionShell>;
}
