"use client";

import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
type ImageTask = { prompt_id: string; status: string; images?: Array<{ filename: string; subfolder: string; type: string }> };
type TargetShot = { id: number; shot_number: number; scene_name: string; visual_description: string };
type TargetEpisode = { id: number; episode_number: number; title: string; shots: TargetShot[] };
type TargetProject = { id: number; name: string; episodes: TargetEpisode[] };
type ShotImage = { link_id: number; media_file_id: number; name: string; mime_type: string | null; file_size: number; download_url: string };
type LibraryImage = { id: number; name: string; mime_type: string | null; file_size: number; download_url: string };
type ImageVersion = { version_number: number; media_file_id: number; name: string; file_size: number; download_url: string; current: boolean };
const styles = ["国漫", "仿真人", "3D漫", "2D漫"] as const;


type ConsistencyResult = {
  ready: boolean;
  shot_count: number;
  missing_profile_count: number;
  missing_profiles: { characters: string[]; scenes: string[]; props: string[] };
  shots: Array<{ shot_id: number; shot_number: number; prompt_preview: string; consistency_rules_applied: boolean }>;
  message: string;
};

function parseProfileLines(value: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of value.split(/\\r?\\n/)) {
    const match = line.match(/^\\s*(\\d+)\\s*[=:：]\\s*(.+)\\s*$/);
    if (match) result[match[1]] = match[2].trim();
  }
  return result;
}

function formatProfileLines(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  return Object.entries(value as Record<string, unknown>)
    .map(([id, description]) => typeof description === "string" ? `${id}：${description}` : "")
    .filter(Boolean).join("\\n");
}

