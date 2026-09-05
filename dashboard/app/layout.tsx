import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL ??
      "https://3pwei.github.io/codex-tw-quant-trading-system/"
  ),
  title: "微型臺指期貨量化儀表板",
  description: "TMF 即時行情、歷史回測、策略與系統狀態儀表板。",
  openGraph: {
    title: "微型臺指期貨 1 分 K 與策略回測",
    description: "TMF 即時行情、歷史回測、基本與組合策略及系統狀態一站管理。",
    url: ".",
    siteName: "Wade Quant Lab",
    locale: "zh_TW",
    type: "website",
    images: [
      {
        url: "og.png",
        width: 1200,
        height: 630,
        alt: "微型臺指期貨 1 分 K 與策略回測",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "微型臺指期貨 1 分 K 與策略回測",
    description: "TMF 即時行情、歷史回測、基本與組合策略及系統狀態一站管理。",
    images: ["og.png"],
  },
  icons: {
    icon: "favicon.svg",
    shortcut: "favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
