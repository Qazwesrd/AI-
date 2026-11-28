"use client";

import { useCallback, useEffect, useState } from "react";

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes > 0 ? `${minutes}分${remainder}秒` : `${remainder}秒`;
}

async function readApiError(response: Response, fallback: string) {
  let detail = "";
  try {
    const data = await response.json();
    detail = typeof data.detail === "string" ? data.detail : "";
  } catch {
    detail = "";
  }
  if (response.status === 504) {
    return detail || "本地模型仍在推理，但等待响应超时。请检查 Ollama 窗口和电脑资源；不要立即重复提交。";
  }
  if (response.status === 503) {
    return detail || "生成请求无法连接本机 Ollama。请重新检测本地引擎状态。";
  }
  return detail || `${fallback}（HTTP ${response.status}）`;
}


const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
type RuntimeModel = { name: string; size_bytes?: number | null };
type RuntimeInfo = { status: "ready" | "not_running"; models: RuntimeModel[]; message: string };
type Novel = { id: number; title: string; chapter_count?: number };
type Chapter = { id: number; chapter_number: number; title: string; word_count: number };
type NovelContext = {
  novel_title: string; chapter_title: string; chapter_number: number; content: string;
  confirmed_continuity: Array<{ category: string; name: string; description: string; data: Record<string, unknown> }>;
  source_text_truncated: boolean; continuity_truncated: boolean;
};
const tasks = [
  { value: "screenplay", label: "剧本生成" },
  { value: "character", label: "人物设定整理" },
  { value: "plot", label: "剧情分析" },
  { value: "storyboard", label: "分镜生成" },
  { value: "image_prompt", label: "图片提示词生成" },
];

