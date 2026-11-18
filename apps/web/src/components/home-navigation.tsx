"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export default function HomeNavigation() {
  const pathname = usePathname();

  if (pathname === "/") return null;

  return (
    <Link
      href="/"
      aria-label="返回首页"
      className="fixed bottom-5 left-5 z-[100] inline-flex items-center gap-2 rounded-full border border-purple-300/35 bg-slate-950/90 px-4 py-3 text-sm font-medium text-purple-100 shadow-xl shadow-purple-950/40 backdrop-blur transition hover:border-purple-200/70 hover:bg-purple-950"
    >
      <span aria-hidden="true">←</span>
      返回首页
    </Link>
  );
}
