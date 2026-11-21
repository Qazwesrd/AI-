"use client";

import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
type ImageTask = { prompt_id: string; status: string; images?: Array<{ filename: string; subfolder: string; type: string }> };
type TargetShot = { id: number; shot_number: number; scene_name: string; visual_description: string };
type TargetEpisode = { id: number; episode_number: number; title: string; shots: TargetShot[] };
type TargetProject = { id: number; name: string; episodes: TargetEpisode[] };
type ShotImage = { link_id: number; media_file_id: number; name: string; mime_type: string | null; file_size: number; download_url: string };
const styles = ["国漫", "仿真人", "3D漫", "2D漫"] as const;

export default function LocalImageStudioPage() {
  const [models, setModels] = useState<string[]>([]);
  const [checkpoint, setCheckpoint] = useState("");
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("低质量、模糊、畸形肢体、多余手指、文字、水印");
  const [style, setStyle] = useState<(typeof styles)[number]>("国漫");
  const [width, setWidth] = useState("768");
  const [height, setHeight] = useState("768");
  const [steps, setSteps] = useState("24");
  const [seed, setSeed] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [task, setTask] = useState<ImageTask | null>(null);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [archiving, setArchiving] = useState<string | null>(null);
  const [archived, setArchived] = useState<Record<string, { id: number; name: string }>>({});
  const [targets, setTargets] = useState<TargetProject[]>([]);
  const [targetProjectId, setTargetProjectId] = useState("");
  const [targetEpisodeId, setTargetEpisodeId] = useState("");
  const [targetShotId, setTargetShotId] = useState("");
  const [shotImages, setShotImages] = useState<ShotImage[]>([]);
  const [shotImagesLoading, setShotImagesLoading] = useState(false);
  const [unlinkingImage, setUnlinkingImage] = useState<number | null>(null);

  const refreshModels = useCallback(async () => {
    setChecking(true); setError("");
    try {
      const response = await fetch(`${API}/api/local-ai/images/models`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "读取本地图片模型失败");
      const names = Array.isArray(data.models) ? data.models : [];
      setModels(names);
      setCheckpoint((current) => current && names.includes(current) ? current : (names[0] || ""));
      setMessage(names.length ? `发现 ${names.length} 个已安装图片模型。` : "ComfyUI 已连接，但尚未发现可用 checkpoint。");
      const targetResponse = await fetch(`${API}/api/local-ai/images/targets`, { cache: "no-store" });
      const targetData = await targetResponse.json();
      if (!targetResponse.ok) throw new Error(targetData.detail || "读取漫剧分镜列表失败");
      setTargets(Array.isArray(targetData.projects) ? targetData.projects : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "未连接本机 ComfyUI");
    } finally { setChecking(false); }
  }, []);

  useEffect(() => { void refreshModels(); }, [refreshModels]);


  useEffect(() => {
    if (!targetShotId) { setShotImages([]); return; }
    let active = true;
    setShotImagesLoading(true);
    fetch(API + "/api/local-ai/images/shots/" + targetShotId + "/media", { cache: "no-store" })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "读取分镜图片失败");
        if (active) setShotImages(Array.isArray(data.images) ? data.images : []);
      })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "读取分镜图片失败"); })
      .finally(() => { if (active) setShotImagesLoading(false); });
    return () => { active = false; };
  }, [targetShotId]);

  async function unlinkShotImage(linkId: number) {
    if (!targetShotId) return;
    setUnlinkingImage(linkId); setError("");
    try {
      const response = await fetch(API + "/api/local-ai/images/shots/" + targetShotId + "/media/" + linkId, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "解除图片关联失败");
      setShotImages((current) => current.filter((item) => item.link_id !== linkId));
      setMessage("已解除图片与分镜的关联；图片仍保留在本地素材库。");
    } catch (err) { setError(err instanceof Error ? err.message : "解除图片关联失败"); }
    finally { setUnlinkingImage(null); }
  }

  async function generate() {
    if (!checkpoint) { setError("请先安装并选择一个 ComfyUI 图片模型。"); return; }
    if (!prompt.trim()) { setError("请填写图片提示词。"); return; }
    if (!confirmed) { setError("请先确认本地生成会占用电脑资源。"); return; }
    setLoading(true); setError(""); setMessage(""); setTask(null);
    try {
      const body: Record<string, unknown> = {
        checkpoint, prompt, negative_prompt: negative, style,
        width: Number(width), height: Number(height), steps: Number(steps),
        confirm_local_generation: true,
      };
      if (seed.trim()) body.seed = Number(seed);
      const response = await fetch(`${API}/api/local-ai/images/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "本地图片任务提交失败");
      setTask({ prompt_id: data.prompt_id, status: data.status });
      setMessage(`任务已提交到本机 ComfyUI。随机种子：${data.seed}`);
      setConfirmed(false);
    } catch (err) { setError(err instanceof Error ? err.message : "本地图片生成失败；没有切换到云端。"); }
    finally { setLoading(false); }
  }


  async function archiveImage(image: { filename: string; subfolder: string; type: string }) {
    if (!task?.prompt_id) return;
    const key = image.filename + "|" + image.subfolder;
    setArchiving(key); setError(""); setMessage("");
    try {
      const response = await fetch(`${API}/api/local-ai/images/tasks/${encodeURIComponent(task.prompt_id)}/archive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: image.filename, subfolder: image.subfolder, type: image.type || "output",
          ...(targetShotId ? { shot_id: Number(targetShotId) } : {}),
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "图片归档失败");
      setArchived((current) => ({ ...current, [key]: { id: data.media_file_id, name: data.name } }));
      setMessage(data.message || `已归档到素材库，素材编号：${data.media_file_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "图片归档失败；原始图片仍保留在 ComfyUI 输出目录。");
    } finally { setArchiving(null); }
  }

  async function refreshTask() {
    if (!task?.prompt_id) return;
    setChecking(true); setError("");
    try {
      const response = await fetch(`${API}/api/local-ai/images/tasks/${encodeURIComponent(task.prompt_id)}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "任务状态读取失败");
      setTask(data);
      setMessage(data.status === "completed" ? "本地图片生成已完成。请在 ComfyUI 输出目录查看图片。" : "任务仍在本机队列中或正在生成。");
    } catch (err) { setError(err instanceof Error ? err.message : "任务状态读取失败"); }
    finally { setChecking(false); }
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-6 text-white">
      <header>
        <p className="text-sm text-fuchsia-200">AI 漫剧制作大师 · 本地 AI</p>
        <h1 className="mt-2 text-3xl font-bold">本地图片生成工作台</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">使用已安装并正在运行的 ComfyUI 和本地图片模型生成图片。所有生成请求只发送到本机 127.0.0.1:8188，不调用云端 API。</p>
      </header>

      <section className="rounded-2xl border border-white/10 bg-slate-950/70 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-lg font-semibold">本地图片模型</h2><p className="mt-1 text-sm text-white/50">仅列出已安装的 checkpoint，不会自动下载。</p></div>
          <button type="button" onClick={() => void refreshModels()} disabled={checking} className="rounded-lg border border-white/20 px-3 py-2 text-sm disabled:opacity-40">{checking ? "检测中…" : "重新检测"}</button>
        </div>
        <label className="mt-4 block text-sm text-white/70">图片模型</label>
        <select value={checkpoint} onChange={(event) => setCheckpoint(event.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-3" disabled={!models.length}>
          {!models.length && <option value="">未发现已安装模型</option>}
          {models.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      </section>

      <section className="rounded-2xl border border-white/10 bg-slate-950/70 p-5">
        <h2 className="text-lg font-semibold">图片内容与画面参数</h2>
        <label className="mt-4 block text-sm text-white/70">正向提示词</label>
        <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} rows={6} maxLength={6000} placeholder="例如：一位身穿白金铠甲的少年站在古老神殿前，电影级构图，半身近景……" className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-3" />
        <label className="mt-4 block text-sm text-white/70">反向提示词</label>
        <textarea value={negative} onChange={(event) => setNegative(event.target.value)} rows={2} maxLength={3000} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-3" />
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <label className="text-sm text-white/70">图片风格<select value={style} onChange={(event) => setStyle(event.target.value as (typeof styles)[number])} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-2">{styles.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label className="text-sm text-white/70">宽度<select value={width} onChange={(event) => setWidth(event.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-2">{["512","640","768","832","1024","1152","1280","1536"].map((v)=><option key={v}>{v}</option>)}</select></label>
          <label className="text-sm text-white/70">高度<select value={height} onChange={(event) => setHeight(event.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-2">{["512","640","768","832","1024","1152","1280","1536"].map((v)=><option key={v}>{v}</option>)}</select></label>
          <label className="text-sm text-white/70">采样步数<select value={steps} onChange={(event) => setSteps(event.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-2">{["12","16","20","24","28","32","36","40"].map((v)=><option key={v}>{v}</option>)}</select></label>
          <label className="text-sm text-white/70">随机种子（可选）<input value={seed} onChange={(event) => setSeed(event.target.value.replace(/\D/g, "").slice(0,10))} placeholder="自动生成" className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-2" /></label>
        </div>
      </section>

      <section className="rounded-2xl border border-amber-300/20 bg-amber-950/20 p-5">
        <label className="flex items-start gap-3 text-sm leading-6 text-amber-100/90">
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-1" />
          我确认使用本机 CPU/GPU 进行图片生成。生成会占用内存、显存、电力和时间；模型必须已安装，不会产生 API 费用，也不会上传提示词到云端。
        </label>
        <button type="button" onClick={() => void generate()} disabled={loading || !models.length} className="mt-4 rounded-xl bg-fuchsia-300 px-5 py-3 font-semibold text-slate-950 disabled:opacity-40">{loading ? "正在提交本地任务…" : "确认并开始本地生成"}</button>
      </section>

      {error && <p role="alert" className="rounded-xl border border-rose-400/30 bg-rose-950/30 p-3 text-sm text-rose-100">{error}</p>}
      {message && <p className="rounded-xl border border-emerald-300/20 bg-emerald-950/20 p-3 text-sm text-emerald-100">{message}</p>}
      <section className="rounded-2xl border border-cyan-300/20 bg-cyan-950/15 p-5">
        <h2 className="text-lg font-semibold">图片归档目标（可选）</h2>
        <p className="mt-1 text-sm text-white/55">归档时可指定图片属于哪一个项目、分集和分镜。留空则只保存到素材库，不会自动关联。</p>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <label className="text-sm text-white/70">漫剧项目
            <select value={targetProjectId} onChange={(event) => { setTargetProjectId(event.target.value); setTargetEpisodeId(""); setTargetShotId(""); }} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-3">
              <option value="">不关联项目</option>
              {targets.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </label>
          <label className="text-sm text-white/70">分集
            <select value={targetEpisodeId} onChange={(event) => { setTargetEpisodeId(event.target.value); setTargetShotId(""); }} disabled={!targetProjectId} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-3 disabled:opacity-40">
              <option value="">选择分集</option>
              {(targets.find((project) => String(project.id) === targetProjectId)?.episodes || []).map((episode) => <option key={episode.id} value={episode.id}>第{episode.episode_number}集 · {episode.title}</option>)}
            </select>
          </label>
          <label className="text-sm text-white/70">分镜镜头
            <select value={targetShotId} onChange={(event) => setTargetShotId(event.target.value)} disabled={!targetEpisodeId} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-3 disabled:opacity-40">
              <option value="">选择镜头</option>
              {(targets.flatMap((project) => project.episodes).find((episode) => String(episode.id) === targetEpisodeId)?.shots || []).map((shot) => <option key={shot.id} value={shot.id}>镜头 {shot.shot_number} · {shot.scene_name || "未命名场景"}</option>)}
            </select>
          </label>
        </div>
        {targetProjectId && targetEpisodeId && targetShotId && <p className="mt-3 text-xs text-cyan-100">已选定关联目标；归档图片时会同时保存并关联到该镜头。</p>}
      </section>


      {targetShotId && <section className="rounded-2xl border border-white/10 bg-slate-950/70 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">当前分镜已关联图片</h2>
          <span className="text-xs text-white/50">{shotImages.length} 张</span>
        </div>
        {shotImagesLoading ? <p className="mt-3 text-sm text-white/50">正在读取图片…</p> : shotImages.length ? <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {shotImages.map((item) => <article key={item.link_id} className="overflow-hidden rounded-xl border border-white/10 bg-slate-900/70">
            <img src={API + item.download_url} alt={item.name} loading="lazy" className="aspect-square w-full object-contain bg-black/30" />
            <div className="space-y-2 p-3">
              <p className="break-all text-xs text-white/70">{item.name}</p>
              <p className="text-xs text-white/40">素材编号 #{item.media_file_id} · {(item.file_size / 1024 / 1024).toFixed(2)} MiB</p>
              <button type="button" onClick={() => void unlinkShotImage(item.link_id)} disabled={unlinkingImage !== null} className="rounded-lg border border-rose-300/30 px-3 py-2 text-xs text-rose-100 disabled:opacity-40">{unlinkingImage === item.link_id ? "正在解除…" : "仅解除分镜关联"}</button>
            </div>
          </article>)}
        </div> : <p className="mt-3 text-sm text-white/50">该分镜暂时没有关联图片。归档新图片时选择此镜头即可添加。</p>}
        <p className="mt-3 text-xs text-white/40">解除关联不会删除素材库中的原图，也不会影响其他镜头使用该图片。</p>
      </section>}

      {task && <section className="rounded-2xl border border-white/10 bg-slate-950/70 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">本地生成任务</h2><p className="mt-1 break-all text-xs text-white/50">任务编号：{task.prompt_id}</p></div><button type="button" onClick={() => void refreshTask()} disabled={checking} className="rounded-lg border border-white/20 px-3 py-2 text-sm disabled:opacity-40">{checking ? "查询中…" : "刷新任务状态"}</button></div>
        <p className="mt-3 text-sm">状态：{task.status === "completed" ? "已完成" : task.status === "running" ? "生成中" : "排队中"}</p>
        {task.images?.length ? <div className="mt-4 space-y-3">{task.images.map((image) => {
          const key = image.filename + "|" + image.subfolder;
          const saved = archived[key];
          return <div key={key} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-slate-900/70 p-3">
            <div className="min-w-0"><p className="break-all text-sm text-white/80">{image.filename}</p><p className="mt-1 text-xs text-white/40">ComfyUI 输出目录 · {image.type || "output"}</p>
              {saved && <p className="mt-1 text-xs text-emerald-200">已归档 · 素材编号 #{saved.id}</p>}
            </div>
            <button type="button" onClick={() => void archiveImage(image)} disabled={Boolean(archiving) || Boolean(saved)} className="rounded-lg border border-emerald-300/30 px-3 py-2 text-sm text-emerald-100 disabled:opacity-40">{archiving === key ? "正在归档…" : saved ? "已归档" : "归档到素材库"}</button>
          </div>;
        })}</div> : null}
      </section>}
      <p className="text-xs leading-5 text-white/40">当前版本采用 ComfyUI 标准 checkpoint 文生图工作流。图片归档需要逐张点击确认；归档后会登记到本地素材库。ControlNet、LoRA、参考图和批量镜头生成将在后续阶段增加。若模型架构与标准 checkpoint 工作流不兼容，ComfyUI 会返回错误，不会尝试云端替代。</p>
    </main>
  );
}
