"use client";

import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type RuntimeModel = { name: string; size_bytes?: number | null };
type RuntimeInfo = { status: "ready" | "not_running"; models: RuntimeModel[]; message: string };

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
  const [loading, setLoading] = useState(false);

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

  useEffect(() => { void refresh(); }, [refresh]);

  async function generate() {
    if (!model) { setError("请先安装并选择一个本地模型"); return; }
    if (!source.trim()) { setError("请先粘贴小说章节、剧情梗概或创作素材"); return; }
    setLoading(true);
    setError("");
    setResult("");
    try {
      const response = await fetch(`${API}/api/local-ai/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, task, style, source_text: source, extra_requirements: extra }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "本地生成失败");
      setResult(data.response || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "本地生成失败；没有切换到云端");
    } finally {
      setLoading(false);
    }
  }

  async function copyResult() {
    try {
      await navigator.clipboard.writeText(result);
    } catch {
      setError("复制失败，请手动选中生成结果复制");
    }
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-5 text-white">
      <header>
        <p className="text-sm text-cyan-300">本地 AI 创作</p>
        <h1 className="mt-1 text-2xl font-bold">本地创作工作台</h1>
        <p className="mt-2 text-sm text-white/60">使用电脑已安装的 Ollama 模型生成文字内容。不会调用云端 API，不会自动下载模型，也不会自动保存或覆盖原稿。</p>
      </header>

      <section className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">本地引擎状态</h2>
            <p className="mt-1 text-sm text-white/60">{runtime?.message || "正在检测本机 Ollama……"}</p>
          </div>
          <button onClick={() => void refresh()} className="rounded-lg border border-white/20 px-3 py-2 text-sm">重新检测</button>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">已安装模型
            <select value={model} onChange={(e) => setModel(e.target.value)} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-900 p-3">
              <option value="">请选择本地模型</option>
              {(runtime?.models || []).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
            </select>
          </label>
          <label className="text-sm">创作任务
            <select value={task} onChange={(e) => setTask(e.target.value)} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-900 p-3">
              {tasks.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label className="text-sm">画面风格
            <select value={style} onChange={(e) => setStyle(e.target.value)} className="mt-1 w-full rounded-lg border border-white/15 bg-slate-900 p-3">
              <option>国漫</option><option>仿真人</option><option>3D漫</option><option>2D漫</option>
            </select>
          </label>
        </div>
        {runtime?.status !== "ready" && <p className="mt-3 text-sm text-amber-200">Ollama 当前未运行或不可连接。请先在电脑上自行启动 Ollama；本程序不会替你安装或启动它。</p>}
        {runtime?.status === "ready" && runtime.models.length === 0 && <p className="mt-3 text-sm text-amber-200">已连接 Ollama，但没有发现已安装模型。请自行安装模型后重新检测。</p>}
      </section>

      <section className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <h2 className="font-semibold">输入创作素材</h2>
        <p className="mt-1 text-sm text-white/55">可以粘贴小说章节、剧情梗概、人物资料或已有分镜。单次最多 12,000 字符。</p>
        <textarea value={source} onChange={(e) => setSource(e.target.value)} maxLength={12000} rows={10} placeholder="请粘贴需要处理的小说内容或创作素材……" className="mt-3 w-full rounded-xl border border-white/15 bg-slate-950 p-3 text-sm leading-6 outline-none focus:border-cyan-400" />
        <div className="mt-3">
          <label className="text-sm">额外要求（选填）</label>
          <textarea value={extra} onChange={(e) => setExtra(e.target.value)} maxLength={3000} rows={3} placeholder="例如：保持原著剧情顺序；每集约 2 分钟；人物服装保持连续一致……" className="mt-1 w-full rounded-xl border border-white/15 bg-slate-950 p-3 text-sm leading-6 outline-none focus:border-cyan-400" />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button disabled={loading || !model || !source.trim() || runtime?.status !== "ready"} onClick={() => void generate()} className="rounded-xl bg-cyan-500 px-5 py-3 font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40">
            {loading ? "本地模型正在生成，请稍候……" : "开始本地生成"}
          </button>
          <span className="text-xs text-white/45">生成速度取决于电脑 CPU、内存、显卡和模型大小。</span>
        </div>
        {error && <p role="alert" className="mt-3 rounded-lg bg-red-500/10 p-3 text-sm text-red-200">{error}</p>}
      </section>

      {result && <section className="rounded-2xl border border-cyan-300/20 bg-cyan-300/5 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold">生成结果</h2>
          <button onClick={() => void copyResult()} className="rounded-lg border border-cyan-200/30 px-3 py-2 text-sm">复制全部内容</button>
        </div>
        <pre className="mt-3 whitespace-pre-wrap break-words font-sans text-sm leading-7">{result}</pre>
        <p className="mt-4 text-xs text-white/50">结果仅显示在本页面，尚未写入小说或剧本文件。请检查内容后再复制到对应模块保存。</p>
      </section>}
    </main>
  );
}
