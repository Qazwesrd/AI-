"use client";

import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Diagnostic = {
  status: string;
  platform: string;
  python: string;
  database: { status: string; path: string; size_bytes?: number; tables?: number; schema_version?: number; message: string };
  storage: { path: string; disk: { status: string; free_bytes: number; total_bytes: number; message: string } };
  media_tools: { ffmpeg: { status: string; path?: string | null; message: string }; ffprobe: { status: string; path?: string | null; message: string } };
  local_ai: { mode: string; network_used: boolean; auto_downloaded: boolean; note: string; tools: { id: string; name: string; purpose: string; status: string; executable?: string | null; install_hint: string }[] };
  ai_permissions: { network_enabled: boolean; paid_api_allowed: boolean; note: string };
  tasks: { active_leases: number; queued_tasks: number };
  warnings: string[];
  checked_components: number;
};

function size(value?: number) {
  if (value === undefined) return "—";
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

function Status({ value }: { value: string }) {
  const ok = value === "ok";
  const warning = value === "warning";
  return <span className={`rounded-full px-3 py-1 text-xs ${ok ? "bg-emerald-500/15 text-emerald-300" : warning ? "bg-amber-500/15 text-amber-200" : "bg-rose-500/15 text-rose-300"}`}>{ok ? "正常" : warning ? "注意" : "异常"}</span>;
}

export default function SystemDiagnosticsPage() {
  const [data, setData] = useState<Diagnostic | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/system/diagnostics`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "无法读取系统诊断信息");
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "系统诊断失败");
    } finally { setBusy(false); }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5";
  return (
    <main className="cosmic min-h-screen p-5 text-white md:p-8">
      <div className="mx-auto max-w-5xl space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">系统运行诊断中心</h1>
            <p className="mt-2 text-sm text-white/60">快速检查本地数据库、磁盘空间、FFmpeg、FFprobe 和任务状态；不会读取或显示 API 密钥。</p>
            <a href="/" className="mt-3 inline-flex rounded-lg border border-purple-300/30 px-3 py-2 text-sm">返回工作台</a>
          </div>
          <button disabled={busy} onClick={() => void refresh()} className="rounded-xl bg-purple-600 px-4 py-3 text-sm font-semibold disabled:opacity-40">{busy ? "正在检查…" : "重新检查"}</button>
        </header>

        {error && <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 p-4 text-rose-200">{error}</div>}

        {data && <>
          <section className={panel}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm text-white/50">整体状态</div>
                <div className="mt-1 text-2xl font-bold">{data.status === "ok" ? "本地环境运行正常" : data.status === "warning" ? "有项目需要留意" : "发现运行问题"}</div>
                <div className="mt-2 text-xs text-white/45">{data.platform} · Python {data.python} · 已检查 {data.checked_components} 个核心组件</div>
              </div>
              <Status value={data.status} />
            </div>
            {data.warnings.length > 0 && <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-amber-200">{data.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
          </section>

          <section className="grid gap-4 md:grid-cols-2">
            <div className={panel}>
              <div className="flex items-center justify-between"><h2 className="font-semibold">数据库</h2><Status value={data.database.status} /></div>
              <p className="mt-3 text-sm">{data.database.message}</p>
              <p className="mt-2 break-all text-xs text-white/45">{data.database.path}</p>
              <p className="mt-2 text-xs text-white/55">数据表：{data.database.tables ?? "—"} · 结构版本：{data.database.schema_version ?? "—"} · 文件大小：{size(data.database.size_bytes)}</p>
              <a href="/backup-center" className="mt-4 inline-flex text-sm text-cyan-200 underline">打开备份与恢复中心</a>
            </div>
            <div className={panel}>
              <div className="flex items-center justify-between"><h2 className="font-semibold">磁盘与素材存储</h2><Status value={data.storage.disk.status} /></div>
              <p className="mt-3 text-sm">{data.storage.disk.message}</p>
              <p className="mt-2 text-xs text-white/55">可用：{size(data.storage.disk.free_bytes)} / 总计：{size(data.storage.disk.total_bytes)}</p>
              <p className="mt-2 break-all text-xs text-white/45">{data.storage.path}</p>
            </div>
            <div className={panel}>
              <h2 className="mb-4 font-semibold">视频处理组件</h2>
              {[["FFmpeg", data.media_tools.ffmpeg], ["FFprobe", data.media_tools.ffprobe]].map(([name, item]: any) => (
                <div key={name} className="mb-4 last:mb-0">
                  <div className="flex items-center justify-between"><span className="text-sm">{name}</span><Status value={item.status} /></div>
                  <p className="mt-1 break-all text-xs text-white/45">{item.path || item.message}</p>
                </div>
              ))}
            </div>
            <div className={panel}>
              <h2 className="mb-4 font-semibold">AI 权限与任务</h2>
              <div className="flex justify-between text-sm"><span className="text-white/60">网络调用总开关</span><span>{data.ai_permissions.network_enabled ? "已开启" : "已关闭"}</span></div>
              <div className="mt-2 flex justify-between text-sm"><span className="text-white/60">付费 API 总授权</span><span>{data.ai_permissions.paid_api_allowed ? "已授权" : "未授权"}</span></div>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <div className="rounded-xl bg-white/5 p-3"><div className="text-xs text-white/45">执行中任务</div><div className="mt-1 text-2xl font-bold">{data.tasks.active_leases}</div></div>
                <div className="rounded-xl bg-white/5 p-3"><div className="text-xs text-white/45">等待队列</div><div className="mt-1 text-2xl font-bold">{data.tasks.queued_tasks}</div></div>
              </div>
              <a href="/tasks" className="mt-4 inline-flex text-sm text-cyan-200 underline">打开统一任务中心</a>
            </div>
          </section>
          <section className={panel}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-semibold">本地 AI 引擎检测</h2>
              <span className="rounded-full bg-emerald-500/15 px-3 py-1 text-xs text-emerald-200">离线检测 · 不下载 · 不收费</span>
            </div>
            <p className="mt-2 text-xs leading-6 text-white/55">{data.local_ai.note}</p>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              {data.local_ai.tools.map((tool) => (
                <div key={tool.id} className="rounded-xl border border-white/10 bg-white/[0.035] p-4">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="font-semibold">{tool.name}</h3>
                    <Status value={tool.status === "installed" ? "ok" : "warning"} />
                  </div>
                  <p className="mt-2 text-xs text-white/60">{tool.purpose}</p>
                  <p className="mt-2 break-all text-xs text-white/45">{tool.executable || "未在系统 PATH 中检测到"}</p>
                  {tool.status !== "installed" && <p className="mt-3 text-xs leading-5 text-amber-100/80">{tool.install_hint}</p>}
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-white/45">检测到运行环境不代表已经下载模型或具备生成能力。模型下载前需要确认文件大小、电脑配置和模型许可证；本功能不会自动下载任何内容。</p>
          </section>
        </>}
      </div>
    </main>
  );
}
