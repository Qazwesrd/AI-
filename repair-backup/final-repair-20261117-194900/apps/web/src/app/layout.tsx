import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI漫剧制作大师",
  description: "AI漫剧创作、小说解析与智能视频剪辑平台",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
