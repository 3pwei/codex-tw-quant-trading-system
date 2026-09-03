import Link from "next/link";
import SectionShell from "../components/section-shell";

export default function ReplayPage() {
  return <SectionShell active="/replay/" eyebrow="WADE QUANT LAB · REPLAY" title="動態歷史回放" description="模擬盤中行情，依時間軸逐根推進 K 棒">
    <section className="feature-state panel"><span>PLANNED MODULE</span><h2>回放控制器準備中</h2><p>這個路由已保留給可暫停、調速及逐根前進的歷史行情播放器。現階段後端 Replay Feed 用於 Mock 驗證，尚未開放瀏覽器選取歷史區間。</p><div><Link href="/backtest/">先使用歷史回測</Link><Link href="/live/">查看即時行情</Link></div></section>
  </SectionShell>;
}