function StoryboardConsistencyPanel({ projectId }: { projectId: string }) {
  const [visualStyle, setVisualStyle] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [characters, setCharacters] = useState("");
  const [scenes, setScenes] = useState("");
  const [props, setProps] = useState("");
  const [result, setResult] = useState<ConsistencyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!projectId) { setResult(null); return; }
    let active = true;
    setBusy(true); setError(""); setNotice(""); setResult(null);
    fetch(`${API}/api/local-ai/images/projects/${projectId}/consistency`, { cache: "no-store" })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "读取一致性档案失败");
        if (!active) return;
        const profile = data.profile || {};
        setVisualStyle(profile.visual_style || "");
        setNegativePrompt(profile.negative_prompt || "");
        setCharacters(formatProfileLines(profile.character_profiles));
        setScenes(formatProfileLines(profile.scene_profiles));
        setProps(formatProfileLines(profile.prop_profiles));
      })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "读取一致性档案失败"); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [projectId]);

  async function saveProfile() {
    if (!projectId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${API}/api/local-ai/images/projects/${projectId}/consistency`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          visual_style: visualStyle, negative_prompt: negativePrompt,
          character_profiles: parseProfileLines(characters),
          scene_profiles: parseProfileLines(scenes), prop_profiles: parseProfileLines(props),
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "保存一致性档案失败");
      setNotice("统一外观档案已保存。接下来先执行批量检查。");
    } catch (err) { setError(err instanceof Error ? err.message : "保存一致性档案失败"); }
    finally { setBusy(false); }
  }

  async function runPreflight(apply: boolean) {
    if (!projectId) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${API}/api/local-ai/images/projects/${projectId}/consistency/${apply ? "apply" : "preflight"}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ shot_ids: [] }),
      });
      const data = await response.json();
      if (!response.ok) {
        if (data.detail) throw new Error(data.detail);
        throw new Error("批量一致性检查失败");
      }
      setResult(data.preflight || data);
      setNotice(apply ? `已将统一外观规则写入 ${data.updated_shot_count} 个分镜提示词。` : "检查完成。先补齐缺失档案，再点击应用。");
    } catch (err) { setError(err instanceof Error ? err.message : "批量一致性检查失败"); }
    finally { setBusy(false); }
  }

  if (!projectId) return null;
  return <section className="space-y-4 rounded-2xl border border-violet-300/25 bg-violet-950/15 p-5">
    <div><h2 className="text-lg font-semibold">跨分镜角色、场景与道具一致性</h2>
      <p className="mt-1 text-sm text-white/55">为同一个角色固定外观描述，为场景和道具固定视觉特征。先检查，再明确应用到本项目最多 200 个分镜。</p></div>
    <label className="block text-sm text-white/70">全项目统一画风
      <textarea value={visualStyle} onChange={(event) => setVisualStyle(event.target.value)} rows={2} maxLength={1200} placeholder="例如：国漫电影质感，统一线稿、色彩、光照和镜头质感" className="mt-2 w-full rounded-lg border border-white/15 bg-slate-950 p-3" />
    </label>
    <label className="block text-sm text-white/70">全项目统一避免项
      <textarea value={negativePrompt} onChange={(event) => setNegativePrompt(event.target.value)} rows={2} maxLength={2000} placeholder="例如：避免角色换脸、发色变化、服装变色、比例变化、文字水印" className="mt-2 w-full rounded-lg border border-white/15 bg-slate-950 p-3" />
    </label>
    <div className="grid gap-3 md:grid-cols-3">
      <label className="block text-sm text-white/70">角色外观档案
        <textarea value={characters} onChange={(event) => setCharacters(event.target.value)} rows={5} placeholder={"每行一条：素材编号：固定外观描述\\n例如：12：银色长发，蓝色眼睛，白金圣衣"} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-950 p-3 text-xs" />
      </label>
      <label className="block text-sm text-white/70">场景外观档案
        <textarea value={scenes} onChange={(event) => setScenes(event.target.value)} rows={5} placeholder={"每行一条：场景素材编号：固定场景描述"} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-950 p-3 text-xs" />
      </label>
      <label className="block text-sm text-white/70">道具外观档案
        <textarea value={props} onChange={(event) => setProps(event.target.value)} rows={5} placeholder={"每行一条：道具素材编号：固定外观描述"} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-950 p-3 text-xs" />
      </label>
    </div>
    <p className="text-xs text-white/45">编号对应项目中角色、场景和道具的素材编号；每行格式为“编号：描述”。保存档案不会自动修改分镜。</p>
    <div className="flex flex-wrap gap-2">
      <button type="button" onClick={() => void saveProfile()} disabled={busy} className="rounded-lg border border-violet-300/30 px-4 py-2 text-sm text-violet-100 disabled:opacity-40">{busy ? "处理中…" : "保存统一外观档案"}</button>
      <button type="button" onClick={() => void runPreflight(false)} disabled={busy} className="rounded-lg border border-white/20 px-4 py-2 text-sm disabled:opacity-40">批量检查一致性</button>
      <button type="button" onClick={() => void runPreflight(true)} disabled={busy} className="rounded-lg border border-emerald-300/30 px-4 py-2 text-sm text-emerald-100 disabled:opacity-40">应用到全部分镜提示词</button>
    </div>
    {error && <p role="alert" className="text-sm text-rose-200">{error}</p>}
    {notice && <p className="text-sm text-emerald-200">{notice}</p>}
    {result && <div className="space-y-2 rounded-xl border border-white/10 bg-black/20 p-3">
      <p className="text-sm">检查了 {result.shot_count} 个分镜；缺失外观档案 {result.missing_profile_count} 项。</p>
      {result.missing_profile_count > 0 && <div className="text-xs text-amber-200">
        {result.missing_profiles.characters.length > 0 && <p>缺少角色档案：{result.missing_profiles.characters.join("、")}</p>}
        {result.missing_profiles.scenes.length > 0 && <p>缺少场景档案：{result.missing_profiles.scenes.join("、")}</p>}
        {result.missing_profiles.props.length > 0 && <p>缺少道具档案：{result.missing_profiles.props.join("、")}</p>}
      </div>}
      <p className="text-xs text-white/50">{result.message}</p>
      <details className="text-xs text-white/60"><summary className="cursor-pointer">查看分镜提示词预览</summary>
        <div className="mt-2 max-h-64 space-y-2 overflow-auto">{result.shots.map((shot) => <div key={shot.shot_id} className="rounded bg-black/20 p-2"><p className="font-medium">镜头 {shot.shot_number} · #{shot.shot_id}</p><pre className="mt-1 whitespace-pre-wrap break-words">{shot.prompt_preview || "尚无图片提示词或一致性规则"}</pre></div>)}</div>
      </details>
    </div>}
    <p className="text-xs text-white/40">本功能仅生成和保存本地提示词规则，不会调用云端 AI，也不会自动开始生图；ComfyUI 仍需用户单独确认生成。</p>
  </section>;
}


type ReferenceCapabilityResult = {
  reference_conditioning_ready: boolean;
  reference_conditioning_nodes_ready: boolean;
  reference_conditioning_models_ready: boolean;
  missing_node_types: string[];
  missing_model_files: string[];
  reference_nodes: Record<string, { available: boolean; input_names?: string[]; adapter_models?: string[]; clip_vision_models?: string[]; reason?: string }>;
  message: string;
};

function ReferenceCapabilityPanel() {
  const [result, setResult] = useState<ReferenceCapabilityResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  async function inspect() {
    setChecking(true); setError("");
    try {
      const response = await fetch(`${API}/api/local-ai/images/reference-capabilities`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "参考图兼容性检测失败");
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "参考图兼容性检测失败");
    } finally { setChecking(false); }
  }

  return <section className="space-y-3 rounded-2xl border border-cyan-300/20 bg-cyan-950/10 p-5">
    <div><h2 className="text-lg font-semibold">本机参考图生图兼容性</h2>
      <p className="mt-1 text-sm text-white/55">检查 ComfyUI 是否已有 IP-Adapter、CLIP Vision、Load Image 节点及对应模型。检测只读取本机节点信息，不安装、不下载、不生成。</p></div>
    <button type="button" onClick={() => void inspect()} disabled={checking} className="rounded-lg border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100 disabled:opacity-40">{checking ? "正在检测本机节点…" : "检测参考图生图环境"}</button>
    {error && <p role="alert" className="text-sm text-rose-200">{error}</p>}
    {result && <div className="space-y-3 rounded-xl border border-white/10 bg-black/20 p-3">
      <p className="text-sm">{result.message}</p>
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg bg-white/5 p-3"><p className="text-xs text-white/50">节点条件</p><p className="mt-1 text-sm">{result.reference_conditioning_nodes_ready ? "已满足" : "未满足"}</p></div>
        <div className="rounded-lg bg-white/5 p-3"><p className="text-xs text-white/50">模型文件</p><p className="mt-1 text-sm">{result.reference_conditioning_models_ready ? "已发现" : "未发现完整组合"}</p></div>
        <div className="rounded-lg bg-white/5 p-3"><p className="text-xs text-white/50">整体状态</p><p className="mt-1 text-sm">{result.reference_conditioning_ready ? "可以进入工作流适配" : "需要补齐环境"}</p></div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {Object.entries(result.reference_nodes).map(([name, node]) => <div key={name} className="rounded-lg border border-white/10 p-2 text-xs">
          <p className="flex items-center justify-between gap-2"><span className="font-medium">{name}</span><span className={node.available ? "text-emerald-300" : "text-amber-200"}>{node.available ? "已安装" : "未发现"}</span></p>
          {node.adapter_models && <p className="mt-1 break-words text-white/50">IP-Adapter 模型：{node.adapter_models.length ? node.adapter_models.join("、") : "未发现"}</p>}
          {node.clip_vision_models && <p className="mt-1 break-words text-white/50">CLIP Vision 模型：{node.clip_vision_models.length ? node.clip_vision_models.join("、") : "未发现"}</p>}
        </div>)}
      </div>
      {result.missing_node_types.length > 0 && <p className="text-xs text-amber-200">缺少节点：{result.missing_node_types.join("、")}</p>}
      {result.missing_model_files.length > 0 && <p className="text-xs text-amber-200">缺少模型：{result.missing_model_files.join("、")}</p>}
      {result.reference_conditioning_ready && <p className="text-xs text-emerald-200">本机环境已具备基本组件；仍需对实际 ComfyUI 工作流的输入接口进行验证，不能仅凭节点存在就保证所有模型组合兼容。</p>}
    </div>}
  </section>;
}



type BatchPreflight = {
  requested_count: number; ready_count: number; missing_count: number;
  ready_shots: Array<{ shot_id: number; shot_number: number; scene_name: string; prompt: string }>;
  missing_shots: Array<{ shot_id: number; shot_number?: number; reason: string }>;
  message: string;
};
type BatchSubmitResult = {
  submitted_count: number; skipped_count: number; failed_count: number;
  tasks: Array<{ shot_id: number; shot_number: number; prompt_id: string; seed: number; status: string }>;
  failed_shots: Array<{ shot_id: number; shot_number: number; reason: string }>;
  skipped_shots: Array<{ shot_id: number; shot_number?: number; reason: string }>;
  message: string;
};

function StoryboardBatchImagePanel({ projects, checkpoint, style, width, height, steps }: {
  projects: TargetProject[]; checkpoint: string; style: (typeof styles)[number];
  width: string; height: string; steps: string;
}) {
  const [projectId, setProjectId] = useState("");
  const [episodeId, setEpisodeId] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [preflight, setPreflight] = useState<BatchPreflight | null>(null);
  const [result, setResult] = useState<BatchSubmitResult | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const project = projects.find((item) => String(item.id) === projectId);
  const episode = project?.episodes.find((item) => String(item.id) === episodeId);
  const shots = episode?.shots || [];

  function toggleShot(id: number) {
    setSelected((current) => current.includes(id) ? current.filter((value) => value !== id) : current.length >= 20 ? current : [...current, id]);
    setPreflight(null); setResult(null);
  }

  async function runPreflight() {
    if (!projectId || !episodeId || !selected.length || !checkpoint) return;
    setBusy(true); setError(""); setPreflight(null); setResult(null);
    try {
      const response = await fetch(`${API}/api/local-ai/images/batch/preflight`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: Number(projectId), episode_id: Number(episodeId), shot_ids: selected, checkpoint, style, width: Number(width), height: Number(height), steps: Number(steps) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "批量预检失败");
      setPreflight(data);
    } catch (err) { setError(err instanceof Error ? err.message : "批量预检失败"); }
    finally { setBusy(false); }
  }

  async function submitBatch() {
    if (!preflight?.ready_count || !confirmed) return;
    setBusy(true); setError(""); setResult(null);
    try {
      const response = await fetch(`${API}/api/local-ai/images/batch/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: Number(projectId), episode_id: Number(episodeId), shot_ids: selected, checkpoint, style, width: Number(width), height: Number(height), steps: Number(steps), confirm_local_generation: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "批量任务提交失败");
      setResult(data); setConfirmed(false);
    } catch (err) { setError(err instanceof Error ? err.message : "批量任务提交失败；没有切换到云端。"); }
    finally { setBusy(false); }
  }

  return <section className="space-y-4 rounded-2xl border border-fuchsia-300/20 bg-fuchsia-950/10 p-5">
    <div><h2 className="text-lg font-semibold">分镜批量生图队列</h2>
      <p className="mt-1 text-sm text-white/55">按分集选择最多 20 个分镜，先检查提示词，再由你确认后逐个提交到本机 ComfyUI。不会自动归档图片。</p></div>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm text-white/70">漫剧项目
        <select value={projectId} onChange={(event) => { setProjectId(event.target.value); setEpisodeId(""); setSelected([]); setPreflight(null); setResult(null); }} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-950 p-3">
          <option value="">选择项目</option>{projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      <label className="text-sm text-white/70">分集
        <select value={episodeId} onChange={(event) => { setEpisodeId(event.target.value); setSelected([]); setPreflight(null); setResult(null); }} disabled={!projectId} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-950 p-3 disabled:opacity-40">
          <option value="">选择分集</option>{(project?.episodes || []).map((item) => <option key={item.id} value={item.id}>第{item.episode_number}集 · {item.title}</option>)}
        </select>
      </label>
    </div>
    {episode && <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm text-white/70">选择需要生成图片的分镜（已选 {selected.length}/20）</p>
        <button type="button" onClick={() => { setSelected(shots.slice(0, 20).map((item) => item.id)); setPreflight(null); setResult(null); }} className="text-xs text-fuchsia-200">选择本集前 20 个</button>
      </div>
      <div className="max-h-64 space-y-1 overflow-auto rounded-xl border border-white/10 bg-black/20 p-2">
        {shots.map((shot) => <label key={shot.id} className="flex cursor-pointer items-start gap-2 rounded-lg p-2 text-sm hover:bg-white/5">
          <input type="checkbox" checked={selected.includes(shot.id)} onChange={() => toggleShot(shot.id)} disabled={!selected.includes(shot.id) && selected.length >= 20} className="mt-1" />
          <span><span className="text-white/85">镜头 {shot.shot_number} · {shot.scene_name || "未命名场景"}</span><span className="mt-1 block text-xs text-white/45">{shot.visual_description || "暂无画面描述"}</span></span>
        </label>)}
        {!shots.length && <p className="p-3 text-sm text-white/45">本集还没有分镜。</p>}
      </div>
    </div>}
    <button type="button" onClick={() => void runPreflight()} disabled={busy || !checkpoint || !projectId || !episodeId || !selected.length} className="rounded-lg border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100 disabled:opacity-40">{busy ? "处理中…" : "检查提示词与分镜归属"}</button>
    {preflight && <div className="space-y-2 rounded-xl border border-white/10 bg-black/20 p-3">
      <p className="text-sm">预检结果：可生成 {preflight.ready_count} 个，需补充或无权访问 {preflight.missing_count} 个。</p>
      {preflight.ready_shots.map((shot) => <p key={shot.shot_id} className="text-xs text-emerald-200">镜头 {shot.shot_number}：提示词已就绪</p>)}
      {preflight.missing_shots.map((shot) => <p key={shot.shot_id} className="text-xs text-amber-200">镜头 #{shot.shot_id}：{shot.reason}</p>)}
      {preflight.ready_count > 0 && <div className="space-y-3 border-t border-white/10 pt-3">
        <label className="flex items-start gap-2 text-sm text-amber-100"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-1" />我确认将 {preflight.ready_count} 个分镜任务提交到本机 GPU 队列，可能持续占用显存、电力和时间。</label>
        <button type="button" onClick={() => void submitBatch()} disabled={busy || !confirmed} className="rounded-lg bg-fuchsia-300 px-4 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40">确认并提交批量生图</button>
      </div>}
    </div>}
    {error && <p role="alert" className="text-sm text-rose-200">{error}</p>}
    {result && <div className="space-y-2 rounded-xl border border-emerald-300/20 bg-emerald-950/15 p-3">
      <p className="text-sm">已提交 {result.submitted_count} 个任务；跳过 {result.skipped_count} 个；提交失败 {result.failed_count} 个。</p>
      {result.tasks.map((task) => <p key={task.prompt_id} className="break-all text-xs text-emerald-200">镜头 {task.shot_number} · 任务 {task.prompt_id} · 种子 {task.seed}</p>)}
      {result.failed_shots.map((item) => <p key={item.shot_id} className="text-xs text-rose-200">镜头 {item.shot_number}：{item.reason}</p>)}
      {result.skipped_shots.map((item) => <p key={item.shot_id} className="text-xs text-amber-200">镜头 #{item.shot_id}：{item.reason}</p>)}
    </div>}
    <p className="text-xs text-white/40">批量提交最多 20 个镜头，ComfyUI 按提交顺序排队。每张图片仍需单独查看、确认归档；失败任务不会自动重试，也不会调用云端服务。</p>
  </section>;
}

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
  const [libraryImages, setLibraryImages] = useState<LibraryImage[]>([]);
  const [reuseMediaId, setReuseMediaId] = useState("");
  const [reusingImage, setReusingImage] = useState(false);
  const [replaceMediaIds, setReplaceMediaIds] = useState<Record<number, string>>({});
  const [versionsByLink, setVersionsByLink] = useState<Record<number, ImageVersion[]>>({});
  const [loadingVersions, setLoadingVersions] = useState<number | null>(null);
  const [restoringVersion, setRestoringVersion] = useState<string | null>(null);

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
    setVersionsByLink({});
    setReplaceMediaIds({});
    fetch(API + "/api/local-ai/images/library/images", { cache: "no-store" })
      .then(async (response) => { const data = await response.json(); if (!response.ok) throw new Error(data.detail || "读取素材库图片失败"); if (active) setLibraryImages(Array.isArray(data.images) ? data.images : []); })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "读取素材库图片失败"); });
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

  async function refreshShotImages() {
    if (!targetShotId) return;
    const response = await fetch(API + "/api/local-ai/images/shots/" + targetShotId + "/media", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "刷新分镜图片失败");
    setShotImages(Array.isArray(data.images) ? data.images : []);
  }

  async function reuseLibraryImage() {
    if (!targetShotId || !reuseMediaId) return;
    setReusingImage(true); setError("");
    try {
      const response = await fetch(API + "/api/local-ai/images/shots/" + targetShotId + "/media/reuse", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ media_file_id: Number(reuseMediaId) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "复用素材图片失败");
      await refreshShotImages();
      setMessage(data.already_linked ? "这张图片已经关联到当前分镜。" : "已将素材库图片复用到当前分镜；原素材没有复制或移动。");
    } catch (err) { setError(err instanceof Error ? err.message : "复用素材图片失败"); }
    finally { setReusingImage(false); }
  }

  async function replaceShotImage(linkId: number) {
    const mediaId = replaceMediaIds[linkId];
    if (!targetShotId || !mediaId) return;
    setError("");
    try {
      const response = await fetch(API + "/api/local-ai/images/shots/" + targetShotId + "/media/" + linkId, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ media_file_id: Number(mediaId) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "替换分镜图片失败");
      await refreshShotImages();
      setMessage("图片已替换，旧版本仍保存在该分镜的版本历史中。");
      await loadImageVersions(linkId);
    } catch (err) { setError(err instanceof Error ? err.message : "替换分镜图片失败"); }
  }

  async function loadImageVersions(linkId: number) {
    if (!targetShotId) return;
    setLoadingVersions(linkId); setError("");
    try {
      const response = await fetch(API + "/api/local-ai/images/shots/" + targetShotId + "/media/" + linkId + "/versions", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "读取图片版本失败");
      setVersionsByLink((current) => ({ ...current, [linkId]: Array.isArray(data.versions) ? data.versions : [] }));
    } catch (err) { setError(err instanceof Error ? err.message : "读取图片版本失败"); }
    finally { setLoadingVersions(null); }
  }

  async function restoreImageVersion(linkId: number, version: number) {
    if (!targetShotId) return;
    const key = linkId + ":" + version;
    setRestoringVersion(key); setError("");
    try {
      const response = await fetch(API + "/api/local-ai/images/shots/" + targetShotId + "/media/" + linkId + "/restore/" + version, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "恢复图片版本失败");
      await refreshShotImages();
      await loadImageVersions(linkId);
      setMessage("已恢复所选图片版本；原有版本记录仍然保留。");
    } catch (err) { setError(err instanceof Error ? err.message : "恢复图片版本失败"); }
    finally { setRestoringVersion(null); }
  }

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


      <ReferenceCapabilityPanel />\n      {targetProjectId && <StoryboardConsistencyPanel projectId={targetProjectId} />}
      <StoryboardBatchImagePanel projects={targets} checkpoint={checkpoint} style={style} width={width} height={height} steps={steps} />\n\n      {targetShotId && <section className="rounded-2xl border border-white/10 bg-slate-950/70 p-5">
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
              <label className="block text-xs text-white/60">替换为素材库图片
                <select value={replaceMediaIds[item.link_id] || ""} onChange={(event) => setReplaceMediaIds((current) => ({ ...current, [item.link_id]: event.target.value }))} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 p-2">
                  <option value="">选择已有图片</option>
                  {libraryImages.filter((image) => image.id !== item.media_file_id).map((image) => <option key={image.id} value={image.id}>#{image.id} · {image.name}</option>)}
                </select>
              </label>
              <button type="button" onClick={() => void replaceShotImage(item.link_id)} disabled={!replaceMediaIds[item.link_id]} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-xs text-cyan-100 disabled:opacity-40">替换并保留旧版本</button>
              <button type="button" onClick={() => void (versionsByLink[item.link_id] ? setVersionsByLink((current) => { const next = { ...current }; delete next[item.link_id]; return next; }) : loadImageVersions(item.link_id))} disabled={loadingVersions === item.link_id} className="rounded-lg border border-white/20 px-3 py-2 text-xs disabled:opacity-40">{loadingVersions === item.link_id ? "读取中…" : versionsByLink[item.link_id] ? "收起版本历史" : "查看版本历史"}</button>
              {versionsByLink[item.link_id] && <div className="space-y-2 rounded-lg bg-black/20 p-2">
                {versionsByLink[item.link_id].map((version) => <div key={version.version_number} className="flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate">版本 {version.version_number} · {version.name} {version.current ? "（当前）" : ""}</span>
                  {!version.current && <button type="button" onClick={() => void restoreImageVersion(item.link_id, version.version_number)} disabled={restoringVersion !== null} className="shrink-0 rounded border border-amber-300/30 px-2 py-1 text-amber-100">{restoringVersion === item.link_id + ":" + version.version_number ? "恢复中…" : "恢复此版本"}</button>}
                </div>)}
              </div>}
              <button type="button" onClick={() => void unlinkShotImage(item.link_id)} disabled={unlinkingImage !== null} className="rounded-lg border border-rose-300/30 px-3 py-2 text-xs text-rose-100 disabled:opacity-40">{unlinkingImage === item.link_id ? "正在解除…" : "仅解除分镜关联"}</button>
            </div>
          </article>)}
        </div> : <p className="mt-3 text-sm text-white/50">该分镜暂时没有关联图片。归档新图片时选择此镜头即可添加。</p>}
        <div className="mt-4 rounded-xl border border-cyan-300/20 bg-cyan-950/15 p-3">
          <label className="block text-sm text-white/70">从素材库复用已有图片（最多显示最近 200 张）
            <select value={reuseMediaId} onChange={(event) => setReuseMediaId(event.target.value)} className="mt-2 w-full rounded-lg border border-white/15 bg-slate-900 p-3">
              <option value="">选择素材库图片</option>
              {libraryImages.map((image) => <option key={image.id} value={image.id}>#{image.id} · {image.name}</option>)}
            </select>
          </label>
          <button type="button" onClick={() => void reuseLibraryImage()} disabled={!reuseMediaId || reusingImage} className="mt-2 rounded-lg border border-cyan-300/30 px-3 py-2 text-sm text-cyan-100 disabled:opacity-40">{reusingImage ? "正在关联…" : "复用到当前分镜"}</button>
          <p className="mt-2 text-xs text-white/45">复用只新增分镜关联，不会复制、移动或删除素材文件；同一张图可以关联多个分镜。</p>
        </div>
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
