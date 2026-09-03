import SectionShell from "../components/section-shell";

export default function SettingsPage() {
  return <SectionShell active="/settings/" eyebrow="WADE QUANT LAB · SETTINGS" title="系統設定" description="行情來源、商品與部署環境摘要">
    <section className="settings-grid panel">
      <div><span>行情供應商</span><strong>永豐 Shioaji</strong><small>正式環境僅訂閱行情</small></div>
      <div><span>商品</span><strong>TMF 微型臺指期貨</strong><small>近月合約由後端識別</small></div>
      <div><span>K 棒週期</span><strong>1／5／10／15／30 分、1 小時、日、週</strong><small>以 1 分 K 依交易所時段即時聚合</small></div>
      <div><span>資料儲存</span><strong>SQLite</strong><small>介面保留 PostgreSQL 切換能力</small></div>
      <div><span>正式主機</span><strong>AWS Lightsail</strong><small>Docker Compose 常駐服務</small></div>
      <div><span>存取保護</span><strong>Cloudflare Access</strong><small>核准 Email 一次性驗證碼</small></div>
    </section>
    <div className="security-note"><b>安全邊界</b><p>API Key、Secret 與帳號資訊只從伺服器環境變數載入，不會顯示在此頁、送到瀏覽器或提交至 GitHub。</p></div>
  </SectionShell>;
}
