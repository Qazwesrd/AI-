"use client";

import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type PatchPackage = {
  id: string;
  title: string;
  description?: string;
  target_version?: string;
  patch_format: string;
  created_at: string;
  sha256: string;
  size_bytes: number;
  changed_files?: string[];
  status: string;
};

export default function PatchCenterPage() {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [targetVersion, setTargetVersion] = useState("current");
  const [format, setFormat] = useState("unified-diff");
  const [targetPath, setTargetPath] = useState("");
  const [code, setCode] = useState("");
  const [packages, setPackages] = useState<PatchPackage[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState<{ package_count?: number; apply_supported?: boolean; message?: string } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([
        fetch(`${API}/api/patch-center/status`, { cache: "no-store" }),
        fetch(`${API}/api/patch-center/packages`, { cache: "no-store" }),
      ]);
      if (!s.ok || !p.ok) throw new Error("补丁中心服务暂不可用");
      const [sd, pd] = await Promise.all([s.json(), p.json()]);
      setStatus(sd);
      setPackages(pd.packages || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取补丁记录失败");
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  async function generatePatch() {
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${API}/api/patch-center/packages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title, description, target_version: targetVersion,
          patch_format: format, target_path: targetPath, code,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "补丁包生成失败");
      setMessage(`补丁包已生成：${data.id}；大小 ${data.size_bytes} 字节；SHA256：${data.sha256}`);
      setTitle(""); setDescription(""); setTargetPath(""); setCode("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "补丁包生成失败");
    } finally {
      setBusy(false);
    }
  }

  return <main className="cosmic min-h-screen text-white">
    <div className="nebula nebula-one" /><div className="nebula nebula-two" />
    <div className="relative z-10 mx-auto max-w-6xl p-5 lg:p-8">
      <header className="mb-7">
        <a href="/" className="text-sm text-purple-300">← 返回首页</a>
        <h1 className="mt-3 text-3xl font-black">🩹 补丁中心</h1>
        <p className="mt-2 text-sm text-purple-200">后续小功能和代码修复通过补丁包交付，不重复下载整套程序或 AI 模型。</p>
      </header>

      <section className="mb-5 rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="font-bold">补丁服务状态</h2><p className="mt-1 text-xs text-purple-300">本机已暂存 {status?.package_count ?? packages.length} 个补丁包</p></div>
          <span className="rounded-full bg-emerald-500/15 px-3 py-1 text-xs text-emerald-200">本地补丁仓库</span>
        </div>
        <p className="mt-3 text-sm text-amber-200">{status?.message || "补丁包生成和暂存已启用；自动应用器尚未启用。"}</p>
      </section>

      {error && <div className="mb-4 rounded-xl border border-rose-400/30 bg-rose-950/30 p-4 text-sm text-rose-200">{error}</div>}
      {message && <div className="mb-4 rounded-xl border border-emerald-400/30 bg-emerald-950/30 p-4 text-sm text-emerald-100 break-all">{message}</div>}

      <section className="rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5">
        <h2 className="mb-4 text-xl font-bold">生成新补丁</h2>
        <div className="grid gap-4 md:grid-cols-2">
          <label className="text-sm">补丁名称
            <input value={title} onChange={e => setTitle(e.target.value)} maxLength={80} placeholder="例如：修复分镜导出按钮" className="mt-2 w-full rounded-xl border border-purple-300/20 bg-black/30 p-3 text-white outline-none focus:border-fuchsia-300" />
          </label>
          <label className="text-sm">目标程序版本
            <input value={targetVersion} onChange={e => setTargetVersion(e.target.value)} maxLength={40} className="mt-2 w-full rounded-xl border border-purple-300/20 bg-black/30 p-3 text-white outline-none focus:border-fuchsia-300" />
          </label>
        </div>
        <label className="mt-4 block text-sm">补丁说明
          <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} maxLength={1000} placeholder="说明本次增加或修复什么功能" className="mt-2 w-full rounded-xl border border-purple-300/20 bg-black/30 p-3 text-white outline-none focus:border-fuchsia-300" />
        </label>
        <label className="mt-4 block text-sm">补丁格式
          <select value={format} onChange={e => setFormat(e.target.value)} className="mt-2 w-full rounded-xl border border-purple-300/20 bg-[#21143c] p-3 text-white">
            <option value="unified-diff">标准代码差异（Unified Diff）</option>
            <option value="source-code">源代码片段</option>
            <option value="json-patch">JSON 配置补丁</option>
          </select>
        </label>
        <label className="mt-4 block text-sm">粘贴补丁代码
          <textarea value={code} onChange={e => setCode(e.target.value)} rows={15} spellCheck={false} placeholder={"在这里粘贴代码或补丁内容……\n标准差异格式示例：\n--- a/apps/web/src/example.ts\n+++ b/apps/web/src/example.ts\n@@ -1,1 +1,1 @@\n-old\n+new"} className="mt-2 w-full rounded-xl border border-purple-300/20 bg-[#090611] p-4 font-mono text-xs leading-6 text-emerald-100 outline-none focus:border-fuchsia-300" />
        </label>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-purple-300">单个补丁上限 512 KiB。模型权重、视频模型、运行时和大型素材仍走独立资源包。</p>
          <button onClick={() => void generatePatch()} disabled={busy || !title.trim() || !code.trim()} className="rounded-xl bg-fuchsia-700 px-5 py-3 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-40">{busy ? "正在生成…" : "生成并保存补丁包"}</button>
        </div>
      </section>

      <section className="mt-6 rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5">
        <div className="mb-4 flex items-center justify-between gap-3"><h2 className="text-xl font-bold">已生成补丁</h2><button onClick={() => void refresh()} className="rounded-lg border border-purple-300/30 px-3 py-2 text-xs">刷新</button></div>
        {packages.length === 0 ? <p className="text-sm text-purple-300">暂无补丁包。</p> : <div className="space-y-3">{packages.map(item => <article key={item.id} className="rounded-xl bg-black/25 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2"><b>{item.title}</b><span className="rounded-full bg-amber-500/15 px-2 py-1 text-xs text-amber-200">已暂存</span></div>
          <p className="mt-1 text-xs text-purple-300">{item.description || "无补丁说明"} · {item.patch_format} · {item.size_bytes} bytes</p>
          <p className="mt-2 text-xs text-purple-300">涉及文件：{item.changed_files?.join("、") || "未提供"}</p><p className="mt-2 break-all font-mono text-[11px] text-purple-400">ID: {item.id} · SHA256: {item.sha256}</p>
        </article>)}</div>}
      </section>
      <p className="mt-5 text-xs leading-6 text-purple-300">安全说明：当前版本只负责生成、校验和本机暂存补丁，不会执行粘贴的代码，也不会覆盖程序文件。自动应用、备份和一键回滚需要下一阶段接入受限更新器并通过桌面端测试后才会开放。</p>
    </div>
  </main>;
}
