import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://tw-quant-backtest.popop9987.chatgpt.site"),
  title: "台股量化交易回測儀表板",
  description: "清楚檢視台股分鐘策略的進出場、獲利、交易成本與回撤風險。",
  openGraph: {
    title: "台股量化交易回測儀表板",
    description: "進出場、損益與風險一頁掌握。",
    url: "https://tw-quant-backtest.popop9987.chatgpt.site",
    siteName: "台股量化交易回測儀表板",
    locale: "zh_TW",
    type: "website",
    images: [
      {
        url: "https://tw-quant-backtest.popop9987.chatgpt.site/og.png",
        width: 1200,
        height: 630,
        alt: "台股量化交易回測儀表板",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "台股量化交易回測儀表板",
    description: "進出場、損益與風險一頁掌握。",
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
