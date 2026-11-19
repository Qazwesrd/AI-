"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type MediaFile = { id: number; name: string; category: string; path: string; size: number };
type EditJob = {
  id: number; title: string; provider: string; model: string; instruction: string;
  source_files?: string[]; status: string; edit_plan: any; output_path: string; error: string;
};
type Handoff = { id: number; prompt: string; manifest: any[]; notice: string };

export default function VideoEditorPage() {
  const [files, setFiles] = useState<MediaFile[]>([]);
  const [audioFiles, setAudioFiles] = useState<MediaFile[]>([]);
  const [subtitleFiles, setSubtitleFiles] = useState<MediaFile[]>([]);
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
  const [handoff, setHandoff] = useState<Handoff | null>(null);
  const [webResult, setWebResult] = useState("");

  async function loadAll() {
    try {
      const [fileResponse, audioResponse, otherResponse, jobResponse] = await Promise.all([
        fetch(`${API}/api/media/files?category=video`),
        fetch(`${API}/api/media/files?category=audio`),
        fetch(`${API}/api/media/files?category=other`),
        fetch(`${API}/api/video-editor/jobs`)
      ]);
      if (fileResponse.ok) setFiles(await fileResponse.json());
      if (audioResponse.ok) setAudioFiles(await audioResponse.json());
      if (otherResponse.ok) {
        const otherFiles: MediaFile[] = await otherResponse.json();
        setSubtitleFiles(otherFiles.filter(file => /\.(srt|ass|ssa|vtt)$/i.test(file.name)));
      }
      if (jobResponse.ok) setJobs(await jobResponse.json());
    } catch {
      setMessage("无法连接本地 API，请确认桌面软件服务已启动。");
    }
  }

  useEffect(() => { void loadAll(); }, []);

  async function uploadFiles(list: FileList | null) {
    if (!list?.length) return;
    setBusy(true);
    setMessage("正在上传视频……");
    try {
      for (const file of Array.from(list)) {
        const form = new FormData();
        form.append("file", file);
        form.append("category", "video");
        const response = await fetch(`${API}/api/media/files/upload`, { method: "POST", body: form });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "上传失败");
      }
      setMessage("视频上传完成。");
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "上传失败");
    } finally { setBusy(false); }
  }

  async function uploadAuxiliaryFiles(list: FileList | null, category: "audio" | "other") {
    if (!list?.length) return;
    setBusy(true);
    setMessage(category === "audio" ? "正在上传音轨文件……" : "正在上传字幕文件……");
    try {
      for (const file of Array.from(list)) {
        const form = new FormData();
        form.append("file", file);
        form.append("category", category);
        const response = await fetch(`${API}/api/media/files/upload`, { method: "POST", body: form });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "文件上传失败");
      }
      setMessage(category === "audio" ? "音轨文件已加入素材库。" : "字幕文件已加入素材库。");
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "文件上传失败");
    } finally {
      setBusy(false);
    }
  }

  async function createPlan() {
    if (!selected.length) return setMessage("请先选择至少一个视频文件。");
    setBusy(true);
    setMessage("正在调用已配置的 AI API 分析剪辑要求……");
    try {
      const response = await fetch(`${API}/api/video-editor/jobs`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, model, instruction, source_paths: selected, confirm_paid: confirmPaid })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "AI 分析失败");
      setMessage(`AI 剪辑方案已生成，任务编号：${data.id}`);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "AI 分析失败");
    } finally { setBusy(false); }
  }

  async function createWebHandoff() {
    if (!selected.length) return setMessage("请先选择至少一个视频文件。");
    if (!instruction.trim()) return setMessage("请先填写剪辑要求。");
    if (!window.confirm("将所选视频的文件名、时长、分辨率等元数据整理成交接包。视频文件本身不会上传到第三方网页。是否继续？")) return;
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/video-editor/web-handoff`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: "网页版 AI 视频剪辑任务",
          instruction, source_paths: selected, confirm_selected_videos: true
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "交接包生成失败");
      setHandoff(data);
      setWebResult("");
      setMessage(`网页版 AI 交接包已准备好，任务编号：${data.id}。请复制提示词到你选择的 AI 网页。`);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "交接包生成失败");
    } finally { setBusy(false); }
  }

  async function resumeWebHandoff(job: EditJob) {
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/video-editor/jobs/${job.id}/web-handoff`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "恢复交接任务失败");
      setHandoff(data);
      setWebResult("");
      setInstruction(job.instruction || "");
      setSelected(job.source_files || []);
      setMessage(`已恢复网页版 AI 剪辑任务 #${job.id}。你可以继续复制提示词、提交视频并导入结果。`);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "恢复交接任务失败");
    } finally { setBusy(false); }
  }

  async function copyHandoff() {
    if (!handoff?.prompt) return;
    try {
      await navigator.clipboard.writeText(handoff.prompt);
      setMessage("网页版 AI 剪辑提示词已复制。请在 AI 网页中按需要手动上传视频，再粘贴提示词。");
    } catch {
      setMessage("复制失败，请手动选择并复制提示词。");
    }
  }

  async function importWebResult() {
    if (!handoff || !webResult.trim()) return;
    setBusy(true);
    try {
      const cleaned = webResult.trim().replace(/^\`\`\`(?:json)?\s*/i, "").replace(/\s*\`\`\`$/, "");
      const plan = JSON.parse(cleaned);
      const response = await fetch(`${API}/api/video-editor/jobs/${handoff.id}/web-result`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "AI 结果导入失败");
      setMessage(data.message || "网页版 AI 剪辑方案已导入并校验。");
      setHandoff(null);
      setWebResult("");
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "AI 结果导入失败，请确认粘贴的是 JSON。");
    } finally { setBusy(false); }
  }

  async function executeJob(id: number) {
    setBusy(true);
    setMessage("FFmpeg 正在执行视频剪辑，请等待……");
    try {
      const response = await fetch(`${API}/api/video-editor/jobs/${id}/execute`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subtitle_path: subtitlePath || null, audio_path: audioPath || null })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "剪辑执行失败");
      setMessage(`视频导出完成：${data.output_path}`);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "剪辑失败");
      await loadAll();
    } finally { setBusy(false); }
  }

  function toggleFile(path: string) {
    setSelected(current => current.includes(path) ? current.filter(x => x !== path) : [...current, path]);
  }

  function formatSize(size: number) {
    return size >= 1024 ** 3 ? `${(size / 1024 ** 3).toFixed(2)} GB` : `${(size / 1024 ** 2).toFixed(1)} MB`;
  }

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6";

  return (
    <main className="cosmic min-h-screen p-5 text-white md:p-8">
      <div className="mx-auto max-w-7xl space-y-7">
        <header>
          <h1 className="text-3xl font-bold">AI 视频剪辑中心</h1>
          <p className="mt-2 text-white/60">可使用已配置的 AI API，也可把任务交给网页版 AI 分析，再由本地 FFmpeg 执行剪辑。</p>
        </header>

        {message && <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4">{message}</div>}

        <section className="grid gap-6 lg:grid-cols-2">
          <div className={panel}>
            <h2 className="mb-4 text-xl font-semibold">第一步：导入原视频</h2>
            <label className="block cursor-pointer rounded-xl border border-dashed border-purple-300/40 p-8 text-center hover:bg-purple-500/10">
              <span className="text-lg">选择视频文件</span>
              <p className="mt-2 text-sm text-white/50">支持多个视频，单文件上限 2GB</p>
              <input type="file" accept="video/*" multiple className="hidden" onChange={e => void uploadFiles(e.target.files)} />
            </label>
            <h3 className="mb-3 mt-6 font-semibold">本地视频素材</h3>
            <div className="max-h-72 space-y-2 overflow-auto">
              {files.map(file => (
                <label key={file.id} className="flex cursor-pointer items-center gap-3 rounded-lg border border-white/10 p-3">
                  <input type="checkbox" checked={selected.includes(file.path)} onChange={() => toggleFile(file.path)} />
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  <span className="text-xs text-white/50">{formatSize(file.size)}</span>
                </label>
              ))}
              {files.length === 0 && <p className="text-sm text-white/40">还没有导入视频</p>}
            </div>
          </div>

          <div className={panel}>
            <h2 className="mb-4 text-xl font-semibold">第二步：描述剪辑要求</h2>
            <label className="mb-2 block text-sm">AI 服务商 ID（内置 API 模式）</label>
            <input value={provider} onChange={e => setProvider(e.target.value)} className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm">AI 模型名称</label>
            <input value={model} onChange={e => setModel(e.target.value)} placeholder="例如 deepseek-chat" className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm">剪辑要求（两种 AI 模式共用）</label>
            <textarea value={instruction} onChange={e => setInstruction(e.target.value)} rows={7}
              placeholder="例如：保留人物说话和动作的部分，删除等待和空镜头。开头使用最精彩的画面，按故事发展顺序拼接，输出横屏 1080P 视频。"
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-4 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={confirmPaid} onChange={e => setConfirmPaid(e.target.checked)} />
              我确认内置 API 本次分析可能产生费用
            </label>
            <div className="flex flex-wrap gap-3">
              <button disabled={busy || !model.trim() || !instruction.trim() || !selected.length} onClick={() => void createPlan()}
                className="rounded-xl bg-purple-600 px-5 py-3 font-semibold disabled:opacity-40">使用内置 AI API</button>
              <button disabled={busy || !instruction.trim() || !selected.length} onClick={() => void createWebHandoff()}
                className="rounded-xl border border-cyan-300/40 bg-cyan-900/40 px-5 py-3 font-semibold text-cyan-100 disabled:opacity-40">使用网页版 AI</button>
            </div>
          </div>
        </section>

        {handoff && (
          <section className={panel}>
            <h2 className="text-xl font-semibold">网页版 AI 交接与结果导入 · 任务 #{handoff.id}</h2>
            <p className="mt-2 text-sm text-amber-200/90">{handoff.notice}</p>
            <div className="mt-4 rounded-xl bg-black/30 p-4">
              <h3 className="mb-2 font-medium">本次视频元数据</h3>
              {handoff.manifest.map((item: any) => (
                <p key={item.file_key} className="text-sm text-white/70">
                  {item.file_key}：{item.file_name} · {item.duration_seconds}s · {item.width || "?"}×{item.height || "?"}
                </p>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap gap-3">
              <button onClick={() => void copyHandoff()} className="rounded-lg bg-cyan-700 px-4 py-2.5">复制 AI 提示词</button>
              <a href="https://chatgpt.com/" target="_blank" rel="noreferrer" className="rounded-lg border border-white/20 px-4 py-2.5">打开 ChatGPT</a>
              <a href="https://www.deepseek.com/" target="_blank" rel="noreferrer" className="rounded-lg border border-white/20 px-4 py-2.5">打开 DeepSeek</a>
              <a href="https://www.doubao.com/" target="_blank" rel="noreferrer" className="rounded-lg border border-white/20 px-4 py-2.5">打开豆包</a>
            </div>
            <label className="mt-5 block text-sm font-medium">粘贴网页版 AI 返回的 JSON 剪辑方案</label>
            <textarea value={webResult} onChange={e => setWebResult(e.target.value)} rows={10}
              placeholder="把网页版 AI 返回的 JSON 原样粘贴到这里……"
              className="mt-2 w-full rounded-xl border border-white/15 bg-black/30 p-3 font-mono text-xs" />
            <button disabled={busy || !webResult.trim()} onClick={() => void importWebResult()}
              className="mt-3 rounded-lg bg-indigo-600 px-5 py-3 disabled:opacity-40">校验并导入剪辑方案</button>
          </section>
        )}

        <section className={panel}>
          <h2 className="mb-4 text-xl font-semibold">第三步：选择字幕与音轨（可选）</h2>
          <div className="grid gap-5 md:grid-cols-2">
            <div className="space-y-3">
              <label className="block text-sm font-medium">字幕文件</label>
              <select value={subtitlePath} onChange={e => setSubtitlePath(e.target.value)}
                className="w-full rounded-lg border border-white/15 bg-black/30 p-3">
                <option value="">不添加字幕</option>
                {subtitleFiles.map(file => <option key={file.id} value={file.path}>{file.name}</option>)}
              </select>
              <label className="block cursor-pointer rounded-lg border border-dashed border-purple-300/30 p-3 text-center text-sm hover:bg-purple-500/10">
                上传字幕（SRT / ASS / SSA / VTT）
                <input type="file" accept=".srt,.ass,.ssa,.vtt" multiple className="hidden"
                  onChange={e => void uploadAuxiliaryFiles(e.target.files, "other")} />
              </label>
              <p className="text-xs text-white/45">已上传 {subtitleFiles.length} 个字幕文件</p>
            </div>
            <div className="space-y-3">
              <label className="block text-sm font-medium">替换音轨</label>
              <select value={audioPath} onChange={e => setAudioPath(e.target.value)}
                className="w-full rounded-lg border border-white/15 bg-black/30 p-3">
                <option value="">保留原视频音轨</option>
                {audioFiles.map(file => <option key={file.id} value={file.path}>{file.name}</option>)}
              </select>
              <label className="block cursor-pointer rounded-lg border border-dashed border-purple-300/30 p-3 text-center text-sm hover:bg-purple-500/10">
                上传音频（MP3 / WAV / AAC / M4A 等）
                <input type="file" accept="audio/*" multiple className="hidden"
                  onChange={e => void uploadAuxiliaryFiles(e.target.files, "audio")} />
              </label>
              <p className="text-xs text-white/45">已上传 {audioFiles.length} 个音频文件</p>
            </div>
          </div>
          <p className="mt-3 text-xs text-white/45">选择的音轨会替换原视频音轨；不选择则保留原音轨。字幕和音轨会在最终导出时合成。</p>
        </section>

        <section className={panel}>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xl font-semibold">剪辑任务与导出</h2>
            <button onClick={() => void loadAll()} className="rounded-lg border border-white/20 px-4 py-2">刷新</button>
          </div>
          <div className="space-y-4">
            {jobs.map(job => (
              <div key={job.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div><h3 className="font-semibold">{job.title} · #{job.id}</h3>
                    <p className="mt-1 text-sm text-white/50">{job.provider} / {job.model} · {job.status}</p></div>
                  <div className="flex flex-wrap gap-2">
                    {job.status === "awaiting_web_ai" && <button disabled={busy} onClick={() => void resumeWebHandoff(job)} className="rounded-lg border border-cyan-300/40 bg-cyan-900/40 px-4 py-2 text-cyan-100 disabled:opacity-40">恢复网页版 AI 交接</button>}
                    {job.status === "planned" && <button disabled={busy} onClick={() => void executeJob(job.id)} className="rounded-lg bg-indigo-600 px-5 py-2 disabled:opacity-40">执行剪辑并导出</button>}
                  </div>
                </div>
                {job.edit_plan && <div className="mt-4 space-y-2 text-sm">
                  <p className="text-white/70">{job.edit_plan.summary}</p><p>片段数量：{job.edit_plan.segments?.length || 0}</p>
                  <div className="max-h-48 space-y-1 overflow-auto text-white/50">
                    {job.edit_plan.segments?.map((segment: any, index: number) => <p key={index}>{index + 1}. {segment.start}s – {segment.end}s：{segment.description}</p>)}
                  </div>
                </div>}
                {job.output_path && <p className="mt-3 break-all text-sm text-green-300">导出文件：{job.output_path}</p>}
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
