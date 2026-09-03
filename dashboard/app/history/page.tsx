import Link from "next/link";
import SectionShell from "../components/section-shell";

export default function HistoryPage() {
  return <SectionShell active="/history/" eyebrow="WADE QUANT LAB · HISTORY" title="回測／交易歷史" description="集中查看已保存的策略執行結果">
    <section className="feature-state panel"><span>ARCHIVE MODULE</span><h2>尚無已保存的執行紀錄</h2><p>目前回測結果於瀏覽器即時計算與顯示，尚未寫入獨立的回測執行紀錄表。建立保存功能後，這裡會提供條件、績效、交易明細與版本追蹤。</p><div><Link href="/backtest/">建立新的回測結果</Link></div></section>
  </SectionShell>;
}
