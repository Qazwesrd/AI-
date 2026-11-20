"use client";

import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type ShotItem = {
  shot_id: number;
  shot_number: number;
  scene_name: string;
  visual_description: string;
  planned_duration_seconds: number;
  status: "ready" | "missing" | "processing" | "broken";
  latest_task_id: number | null;
  latest_task_status: string | null;
  selected_task_id: number | null;
  video: null | { duration_seconds: number; width?: number; height?: number; preview_url?: string | null };
  warnings: { task_id: number; reason: string }[];
  error?: string | null;
};

type Readiness = {
  episode_id: number;
  episode_number?: number;
  episode_title?: string;
  ready: boolean;
  total_shots: number;
  ready_count: number;
  missing_count: number;
  processing_count: number;
  broken_count: number;
  items: ShotItem[];
  message: string;
};

const statusText: Record<ShotItem["status"], string> = {
  ready: "视频可用",
  missing: "缺少视频",
  processing: "仍在生成",
  broken: "文件损坏或丢失",
};

export default function EpisodeReadinessPage() {
  const [episodeId, setEpisodeId] = useState("");
  const [report, setReport] = useState<Readiness | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [replacement, setReplacement] = useState<Record<number, File | null>>({});
  const [renderResult, setRenderResult] = useState<{ output?: string; subtitle_path?: string; duration_target?: number; quality_report?: { duration_seconds?: number; width?: number; height?: number } } | null>(null);

  async function checkReadiness() {
    const id = Number(episodeId);
    if (!Number.isInteger(id) || id <= 0) {
      setMessage("请输入有效的分集 ID（正整数）。");
      return;
    }
    setBusy(true);
    setMessage("正在逐镜头检查视频文件、视频流和时长……");
    setRenderResult(null);
    try {
      const response = await fetch(`${API}/api/media/video/episodes/${id}/render-readiness`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "检查失败");
      setReport(data);
      setMessage(data.message);
    } catch (error) {
      setReport(null);
      setMessage(error instanceof Error ? error.message : "检查失败");
    } finally {
      setBusy(false);
    }
  }

  async function replaceShot(shotId: number) {
    const file = replacement[shotId];
    if (!file || !report) {
      setMessage("请先选择要替换的本地视频文件。");
      return;
    }
    if (!window.confirm(`确认将镜头 #${shotId} 替换为“${file.name}”？旧素材会保留，不会调用 AI 服务或产生 API 费用。`)) return;
    setBusy(true);
    setMessage(`正在验证并保存镜头 #${shotId} 的替换视频……`);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(`${API}/api/media/video/episodes/${report.episode_id}/shots/${shotId}/replace`, {
        method: "POST",
        body: form,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "替换失败");
      setMessage(data.message || "镜头替换完成。");
      setReplacement(current => ({ ...current, [shotId]: null }));
      await checkReadiness();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "替换失败");
    } finally {
      setBusy(false);
    }
  }

  async function renderEpisode() {
    if (!report?.ready) {
      setMessage("仍有缺失、生成中或损坏的镜头，不能开始合成。");
      return;
    }
    if (!window.confirm(`确认合成第 ${report.episode_number ?? ""} 集？将使用当前检查通过的镜头视频，并在本地执行 FFmpeg 合成。`)) return;
    setBusy(true);
    setMessage("正在合成分集视频，请保持软件运行……");
    try {
      const response = await fetch(`${API}/api/media/video/episodes/${report.episode_id}/render`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "合成失败");
      setRenderResult(data);
      setMessage("分集成片已生成，并完成基础音视频质量检查。");
      await checkReadiness();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "合成失败");
    } finally {
      setBusy(false);
    }
  }

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/70 p-5";
  const counts = report ? [
    ["可用", report.ready_count, "text-emerald-300"],
    ["缺失", report.missing_count, "text-amber-300"],
    ["生成中", report.processing_count, "text-sky-300"],
    ["异常", report.broken_count, "text-rose-300"],
  ] as const : [];

  return (
    <main className="cosmic min-h-screen p-5 text-white md:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <header>
          <h1 className="text-3xl font-bold">分集成片检查与镜头替换</h1>
          <p className="mt-2 text-sm text-white/60">先检查每个镜头的视频文件和媒体流，再合成 MP4。替换素材只保存在本地，不会触发 AI 生成。</p>
          <a href="/storyboard" className="mt-3 inline-flex rounded-lg border border-white/20 px-4 py-2 text-sm hover:bg-white/10">返回分镜工作台</a>
        </header>

        {message && <div className="rounded-xl border border-purple-300/30 bg-purple-500/10 p-4">{message}</div>}

        <section className={panel}>
          <h2 className="mb-3 text-lg font-semibold">选择要检查的分集</h2>
          <div className="flex flex-wrap gap-3">
            <input value={episodeId} onChange={event => setEpisodeId(event.target.value)} inputMode="numeric" placeholder="输入分集 ID，例如 1" className="min-w-0 flex-1 rounded-lg border border-white/15 bg-black/30 px-3 py-3" />
            <button disabled={busy} onClick={() => void checkReadiness()} className="rounded-lg bg-indigo-600 px-5 py-3 font-semibold disabled:opacity-50">检查成片条件</button>
          </div>
        </section>

        {report && (
          <>
            <section className={panel}>
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h2 className="text-xl font-semibold">{report.episode_title || `分集 #${report.episode_id}`}</h2>
                  <p className="mt-1 text-sm text-white/55">共 {report.total_shots} 个镜头 · 逐个验证视频文件与可解码视频流</p>
                </div>
                <button disabled={busy || !report.ready} onClick={() => void renderEpisode()} className="rounded-xl bg-emerald-600 px-5 py-3 font-semibold disabled:cursor-not-allowed disabled:opacity-40">合成分集 MP4</button>
              </div>
              <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
                {counts.map(([label, value, color]) => <div key={label} className="rounded-xl bg-black/25 p-4"><div className={`text-2xl font-bold ${color}`}>{value}</div><div className="mt-1 text-sm text-white/55">{label}</div></div>)}
              </div>
              {!report.ready && <p className="mt-4 text-sm text-amber-200">请先处理下方未通过检查的镜头。已有历史有效视频会保留，并优先选择可正常读取的版本。</p>}
            </section>

            <section className="space-y-3">
              {report.items.map(item => (
                <article key={item.shot_id} className={panel}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="font-semibold">镜头 {item.shot_number} <span className="ml-2 text-xs text-white/45">ID {item.shot_id}</span></h3>
                      <p className="mt-1 text-sm text-white/55">{item.scene_name || "未命名场景"} · 计划 {item.planned_duration_seconds}s</p>
                    </div>
                    <span className={`rounded-full px-3 py-1 text-xs ${item.status === "ready" ? "bg-emerald-500/15 text-emerald-300" : item.status === "processing" ? "bg-sky-500/15 text-sky-300" : item.status === "broken" ? "bg-rose-500/15 text-rose-300" : "bg-amber-500/15 text-amber-300"}`}>{statusText[item.status]}</span>
                  </div>
                  {item.visual_description && <p className="mt-3 text-sm text-white/70">{item.visual_description}</p>}
                  {item.video && <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-emerald-200"><span>{item.video.duration_seconds}s</span><span>{item.video.width || "?"} × {item.video.height || "?"}</span>{item.video.preview_url && <a className="underline" href={`${API}${item.video.preview_url}`} target="_blank" rel="noreferrer">打开当前视频</a>}</div>}
                  {item.warnings.map(warning => <p key={warning.task_id} className="mt-2 text-xs text-rose-300">历史任务 #{warning.task_id}：{warning.reason}</p>)}
                  {item.error && <p className="mt-2 text-xs text-rose-300">{item.error}</p>}
                  {item.status !== "ready" && (
                    <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-white/10 pt-4">
                      <label className="min-w-0 flex-1 text-sm text-white/65">选择本地替换视频
                        <input type="file" accept=".mp4,.mov,.mkv,.webm,video/*" className="mt-2 block w-full text-xs" onChange={event => setReplacement(current => ({ ...current, [item.shot_id]: event.target.files?.[0] || null }))} />
                      </label>
                      <button disabled={busy || !replacement[item.shot_id]} onClick={() => void replaceShot(item.shot_id)} className="rounded-lg border border-cyan-300/40 bg-cyan-900/40 px-4 py-2 text-sm text-cyan-100 disabled:opacity-40">确认替换此镜头</button>
                    </div>
                  )}
                </article>
              ))}
            </section>
          </>
        )}

        {renderResult && (
          <section className={panel}>
            <h2 className="text-lg font-semibold text-emerald-300">成片生成成功</h2>
            <p className="mt-2 break-all text-sm">{renderResult.output}</p>
            {renderResult.subtitle_path && <p className="mt-2 break-all text-xs text-white/55">字幕：{renderResult.subtitle_path}</p>}
            {renderResult.quality_report && <p className="mt-3 text-sm text-white/65">时长 {renderResult.quality_report.duration_seconds ?? "—"} 秒 · 分辨率 {renderResult.quality_report.width ?? "—"} × {renderResult.quality_report.height ?? "—"}</p>}
          </section>
        )}
      </div>
    </main>
  );
}