export default function LocalCreatorPage() {
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null);
  const [model, setModel] = useState("");
  const [task, setTask] = useState("screenplay");
  const [style, setStyle] = useState("国漫");
  const [source, setSource] = useState("");
  const [extra, setExtra] = useState("");
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [generationStartedAt, setGenerationStartedAt] = useState<number | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [novels, setNovels] = useState<Novel[]>([]);
  const [novelId, setNovelId] = useState("");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [chapterId, setChapterId] = useState("");
  const [contextLoading, setContextLoading] = useState(false);
  const [packageLoading, setPackageLoading] = useState(false);
  const [packageStages, setPackageStages] = useState<Array<{ stage: string; title: string; content: string }>>([]);

  const refresh = useCallback(async () => {
    setError("");
    try {
      const response = await fetch(`${API}/api/local-ai/runtime`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "本地引擎检测失败");
      setRuntime(data);
      setModel((current) => current || data.models?.[0]?.name || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "本地引擎检测失败");
    }
  }, []);

  useEffect(() => {
    if (!loading && !packageLoading) return;
    const timer = window.setInterval(() => {
      if (generationStartedAt !== null) {
        setElapsedSeconds(Math.max(0, Math.floor((Date.now() - generationStartedAt) / 1000)));
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [loading, packageLoading, generationStartedAt]);

  useEffect(() => {
    void refresh();
    fetch(`${API}/api/novels`, { cache: "no-store" })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "小说列表读取失败");
        const items = Array.isArray(data.items) ? data.items : [];
        setNovels(items);
        if (items.length) setNovelId(String(items[0].id));
      })
      .catch((err) => setError(err instanceof Error ? err.message : "小说列表读取失败"));
  }, [refresh]);

  useEffect(() => {
    if (!novelId) { setChapters([]); setChapterId(""); return; }
    fetch(`${API}/api/novels/${novelId}/chapters`, { cache: "no-store" })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "章节列表读取失败");
        const items = Array.isArray(data.items) ? data.items : [];
        setChapters(items);
        setChapterId(items.length ? String(items[0].id) : "");
      })
      .catch((err) => setError(err instanceof Error ? err.message : "章节列表读取失败"));
  }, [novelId]);

  async function loadChapterContext() {
    if (!novelId || !chapterId) { setError("请先选择小说和章节"); return; }
    setContextLoading(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${API}/api/local-ai/novel-context/${novelId}/${chapterId}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "章节资料读取失败");
      const context = data as NovelContext;
      const continuityText = context.confirmed_continuity.map((item) =>
        `【${item.category}】${item.name}\n${item.description}\n${JSON.stringify(item.data)}`
      ).join("\n\n");
      const continuityBlock = continuityText ? `\n\n【已确认的连续性资料】\n${continuityText}` : "";
      const chapterBudget = Math.max(1000, 12000 - Math.min(continuityBlock.length, 4000));
      const combined = `【小说】${context.novel_title}\n【章节】第${context.chapter_number}章 ${context.chapter_title}\n\n【章节正文】\n${context.content.slice(0, chapterBudget)}${continuityBlock.slice(0, 4000)}`;
      setSource(combined.slice(0, 12000));
      setNotice(`已载入“${context.novel_title} / ${context.chapter_title}”。${context.source_text_truncated || context.continuity_truncated || combined.length > 12000 ? "资料较长，部分内容已按安全长度截取。" : ""}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "章节资料读取失败");
    } finally {
      setContextLoading(false);
    }
  }

  async function generate() {
    if (!model) { setError("请先安装并选择一个本地模型"); return; }
    if (!source.trim()) { setError("请先选择章节载入资料，或粘贴创作素材"); return; }
    setLoading(true); setError(""); setResult(""); setGenerationStartedAt(Date.now()); setElapsedSeconds(0);
    try {
      const response = await fetch(`${API}/api/local-ai/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, task, style, source_text: source, extra_requirements: extra }),
      });
      if (!response.ok) throw new Error(await readApiError(response, "本地生成失败"));
      const data = await response.json();
      setResult(data.response || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "本地生成失败；没有切换到云端");
    } finally { setLoading(false); setGenerationStartedAt(null); }
  }

  async function generatePackage() {
    if (!model || !source.trim()) { setError("请先选择本地模型并载入章节资料"); return; }
    setPackageLoading(true); setError(""); setResult(""); setPackageStages([]); setGenerationStartedAt(Date.now()); setElapsedSeconds(0);
    try {
      const response = await fetch(`${API}/api/local-ai/generate-package`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model, style, source_text: source, extra_requirements: extra }) });
      if (!response.ok) throw new Error(await readApiError(response, "整章创作包生成失败"));
      const data = await response.json();
      setPackageStages(data.stages || []);
      setNotice("五阶段创作包已生成，结果仅保存在当前页面，尚未写入原稿。");
    } catch (err) { setError(err instanceof Error ? err.message : "本地创作包生成失败；没有切换到云端"); }
    finally { setPackageLoading(false); setGenerationStartedAt(null); }
  }

  async function copyResult() {
    try { await navigator.clipboard.writeText(result); }
    catch { setError("复制失败，请手动选中生成结果复制"); }
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-5 text-white">
      <header>
        <p className="text-sm text-cyan-300">本地 AI 创作</p>
        <h1 className="mt-1 text-2xl font-bold">本地创作工作台</h1>
        <p className="mt-2 text-sm text-white/60">从小说章节自动载入正文和已确认的人物、场景、道具资料，再使用本机 Ollama 生成。不会调用云端 API、自动下载模型或自动覆盖原稿。</p>
      </header>

      <section className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="font-semibold">本地引擎状态</h2><p className="mt-1 text-sm text-white/60">{runtime?.message || "正在检测本机 Ollama……"}</p></div>
          <button onClick={() => void refresh()} className="rounded-lg border border-white/20 px-3 py-2 text-sm">重新检测</button>
        </div>
        <label className="mt-4 block text-sm">已安装模型
          <select value={model} onChange={(e) => setModel(e.target.value)} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-900 p-3">
            <option value="">请选择本地模型</option>{(runtime?.models || []).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select>
        </label>
        {runtime?.status !== "ready" && <p className="mt-3 text-sm text-amber-200">Ollama 当前未运行或不可连接。请自行启动 Ollama；本程序不会替你安装或启动它。</p>}
        {runtime?.status === "ready" && runtime.models.length === 0 && <p className="mt-3 text-sm text-amber-200">已连接 Ollama，但没有发现已安装模型。请自行安装模型后重新检测。</p>}
      </section>

      <section className="rounded-2xl border border-cyan-300/20 bg-cyan-300/5 p-4">
        <h2 className="font-semibold">从小说章节自动载入</h2>
        <p className="mt-1 text-sm text-white/55">只读取当前小说章节和已经确认、未过期的人物/场景/道具/剧情资料，不会把未确认或旧版本资料当作事实。</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">选择小说
            <select value={novelId} onChange={(e) => setNovelId(e.target.value)} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-900 p-3">
              <option value="">请选择小说</option>{novels.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </label>
          <label className="text-sm">选择章节
            <select value={chapterId} onChange={(e) => setChapterId(e.target.value)} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-900 p-3">
              <option value="">请选择章节</option>{chapters.map((item) => <option key={item.id} value={item.id}>第{item.chapter_number}章 · {item.title}</option>)}
            </select>
          </label>
        </div>
        <button disabled={!novelId || !chapterId || contextLoading} onClick={() => void loadChapterContext()} className="mt-3 rounded-lg bg-cyan-500 px-4 py-2.5 text-sm font-semibold text-slate-950 disabled:opacity-40">{contextLoading ? "正在读取章节资料…" : "载入章节与人物连续性资料"}</button>
        {notice && <p className="mt-3 text-sm text-emerald-200">{notice}</p>}
      </section>

      <section className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <h2 className="font-semibold">创作任务与风格</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">创作任务
            <select value={task} onChange={(e) => setTask(e.target.value)} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-900 p-3">{tasks.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
          </label>
          <label className="text-sm">画面风格
            <select value={style} onChange={(e) => setStyle(e.target.value)} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-900 p-3"><option>国漫</option><option>仿真人</option><option>3D漫</option><option>2D漫</option></select>
          </label>
        </div>
        <label className="mt-4 block text-sm">章节正文与连续性资料（可编辑，最多 12,000 字符）</label>
        <textarea value={source} onChange={(e) => setSource(e.target.value)} maxLength={12000} rows={12} placeholder="选择上方小说章节后点击载入，也可以直接粘贴素材……" className="mt-1 w-full rounded-xl border border-white/15 bg-slate-950 p-3 text-sm leading-6 outline-none focus:border-cyan-400" />
        <label className="mt-3 block text-sm">额外要求（选填）</label>
        <textarea value={extra} onChange={(e) => setExtra(e.target.value)} maxLength={3000} rows={3} placeholder="例如：严格按原著顺序；人物服装、发型、道具保持连续一致；每集约 2 分钟……" className="mt-1 w-full rounded-xl border border-white/15 bg-slate-950 p-3 text-sm leading-6 outline-none focus:border-cyan-400" />
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button disabled={loading || !model || !source.trim() || runtime?.status !== "ready"} onClick={() => void generate()} className="rounded-xl bg-cyan-500 px-5 py-3 font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40">{loading ? "本地模型正在生成，请稍候……" : "开始本地生成"}</button>
          <span className="text-xs text-white/45">生成速度取决于电脑 CPU、内存、显卡和模型大小。</span>
        </div>
        <button disabled={packageLoading || loading || !model || !source.trim() || runtime?.status !== "ready"} onClick={() => void generatePackage()} className="mt-3 rounded-xl border border-cyan-300/40 px-5 py-3 font-semibold text-cyan-100 disabled:cursor-not-allowed disabled:opacity-40">{packageLoading ? "正在依次生成五个阶段，请耐心等待…" : "一键生成整章创作包（5阶段）"}</button>
        <p className="mt-2 text-xs text-white/45">整套流程会连续调用本机模型五次，速度取决于电脑性能；不会产生云端 API 费用，也不会自动保存。</p>
        {(loading || packageLoading) && <div role="status" aria-live="polite" className="mt-4 rounded-xl border border-cyan-300/30 bg-cyan-300/10 p-4"><p className="font-semibold text-cyan-100">{packageLoading ? "整章创作包正在本机生成" : "本地模型正在推理"}</p><p className="mt-1 text-sm text-white/75">已等待 {formatElapsed(elapsedSeconds)}。Ollama 状态检测成功只代表服务可访问，不代表模型已完成生成；CPU 推理可能需要较长时间。</p><p className="mt-1 text-xs text-white/55">请保持 Ollama 运行，不要重复点击提交。此页面不会自动切换云端，也不会因等待时间较长而重复发送请求。</p></div>}
        {error && <p role="alert" className="mt-3 rounded-lg bg-red-500/10 p-3 text-sm text-red-200">{error}</p>}
      </section>

      {packageStages.length > 0 && <section className="rounded-2xl border border-cyan-300/20 bg-cyan-300/5 p-4"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">整章创作包 · 五阶段结果</h2><button onClick={() => void navigator.clipboard.writeText(packageStages.map((item) => `# ${item.title}\n\n${item.content}`).join("\n\n---\n\n"))} className="rounded-lg border border-cyan-200/30 px-3 py-2 text-sm">复制完整创作包</button></div>{packageStages.map((item) => <details key={item.stage} open className="mt-3 rounded-xl border border-white/10 bg-slate-950/50 p-3"><summary className="cursor-pointer font-semibold">{item.title}</summary><pre className="mt-3 whitespace-pre-wrap break-words font-sans text-sm leading-7">{item.content}</pre></details>)}<p className="mt-3 text-xs text-white/50">内容只保存在当前页面，刷新页面后不会保留；检查后请自行复制保存。</p></section>}

      {result && <section className="rounded-2xl border border-cyan-300/20 bg-cyan-300/5 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">生成结果</h2><button onClick={() => void copyResult()} className="rounded-lg border border-cyan-200/30 px-3 py-2 text-sm">复制全部内容</button></div>
        <pre className="mt-3 whitespace-pre-wrap break-words font-sans text-sm leading-7">{result}</pre>
        <p className="mt-4 text-xs text-white/50">结果尚未写入小说或剧本文件。请检查后再复制保存。</p>
      </section>}
    </main>
  );
}
