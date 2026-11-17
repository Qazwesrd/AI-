"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Novel = { id: number; title?: string; name?: string };
type Project = { id: number; novel_id: number; name: string; adaptation_style: string; status: string; generation_blocked?: boolean; source_change_notice?: { status?: string; detected_at?: string } | null };
type Episode = {
  id: number; episode_number: number; title: string; source_text: string;
  synopsis: string; screenplay: string; duration_seconds: number; status: string;
};
type Shot = {
  id: number; shot_number: number; scene_name: string; shot_type: string;
  camera_movement: string; duration_seconds: number; visual_description: string;
  character_actions: string; dialogue: string; sound_effects: string; image_prompt: string;
};
type VisualAsset = { id: number; category: string; name: string; description: string };
type VideoTask = { id: number; shot_id: number; shot_number: number; provider: string; model: string; status: string; remote_task_id: string; result_path: string; error: string };

const styles = [
  "3D动漫电影风格", "真人电影风格", "2D动漫风格",
  "国风漫画风格", "水墨动画风格", "自定义参考图风格",
];

export default function StoryboardPage() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [videoTasks, setVideoTasks] = useState<VideoTask[]>([]);
  const [assets, setAssets] = useState<VisualAsset[]>([]);
  const [novelId, setNovelId] = useState("");
  const [projectId, setProjectId] = useState<number | null>(null);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [episodeId, setEpisodeId] = useState<number | null>(null);
  const [projectName, setProjectName] = useState("");
  const [style, setStyle] = useState(styles[0]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [videoProvider, setVideoProvider] = useState("custom_video");
  const [videoModel, setVideoModel] = useState("");
  const [videoPaidConfirmed, setVideoPaidConfirmed] = useState(false);
  const [videoCreatePath, setVideoCreatePath] = useState("/videos/generations");
  const [videoStatusPath, setVideoStatusPath] = useState("/videos/{task_id}");
  const [videoIdField, setVideoIdField] = useState("id");
  const [videoUrlField, setVideoUrlField] = useState("video_url");
  const [videoReferenceMode, setVideoReferenceMode] = useState<"none" | "data_uri">("none");
  const [videoReferenceField, setVideoReferenceField] = useState("image");
  const [speechProvider, setSpeechProvider] = useState("openai");
  const [speechModel, setSpeechModel] = useState("");
  const [speechVoice, setSpeechVoice] = useState("alloy");
  const [speechPaidConfirmed, setSpeechPaidConfirmed] = useState(false);
  const [backgroundMusicPath, setBackgroundMusicPath] = useState("");

  const [shotForm, setShotForm] = useState({
    scene_name: "", shot_type: "中景", camera_movement: "固定镜头",
    duration_seconds: 5, visual_description: "", character_actions: "",
    dialogue: "", sound_effects: "", image_prompt: "",
  });

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5 shadow-xl shadow-purple-950/20";
  const field = "w-full rounded-xl border border-purple-300/20 bg-slate-900/80 px-3 py-2.5 text-sm text-white outline-none focus:border-purple-400";
  const button = "rounded-xl border border-purple-300/30 bg-purple-600/80 px-4 py-2 text-sm font-medium text-white transition hover:bg-purple-500 disabled:opacity-50";

  async function request(path: string, options?: RequestInit) {
    const response = await fetch(`${API}${path}`, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "请求失败");
    return result;
  }

  async function loadBase() {
    try {
      const [n, p, a] = await Promise.all([
        request("/api/story/novels"),
        request("/api/story/projects"),
        request("/api/story/assets"),
      ]);
      setNovels(n);
      setProjects(p);
      setAssets(a);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "读取数据失败");
    }
  }

  async function loadProject(id: number) {
    const result = await request(`/api/story/projects/${id}`);
    setProjectId(id);
    setSelectedProject(result.project || null);
    setEpisodes(result.episodes);
    setEpisodeId(null);
    setShots([]);
  }

  async function loadShots(id: number) {
    const result = await request(`/api/story/episodes/${id}/shots`);
    setShots(result);
    setEpisodeId(id);
    await loadEpisodeVideoTasks(id);
  }

  useEffect(() => { loadBase(); }, []);

  async function createProject() {
    if (!novelId) return setMessage("请先选择小说");
    setBusy(true);
    try {
      const result = await request("/api/story/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          novel_id: Number(novelId),
          name: projectName || "小说漫剧改编项目",
          adaptation_style: style,
          target_duration: 120,
          min_duration: 90,
          max_duration: 150,
        }),
      });
      setMessage("改编项目已创建");
      await loadBase();
      await loadProject(result.project.id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  async function planEpisodesAI() {
    if (!projectId) return setMessage("请先选择剧本项目");
    const approved = window.confirm("AI 将按每批最多3章的方式分析剧情；每章完成后立即保存。886章小说需要多批处理，过程中可关闭软件，之后再次点击会从上次进度继续。若当前模型为付费服务，可能产生费用。是否开始/继续？");
    if (!approved) return;
    setBusy(true);
    try {
      let done = false;
      let latest: { episodes?: Episode[]; message?: string; processed_chapters?: number; total_chapters?: number } | null = null;
      let batches = 0;
      while (!done && batches < 400) {
        const batchResult = await request(`/api/story/projects/${projectId}/plan-ai`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm_paid: true }),
        });
        latest = batchResult;
        done = Boolean(batchResult.done);
        setEpisodes(batchResult.episodes || []);
        setMessage(batchResult.message || "AI分集规划处理中");
        batches += 1;
      }
      if (!done) throw new Error("已达到单次连续执行批次数上限；当前进度已保存，再次点击即可继续。");
      setMessage(latest?.message || "AI分集规划完成");
    } catch (error) {
      setMessage((error instanceof Error ? error.message : "AI分集规划暂停") + "。已完成章节会保留，再次点击可继续。");
    } finally { setBusy(false); }
  }
  async function planEpisodes() {
    if (!projectId) return setMessage("请先选择剧本项目");
    setBusy(true);
    try {
      const result = await request(`/api/story/projects/${projectId}/plan`, { method: "POST" });
      setEpisodes(result.episodes);
      setMessage(result.message);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "分集规划失败");
    } finally {
      setBusy(false);
    }
  }

  async function generateEpisodeScriptAI(episode: Episode) {
    const approved = window.confirm("将调用已启用的文本 AI，根据本集原著生成影视剧本和对应分镜。若当前模型属于付费服务，可能产生 API 费用。是否继续？");
    if (!approved) return;
    setBusy(true);
    try {
      const result = await request(`/api/story/episodes/${episode.id}/generate-ai`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm_paid: true }),
      });
      if (projectId) await loadProject(projectId);
      await loadShots(episode.id);
      setMessage(result.message || "AI剧本与分镜生成完成");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "AI生成失败");
    } finally { setBusy(false); }
  }
  async function loadEpisodeVideoTasks(id: number) {
    try { const rows = await request("/api/media/video/episodes/" + id + "/tasks"); setVideoTasks(rows); }
    catch (error) { setMessage(error instanceof Error ? error.message : "读取视频任务失败"); }
  }

  async function refreshEpisodeVideoTasks(id: number) {
    setBusy(true);
    try {
      const result = await request("/api/media/video/episodes/" + id + "/refresh", { method: "POST" });
      await loadEpisodeVideoTasks(id);
      setMessage("已查询 " + result.checked + " 个镜头任务状态；请留意仍在排队或生成中的任务。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "查询视频任务失败"); }
    finally { setBusy(false); }
  }
  async function generateEpisodeVideos(episode: Episode) {
    if (!videoModel.trim()) return setMessage("请填写已开通的视频模型 ID");
    if (!window.confirm("将按每批最多3个镜头提交视频生成请求，并保存每个镜头的任务编号。每个镜头都可能产生费用，是否继续？")) return;
    setBusy(true);
    try {
      let done = false;
      let batches = 0;
      let totalCreated = 0;
      while (!done && batches < 12) {
        const result = await request("/api/media/video/episodes/" + episode.id + "/generate", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider: videoProvider, model: videoModel, batch_size: 3, confirm_paid: videoPaidConfirmed, create_path: videoCreatePath, status_path_template: videoStatusPath, task_id_field: videoIdField, video_url_field: videoUrlField, status_field: "status", aspect_ratio: "16:9", reference_image_mode: videoReferenceMode, reference_image_field: videoReferenceField }),
        });
        totalCreated += (result.created || []).length;
        done = Boolean(result.done);
        batches += 1;
        await loadEpisodeVideoTasks(episode.id);
        setMessage(result.message || "正在分批提交镜头视频任务");
        if (result.failures?.length) {
          throw new Error("有 " + result.failures.length + " 个镜头提交失败；已成功提交的任务已保存，请修正服务商配置后再次点击重试。");
        }
      }
      if (!done) throw new Error("本次已达到批次上限，已提交任务全部保存，再次点击即可继续。");
      setMessage("本集镜头视频任务提交完成，共提交 " + totalCreated + " 个任务。接下来查询任务状态并下载成片。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提交镜头视频失败");
    } finally { setBusy(false); }
  }

  async function generateEpisodeSpeech(episode: Episode) {
    if (!speechModel.trim()) return setMessage("请填写已开通的语音模型 ID");
    if (!window.confirm("将按每个分镜的对白/旁白生成语音，并按镜头时长合成整集配音轨。语音 API 可能产生费用，是否继续？")) return;
    setBusy(true);
    try {
      const result = await request("/api/media/speech/episodes/" + episode.id + "/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: speechProvider, model: speechModel, voice: speechVoice, confirm_paid: speechPaidConfirmed }),
      });
      setMessage(result.message || "本集对白配音完成");
    } catch (error) { setMessage(error instanceof Error ? error.message : "本集配音失败"); }
    finally { setBusy(false); }
  }
  async function renderEpisodeVideo(episode: Episode) {
    if (!window.confirm("只有本集所有镜头视频均已生成并下载到本地，才能合成。现在尝试合成吗？")) return;
    setBusy(true);
    try {
      const query = backgroundMusicPath.trim() ? "?music_path=" + encodeURIComponent(backgroundMusicPath.trim()) : "";
      const result = await request("/api/media/video/episodes/" + episode.id + "/render" + query, { method: "POST" });
      setMessage("第" + episode.episode_number + "集初版视频已合成：" + result.output);
    } catch (error) { setMessage(error instanceof Error ? error.message : "视频合成失败"); }
    finally { setBusy(false); }
  }
  async function updateEpisode(episode: Episode, patch: Partial<Episode>) {
    try {
      await request(`/api/story/episodes/${episode.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (projectId) await loadProject(projectId);
      setMessage("分集信息已保存");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    }
  }

  async function splitEpisode(id: number) {
    try {
      await request(`/api/story/episodes/${id}/split`, { method: "POST" });
      if (projectId) await loadProject(projectId);
      setMessage("分集已拆分");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "拆分失败");
    }
  }

  async function mergeEpisodes() {
    if (!projectId || episodes.length < 2) return;
    const ids = episodes.slice(0, 2).map(x => x.id);
    try {
      await request(`/api/story/projects/${projectId}/merge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ episode_ids: ids, title: "合并分集" }),
      });
      await loadProject(projectId);
      setMessage("前两集已合并");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "合并失败");
    }
  }

  async function moveEpisode(index: number, direction: number) {
    if (!projectId) return;
    const next = [...episodes];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    try {
      await request(`/api/story/projects/${projectId}/reorder`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ episode_ids: next.map(x => x.id) }),
      });
      await loadProject(projectId);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "排序失败");
    }
  }

  async function addShot() {
    if (!episodeId) return setMessage("请先选择分集");
    try {
      await request(`/api/story/episodes/${episodeId}/shots`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...shotForm,
          shot_number: shots.length + 1,
          sort_order: shots.length + 1,
        }),
      });
      await loadShots(episodeId);
      setMessage("分镜镜头已保存");
      setShotForm({
        scene_name: "", shot_type: "中景", camera_movement: "固定镜头",
        duration_seconds: 5, visual_description: "", character_actions: "",
        dialogue: "", sound_effects: "", image_prompt: "",
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建分镜失败");
    }
  }

  async function deleteShot(id: number) {
    try {
      await request(`/api/story/shots/${id}`, { method: "DELETE" });
      if (episodeId) await loadShots(episodeId);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "删除镜头失败");
    }
  }

  async function linkAsset(shot: Shot, assetId: number) {
    try {
      await request(`/api/story/shots/${shot.id}/assets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_id: assetId, role: "visual" }),
      });
      setMessage(`已将素材关联到镜头 #${shot.shot_number}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "素材关联失败");
    }
  }

  return (
    <main className="min-h-screen space-y-6 p-6 text-white">
      <header>
        <div className="text-sm text-purple-300">AI COMIC STUDIO / STORYBOARD</div>
        <h1 className="mt-2 text-3xl font-bold">剧本改编与智能分镜中心</h1>
        <p className="mt-2 text-sm text-slate-300">小说 → 分集规划 → 影视剧本 → 镜头分镜 → 视觉素材匹配</p>
      </header>

      {message && <div className="rounded-xl border border-purple-300/20 bg-purple-950/50 p-3 text-sm text-purple-100">{message}</div>}

      <section className={panel}>
        <h2 className="mb-4 text-xl font-semibold">创建小说改编项目</h2>
        <div className="grid gap-3 md:grid-cols-2">
          <select className={field} value={novelId} onChange={e => setNovelId(e.target.value)}>
            <option value="">选择已导入的小说</option>
            {novels.map(n => <option key={n.id} value={n.id}>{n.title || n.name || `小说 #${n.id}`}</option>)}
          </select>
          <input className={field} value={projectName} onChange={e => setProjectName(e.target.value)} placeholder="改编项目名称" />
          <select className={field} value={style} onChange={e => setStyle(e.target.value)}>
            {styles.map(s => <option key={s}>{s}</option>)}
          </select>
          <div className="rounded-xl border border-purple-300/15 bg-slate-900/70 p-3 text-sm text-slate-300">
            单集目标 120 秒；允许 90–150 秒。每章初步规划为 1 集，超长章节可拆为 2 集。
          </div>
        </div>
        <button className={`${button} mt-4`} disabled={busy} onClick={createProject}>创建改编项目</button>
      </section>

      <section className={panel}>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-xl font-semibold">改编项目与自动分集</h2>
          <select className={`${field} max-w-sm`} value={projectId ?? ""} onChange={e => e.target.value && loadProject(Number(e.target.value))}>
            <option value="">选择已有改编项目</option>
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}（#{p.id}）{p.status === "source_changed" ? " · 原文已更新/已暂停" : ""}</option>)}
          </select>
        </div>
        {selectedProject?.generation_blocked && (
          <div className="mb-4 rounded-xl border border-red-400/40 bg-red-950/40 p-4 text-sm text-red-100">
            <strong>原文版本已更新，当前项目已暂停制作</strong>
            <p className="mt-2 leading-6">此项目的分集、剧本和分镜属于重新导入前的旧版本。为防止新旧剧情混用，系统已阻止继续规划、生成剧本、视频、配音和成片。旧项目与已有成果全部保留。请在上方选择当前小说，新建一个改编项目并重新规划。</p>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <button className={button} disabled={!projectId || busy || Boolean(selectedProject?.generation_blocked)} onClick={planEpisodesAI}>AI智能分集规划</button>
          <button className="rounded-xl border border-purple-300/20 px-4 py-2 text-sm" disabled={!projectId || busy || Boolean(selectedProject?.generation_blocked)} onClick={planEpisodes}>按章节建立草案</button>
          <button className="rounded-xl border border-sky-300/30 bg-sky-800/60 px-4 py-2 text-sm" disabled={episodes.length < 2} onClick={mergeEpisodes}>合并前两集</button>
        </div>
        <p className="mt-2 text-xs text-slate-400">AI分集规划每批处理最多3章并逐章保存；中断后再次点击可从已保存进度继续。</p>
        <div className="mt-4 space-y-3">
          {episodes.map((ep, index) => (
            <article key={ep.id} className={`rounded-xl border p-4 ${episodeId === ep.id ? "border-purple-400 bg-purple-950/40" : "border-purple-300/15 bg-slate-900/70"}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <button className="text-left font-semibold hover:text-purple-300" onClick={() => loadShots(ep.id)}>
                  第 {ep.episode_number} 集：{ep.title}
                </button>
                <span className="text-xs text-slate-400">{ep.duration_seconds} 秒 · {ep.status}</span>
              </div>
              <p className="mt-2 line-clamp-2 text-xs text-slate-400">原著内容：{ep.source_text?.slice(0, 180) || "暂无内容"}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button className="rounded-lg border border-fuchsia-300/30 bg-fuchsia-700/40 px-3 py-1.5 text-xs disabled:opacity-50" disabled={busy || Boolean(selectedProject?.generation_blocked)} onClick={() => generateEpisodeScriptAI(ep)}>AI生成剧本与分镜</button>
                <button className="rounded-lg border border-cyan-300/30 bg-cyan-800/50 px-3 py-1.5 text-xs disabled:opacity-50" disabled={busy || Boolean(selectedProject?.generation_blocked)} onClick={() => generateEpisodeVideos(ep)}>按分镜生成视频</button>
                <button className="rounded-lg border border-amber-300/30 bg-amber-800/50 px-3 py-1.5 text-xs disabled:opacity-50" disabled={busy || Boolean(selectedProject?.generation_blocked)} onClick={() => generateEpisodeSpeech(ep)}>生成本集配音</button>
                <button className="rounded-lg border border-emerald-300/30 bg-emerald-800/50 px-3 py-1.5 text-xs disabled:opacity-50" disabled={busy || Boolean(selectedProject?.generation_blocked)} onClick={() => renderEpisodeVideo(ep)}>合成本集视频</button>
                <button className="rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs" onClick={() => loadShots(ep.id)}>打开分镜</button>
                <button className="rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs" onClick={() => splitEpisode(ep.id)}>拆分本集</button>
                <button className="rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs" onClick={() => moveEpisode(index, -1)}>上移</button>
                <button className="rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs" onClick={() => moveEpisode(index, 1)}>下移</button>
                <button className="rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs" onClick={() => updateEpisode(ep, { duration_seconds: Math.min(150, ep.duration_seconds + 10) })}>时长 +10 秒</button>
              </div>
            </article>
          ))}
          {episodes.length === 0 && <p className="text-sm text-slate-400">选择或创建项目后，生成分集计划。</p>}
        </div>
      </section>

      <section className={panel}>
        <h2 className="mb-4 text-xl font-semibold">分镜视频生成配置</h2>
        <p className="mb-3 text-xs text-slate-400">使用已在 AI 管理中心配置并启用的视频服务商，填写该服务商实际支持的模型 ID 和任务接口路径。当前会将分镜文字及已确认素材描述作为提示词；需要远程图片上传的专用图生视频接口还需按服务商协议单独适配。</p>
        <div className="grid gap-3 md:grid-cols-2">
          <input className={field} value={videoProvider} onChange={e => setVideoProvider(e.target.value)} placeholder="服务商 ID，例如 doubao" />
          <input className={field} value={videoModel} onChange={e => setVideoModel(e.target.value)} placeholder="视频模型 ID（必填）" />
          <input className={field} value={videoCreatePath} onChange={e => setVideoCreatePath(e.target.value)} placeholder="创建任务 API 路径" />
          <input className={field} value={videoStatusPath} onChange={e => setVideoStatusPath(e.target.value)} placeholder="查询任务路径，保留 {task_id}" />
          <input className={field} value={videoIdField} onChange={e => setVideoIdField(e.target.value)} placeholder="任务编号返回字段" />
          <input className={field} value={videoUrlField} onChange={e => setVideoUrlField(e.target.value)} placeholder="视频 URL 返回字段" />
        </div>
        <div className="mt-4 rounded-xl border border-cyan-300/20 bg-cyan-950/20 p-3">
          <label className="mb-2 block text-xs font-semibold text-cyan-100">分镜参考图输入（仅适用于明确支持 JSON Base64 Data URI 的视频 API）</label>
          <div className="grid gap-3 md:grid-cols-2">
            <select className={field} value={videoReferenceMode} onChange={e => setVideoReferenceMode(e.target.value as "none" | "data_uri")}>
              <option value="none">关闭参考图（纯文字提示词）</option>
              <option value="data_uri">发送已关联素材图片（Data URI）</option>
            </select>
            <input className={field} value={videoReferenceField} onChange={e => setVideoReferenceField(e.target.value)} placeholder="图片字段名，例如 image 或 input.image" />
          </div>
          <p className="mt-2 text-xs leading-5 text-slate-400">开启后会从本镜头关联的已确认视觉素材中选择一张图片，优先人物、其次场景和道具，作为 Base64 Data URI 发送。服务商必须明确支持这种字段和格式；不支持时请保持关闭。单张图片上限 8 MB。不会自动上传到第三方图床。</p>
        </div>
        <label className="mt-3 flex items-center gap-2 text-xs text-amber-200"><input type="checkbox" checked={videoPaidConfirmed} onChange={e => setVideoPaidConfirmed(e.target.checked)} />我已确认本次镜头视频生成可能产生费用</label>
        <div className="mt-5 border-t border-purple-300/15 pt-4">
          <h3 className="mb-3 text-sm font-semibold">对白配音配置</h3>
          <div className="grid gap-3 md:grid-cols-3">
            <input className={field} value={speechProvider} onChange={e => setSpeechProvider(e.target.value)} placeholder="语音服务商 ID" />
            <input className={field} value={speechModel} onChange={e => setSpeechModel(e.target.value)} placeholder="语音模型 ID" />
            <input className={field} value={speechVoice} onChange={e => setSpeechVoice(e.target.value)} placeholder="声音 ID" />
          </div>
          <label className="mt-3 flex items-center gap-2 text-xs text-amber-200"><input type="checkbox" checked={speechPaidConfirmed} onChange={e => setSpeechPaidConfirmed(e.target.checked)} />我已确认本次配音可能产生费用</label>
          <div className="mt-4">
            <label className="mb-2 block text-xs text-slate-300">背景音乐文件路径（可选，需先上传到媒体素材库）</label>
            <input className={field} value={backgroundMusicPath} onChange={e => setBackgroundMusicPath(e.target.value)} placeholder="填写 storage 目录中的背景音乐完整路径" />
          </div>
        </div>
      </section>

      <section className={panel}>
        <h2 className="mb-4 text-xl font-semibold">分镜镜头编辑器</h2>
        {episodeId && (
          <div className="mb-4 rounded-xl border border-cyan-300/20 bg-cyan-950/30 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong className="text-sm">本集视频任务</strong>
              <button className="rounded-lg border border-cyan-300/30 px-3 py-1.5 text-xs disabled:opacity-50" disabled={busy} onClick={() => refreshEpisodeVideoTasks(episodeId)}>查询并下载已完成视频</button>
            </div>
            <div className="mt-2 space-y-2">
              {videoTasks.map(task => (
                <div key={task.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-black/20 p-2 text-xs">
                  <span>镜头 {task.shot_number} · {task.provider}/{task.model} · {task.status}</span>
                  {task.result_path && <span className="break-all text-emerald-300">已下载到本地</span>}
                  {task.error && <span className="text-red-300">{task.error}</span>}
                </div>
              ))}
              {videoTasks.length === 0 && <p className="text-xs text-slate-400">尚未提交视频任务。</p>}
            </div>
          </div>
        )}
        {!episodeId ? (
          <p className="text-sm text-slate-400">请先在上方选择一集，进入镜头编辑。</p>
        ) : (
          <>
            <div className="grid gap-3 md:grid-cols-2">
              <input className={field} placeholder="场景名称" value={shotForm.scene_name} onChange={e => setShotForm({ ...shotForm, scene_name: e.target.value })} />
              <select className={field} value={shotForm.shot_type} onChange={e => setShotForm({ ...shotForm, shot_type: e.target.value })}>
                {["远景", "全景", "中景", "近景", "特写", "大特写", "俯拍", "仰拍", "主观镜头"].map(x => <option key={x}>{x}</option>)}
              </select>
              <select className={field} value={shotForm.camera_movement} onChange={e => setShotForm({ ...shotForm, camera_movement: e.target.value })}>
                {["固定镜头", "推进", "拉远", "横移", "跟拍", "环绕", "摇镜头", "升降", "手持"].map(x => <option key={x}>{x}</option>)}
              </select>
              <input className={field} type="number" min="1" max="120" value={shotForm.duration_seconds} onChange={e => setShotForm({ ...shotForm, duration_seconds: Number(e.target.value) })} placeholder="镜头时长（秒）" />
              <textarea className={`${field} md:col-span-2`} rows={2} placeholder="画面内容、构图、光线、环境" value={shotForm.visual_description} onChange={e => setShotForm({ ...shotForm, visual_description: e.target.value })} />
              <textarea className={field} rows={2} placeholder="人物动作、表情、走位" value={shotForm.character_actions} onChange={e => setShotForm({ ...shotForm, character_actions: e.target.value })} />
              <textarea className={field} rows={2} placeholder="对白 / 旁白" value={shotForm.dialogue} onChange={e => setShotForm({ ...shotForm, dialogue: e.target.value })} />
              <textarea className={field} rows={2} placeholder="音效与环境声" value={shotForm.sound_effects} onChange={e => setShotForm({ ...shotForm, sound_effects: e.target.value })} />
              <textarea className={`${field} md:col-span-2`} rows={3} placeholder="供图片或视频模型使用的镜头提示词" value={shotForm.image_prompt} onChange={e => setShotForm({ ...shotForm, image_prompt: e.target.value })} />
            </div>
            <button className={`${button} mt-3`} onClick={addShot}>保存分镜镜头</button>
          </>
        )}
        <div className="mt-5 space-y-3">
          {shots.map(shot => (
            <article key={shot.id} className="rounded-xl border border-purple-300/15 bg-slate-900/70 p-4">
              <div className="flex items-center justify-between gap-2">
                <h3 className="font-semibold">镜头 {shot.shot_number} · {shot.shot_type} · {shot.camera_movement}</h3>
                <button className="text-xs text-red-300" onClick={() => deleteShot(shot.id)}>删除</button>
              </div>
              <p className="mt-2 text-sm text-slate-300">{shot.visual_description || "尚未填写画面描述"}</p>
              <p className="mt-1 text-xs text-slate-400">场景：{shot.scene_name || "未指定"} · 时长：{shot.duration_seconds} 秒</p>
              {shot.dialogue && <p className="mt-2 text-sm text-purple-200">对白：{shot.dialogue}</p>}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="text-xs text-slate-400">关联视觉素材：</span>
                <select className="max-w-xs rounded-lg border border-purple-300/20 bg-slate-950 px-2 py-1 text-xs" defaultValue="" onChange={e => e.target.value && linkAsset(shot, Number(e.target.value))}>
                  <option value="">选择人物/场景/道具</option>
                  {assets.map(a => <option key={a.id} value={a.id}>{a.name}（{a.category}）</option>)}
                </select>
              </div>
            </article>
          ))}
          {episodeId && shots.length === 0 && <p className="text-sm text-slate-400">当前分集还没有分镜镜头。</p>}
        </div>
      </section>
    </main>
  );
}
