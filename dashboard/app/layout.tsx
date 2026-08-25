import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://tw-quant-backtest.popop9987.chatgpt.site"),
  title: "微型臺指期貨 5 分 K 夜盤回測",
  description: "檢視微型臺指期貨 8/24 夜盤的 5 分 K 進出場、損益與回撤風險。",
  openGraph: {
    title: "微型臺指期貨 5 分 K 夜盤回測",
    description: "8/24 夜盤進出場、損益與風險一頁掌握。",
    url: "https://tw-quant-backtest.popop9987.chatgpt.site",
    siteName: "微型臺指期貨夜盤回測",
    locale: "zh_TW",
    type: "website",
    images: [
      {
        url: "https://tw-quant-backtest.popop9987.chatgpt.site/og.png",
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
    images: ["https://tw-quant-backtest.popop9987.chatgpt.site/og.png"],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
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
