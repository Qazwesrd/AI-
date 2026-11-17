"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type MediaFile = {
  id: number;
  name: string;
  category: string;
  path: string;
  size: number;
};

type EditJob = {
  id: number;
  title: string;
  provider: string;
  model: string;
  instruction: string;
  status: string;
  edit_plan: any;
  output_path: string;
  error: string;
};

export default function VideoEditorPage() {
  const [files, setFiles] = useState<MediaFile[]>([]);
  const [jobs, setJobs] = useState<EditJob[]>([]);
  const [provider, setProvider] = useState("deepseek");
  const [model, setModel] = useState("");
  const [instruction, setInstruction] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [subtitlePath, setSubtitlePath] = useState("");
  const [audioPath, setAudioPath] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadAll() {
    try {
      const [fileResponse, jobResponse] = await Promise.all([
        fetch(`${API}/api/media/files?category=video`),
        fetch(`${API}/api/video-editor/jobs`)
      ]);
      if (fileResponse.ok) setFiles(await fileResponse.json());
      if (jobResponse.ok) setJobs(await jobResponse.json());
    } catch {
      setMessage("无法连接本地 API，请确认桌面软件服务已启动。");
    }
  }

  useEffect(() => { loadAll(); }, []);

  async function uploadFiles(list: FileList | null) {
    if (!list?.length) return;
    setBusy(true);
    setMessage("正在上传视频……");
    try {
      for (const file of Array.from(list)) {
        const form = new FormData();
        form.append("file", file);
        form.append("category", "video");
        const response = await fetch(`${API}/api/media/files/upload`, {
          method: "POST",
          body: form
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "上传失败");
      }
      setMessage("视频上传完成。");
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  async function createPlan() {
    if (!selected.length) {
      setMessage("请先选择至少一个视频文件。");
      return;
    }
    setBusy(true);
    setMessage("正在调用 AI 分析剪辑要求……");
    try {
      const response = await fetch(`${API}/api/video-editor/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider, model, instruction,
          source_paths: selected,
          confirm_paid: confirmPaid
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "AI 分析失败");
      setMessage(`AI 剪辑方案已生成，任务编号：${data.id}`);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "AI 分析失败");
    } finally {
      setBusy(false);
    }
  }

  async function executeJob(id: number) {
    setBusy(true);
    setMessage("FFmpeg 正在执行视频剪辑，请等待……");
    try {
      const response = await fetch(`${API}/api/video-editor/jobs/${id}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subtitle_path: subtitlePath || null,
          audio_path: audioPath || null
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "剪辑执行失败");
      setMessage(`视频导出完成：${data.output_path}`);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "剪辑失败");
      await loadAll();
    } finally {
      setBusy(false);
    }
  }

  function toggleFile(path: string) {
    setSelected(current =>
      current.includes(path)
        ? current.filter(x => x !== path)
        : [...current, path]
    );
  }

  function formatSize(size: number) {
    return size >= 1024 ** 3
      ? `${(size / 1024 ** 3).toFixed(2)} GB`
      : `${(size / 1024 ** 2).toFixed(1)} MB`;
  }

  return (
    <main className="min-h-screen p-8 text-white">
      <div className="mx-auto max-w-7xl space-y-7">
        <header>
          <h1 className="text-3xl font-bold">AI 视频剪辑中心</h1>
          <p className="mt-2 text-white/60">
            用文字描述剪辑要求，让 AI 规划，再由本地 FFmpeg 执行
          </p>
        </header>

        {message && (
          <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4">
            {message}
          </div>
        )}

        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
            <h2 className="mb-4 text-xl font-semibold">第一步：导入原视频</h2>
            <label className="block cursor-pointer rounded-xl border border-dashed border-purple-300/40 p-8 text-center hover:bg-purple-500/10">
              <span className="text-lg">选择视频文件</span>
              <p className="mt-2 text-sm text-white/50">支持多个视频，单文件上限 2GB</p>
              <input type="file" accept="video/*" multiple className="hidden"
                onChange={e => uploadFiles(e.target.files)} />
            </label>

            <h3 className="mb-3 mt-6 font-semibold">本地视频素材</h3>
            <div className="max-h-72 space-y-2 overflow-auto">
              {files.map(file => (
                <label key={file.id}
                  className="flex cursor-pointer items-center gap-3 rounded-lg border border-white/10 p-3">
                  <input type="checkbox"
                    checked={selected.includes(file.path)}
                    onChange={() => toggleFile(file.path)} />
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  <span className="text-xs text-white/50">{formatSize(file.size)}</span>
                </label>
              ))}
              {files.length === 0 && <p className="text-sm text-white/40">还没有导入视频</p>}
            </div>
          </div>

          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
            <h2 className="mb-4 text-xl font-semibold">第二步：描述剪辑要求</h2>
            <label className="mb-2 block text-sm">AI 服务商 ID</label>
            <input value={provider} onChange={e => setProvider(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />

            <label className="mb-2 block text-sm">AI 模型名称</label>
            <input value={model} onChange={e => setModel(e.target.value)}
              placeholder="例如 deepseek-chat"
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />

            <label className="mb-2 block text-sm">剪辑要求</label>
            <textarea value={instruction} onChange={e => setInstruction(e.target.value)}
              rows={7}
              placeholder="例如：保留人物说话和动作的部分，删除等待和空镜头。开头使用最精彩的画面，按故事发展顺序拼接，输出横屏 1080P 视频。"
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />

            <label className="mb-4 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={confirmPaid}
                onChange={e => setConfirmPaid(e.target.checked)} />
              我确认本次 AI 分析可能产生 API 费用
            </label>

            <button disabled={busy || !model.trim() || !instruction.trim()}
              onClick={createPlan}
              className="rounded-xl bg-purple-600 px-6 py-3 font-semibold disabled:opacity-40">
              AI 分析并生成剪辑方案
            </button>
          </div>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
          <h2 className="mb-4 text-xl font-semibold">第三步：可选字幕与音轨</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-2 block text-sm">字幕文件路径（可选）</label>
              <input value={subtitlePath} onChange={e => setSubtitlePath(e.target.value)}
                placeholder="storage/subtitles/xxx.srt 的完整路径"
                className="w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            </div>
            <div>
              <label className="mb-2 block text-sm">替换音轨路径（可选）</label>
              <input value={audioPath} onChange={e => setAudioPath(e.target.value)}
                placeholder="storage/audio/xxx.mp3 的完整路径"
                className="w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            </div>
          </div>
          <p className="mt-3 text-xs text-white/45">
            当前选择音轨会替换原视频音轨；不填写则保留视频原音轨。
          </p>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xl font-semibold">剪辑任务与导出</h2>
            <button onClick={loadAll} className="rounded-lg border border-white/20 px-4 py-2">
              刷新
            </button>
          </div>

          <div className="space-y-4">
            {jobs.map(job => (
              <div key={job.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="font-semibold">{job.title} · #{job.id}</h3>
                    <p className="mt-1 text-sm text-white/50">
                      {job.provider} / {job.model} · {job.status}
                    </p>
                  </div>
                  {job.status === "planned" && (
                    <button disabled={busy} onClick={() => executeJob(job.id)}
                      className="rounded-lg bg-indigo-600 px-5 py-2 disabled:opacity-40">
                      执行剪辑并导出
                    </button>
                  )}
                </div>

                {job.edit_plan && (
                  <div className="mt-4 space-y-2 text-sm">
                    <p className="text-white/70">{job.edit_plan.summary}</p>
                    <p>片段数量：{job.edit_plan.segments?.length || 0}</p>
                    <div className="max-h-48 space-y-1 overflow-auto text-white/50">
                      {job.edit_plan.segments?.map((segment: any, index: number) => (
                        <p key={index}>
                          {index + 1}. {segment.start}s – {segment.end}s：
                          {segment.description}
                        </p>
                      ))}
                    </div>
                  </div>
                )}

                {job.output_path && (
                  <p className="mt-3 break-all text-sm text-green-300">
                    导出文件：{job.output_path}
                  </p>
                )}
                {job.error && <p className="mt-3 text-sm text-red-300">{job.error}</p>}
              </div>
            ))}
            {jobs.length === 0 && <p className="text-white/40">暂无剪辑任务</p>}
          </div>
        </section>
      </div>
    </main>
  );
}
