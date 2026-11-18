import type { Metadata } from "next";
import "./globals.css";
import HomeNavigation from "../components/home-navigation";

export const metadata: Metadata = {
  title: "AI漫剧制作大师",
  description: "AI漫剧创作、小说解析与智能视频剪辑平台",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full">{children}<HomeNavigation /></body>
    </html>
  );
}
