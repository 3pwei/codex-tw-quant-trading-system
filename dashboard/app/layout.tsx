import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL ??
      "https://3pwei.github.io/codex-tw-quant-trading-system/"
  ),
  title: "微型臺指期貨量化儀表板",
  description: "TMF 回測與即時 1 分 K 行情儀表板。",
  openGraph: {
    title: "微型臺指期貨 5 分 K 夜盤回測",
    description: "8/24 夜盤進出場、損益與風險一頁掌握。",
    url: ".",
    siteName: "微型臺指期貨夜盤回測",
    locale: "zh_TW",
    type: "website",
    images: [
      {
        url: "og.png",
        width: 1200,
        height: 630,
        alt: "微型臺指期貨 5 分 K 夜盤回測",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "微型臺指期貨 5 分 K 夜盤回測",
    description: "8/24 夜盤進出場、損益與風險一頁掌握。",
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
