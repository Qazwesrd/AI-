"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type TextRuntime = {
  status?: string;
  base_url?: string;
  models?: Array<{ name: string; size_bytes?: number | null }>;
  message?: string;
};
type ImageRuntime = {
  status?: string;
  base_url?: string;
  checkpoint_count?: number;
  checkpoints?: string[];
  message?: string;
  hardware?: { reasons?: string[]; note?: string };
};
type Diagnostics = {
  media_tools?: {
    ffmpeg?: { status?: string; path?: string; message?: string };
    ffprobe?: { status?: string; path?: string; message?: string };
  };
  hardware?: { cpu?: { processor?: string }; memory?: { total_gib?: number }; gpu?: { name?: string; vendor?: string } };
};

function State({ value }: { value?: string }) {
  const ready = value === "ready" || value === "ok";
  return <span className={`rounded-full px-3 py-1 text-xs ${ready ? "bg-emerald-500/15 text-emerald-300" : "bg-amber-500/15 text-amber-200"}`}>{ready ? "已连接" : value === "not_running" ? "未启动" : value === "error" ? "异常" : "未检测"}</span>;
}
function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5"><h2 className="mb-4 text-lg font-bold">{title}</h2>{children}</section>;
}

export default function LocalAICenterPage() {
  const [textRuntime, setTextRuntime] = useState<TextRuntime | null>(null);
  const [imageRuntime, setImageRuntime] = useState<ImageRuntime | null>(null);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const [textResponse, imageResponse, diagnosticsResponse] = await Promise.all([
        fetch(`${API}/api/local-ai/runtime`, { cache: "no-store" }),
        fetch(`${API}/api/local-ai/image-runtime`, { cache: "no-store" }),
        fetch(`${API}/api/system/diagnostics`, { cache: "no-store" }),
      ]);
      const [textData, imageData, diagnosticsData] = await Promise.all([
        textResponse.json(), imageResponse.json(), diagnosticsResponse.json(),
      ]);
      if (!textResponse.ok) throw new Error(textData.detail || "读取本地文字 AI 状态失败");
      if (!imageResponse.ok) throw new Error(imageData.detail || "读取本地图片 AI 状态失败");
      if (!diagnosticsResponse.ok) throw new Error(diagnosticsData.detail || "读取多媒体引擎状态失败");
      setTextRuntime(textData);
      setImageRuntime(imageData);
      setDiagnostics(diagnosticsData);
    } catch (e) {
      setError(e instanceof Error ? e.message : "本地引擎状态读取失败");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  async function testTextModel(model: string) {
    setBusy(true); setTestResult(""); setError("");
    try {
      const response = await fetch(`${API}/api/local-ai/test`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "本地模型测试失败");
      setTestResult(data.response || data.message || "本地模型测试通过");
    } catch (e) {
      setError(e instanceof Error ? e.message : "本地模型测试失败");
    } finally {
      setBusy(false);
    }
  }

  const ffmpeg = diagnostics?.media_tools?.ffmpeg;
  const ffprobe = diagnostics?.media_tools?.ffprobe;
  const models = textRuntime?.models || [];

  return <main className="cosmic min-h-screen text-white">
    <div className="nebula nebula-one" /><div className="nebula nebula-two" />
    <div className="relative z-10 mx-auto max-w-7xl p-5 lg:p-8">
      <header className="mb-7">
        <a href="/" className="text-sm text-purple-300">← 返回首页</a>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <div><h1 className="text-3xl font-black">🧰 本地 AI 引擎与模型中心</h1>
            <p className="mt-2 text-sm text-purple-200">集中查看本地文字模型、图片生成引擎和视频处理组件。状态只以本机检测结果为准。</p></div>
          <button onClick={() => void refresh()} disabled={busy} className="rounded-xl border border-purple-300/30 bg-purple-700/40 px-4 py-2 text-sm disabled:opacity-50">{busy ? "正在检测…" : "↻ 刷新检测"}</button>
        </div>
      </header>

      {error && <div className="mb-5 rounded-xl border border-rose-400/30 bg-rose-950/30 p-4 text-sm text-rose-200">{error}</div>}
      {testResult && <div className="mb-5 rounded-xl border border-emerald-400/30 bg-emerald-950/30 p-4 text-sm text-emerald-100"><b>本地模型测试结果</b><p className="mt-2 whitespace-pre-wrap">{testResult}</p></div>}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="📝 本地文字 AI · Ollama">
          <div className="mb-3 flex items-center justify-between"><span className="text-sm text-purple-200">服务状态</span><State value={textRuntime?.status} /></div>
          <p className="text-xs text-purple-300">服务地址：{textRuntime?.base_url || "127.0.0.1:11435"}</p>
          <div className="mt-4 rounded-xl bg-black/20 p-4">
            <div className="mb-2 text-sm font-semibold">已安装模型（本机实际清单）</div>
            {models.length ? <ul className="space-y-2">{models.map((model) => <li key={model.name} className="flex flex-wrap items-center justify-between gap-2 text-sm"><span>{model.name}</span><button onClick={() => void testTextModel(model.name)} disabled={busy} className="rounded-lg bg-purple-700/60 px-3 py-1.5 text-xs disabled:opacity-50">测试模型</button></li>)}</ul> : <p className="text-xs text-amber-200">未读取到已安装模型；不会在此页面自动下载模型。</p>}
          </div>
          <p className="mt-3 text-xs leading-6 text-purple-300">用途：小说分析、人物设定、剧本、对白、剧情审核、分镜提示词。当前兼容已有 Qwen 模型；新一代 Qwen3.5:4b 可作为后续离线资源包升级目标，不能把尚未安装的模型显示为已就绪。</p>
          <a href="/ai-analysis" className="mt-4 inline-block rounded-lg border border-purple-300/30 px-3 py-2 text-sm">进入小说分析与剧情审核 →</a>
        </Panel>

        <Panel title="🎨 本地图片 AI · ComfyUI">
          <div className="mb-3 flex items-center justify-between"><span className="text-sm text-purple-200">服务状态</span><State value={imageRuntime?.status} /></div>
          <p className="text-xs text-purple-300">服务地址：{imageRuntime?.base_url || "127.0.0.1:8188"}</p>
          <div className="mt-4 rounded-xl bg-black/20 p-4">
            <div className="text-sm font-semibold">已识别图片模型：{imageRuntime?.checkpoint_count ?? 0} 个</div>
            {imageRuntime?.checkpoints?.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">{imageRuntime.checkpoints.map((name) => <li key={name}>{name}</li>)}</ul> : <p className="mt-2 text-xs text-amber-200">未检测到已安装 checkpoint。图片工作台入口存在不代表模型已经安装。</p>}
          </div>
          <p className="mt-3 text-xs leading-6 text-purple-300">用途：人物立绘、场景、道具、分镜图。会按显卡与内存能力选择工作流；低显存/核显环境需要低分辨率或 CPU 模式，速度可能较慢。</p>
          <a href="/local-image-studio" className="mt-4 inline-block rounded-lg border border-purple-300/30 px-3 py-2 text-sm">进入本地图片生成工作台 →</a>
        </Panel>

        <Panel title="🎞️ 本地视频处理 · FFmpeg">
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between gap-3"><span>FFmpeg</span><State value={ffmpeg?.status} /></div>
            <p className="break-all text-xs text-purple-300">{ffmpeg?.path || ffmpeg?.message || "尚未检测"}</p>
            <div className="flex items-center justify-between gap-3"><span>FFprobe</span><State value={ffprobe?.status} /></div>
            <p className="break-all text-xs text-purple-300">{ffprobe?.path || ffprobe?.message || "尚未检测"}</p>
          </div>
          <p className="mt-3 text-xs leading-6 text-purple-300">用途：视频合成、剪辑、转码、字幕处理和导出。以桌面程序实际绑定的 FFmpeg/FFprobe 路径为准。</p>
          <a href="/video-editor" className="mt-4 inline-block rounded-lg border border-purple-300/30 px-3 py-2 text-sm">进入视频剪辑工作台 →</a>
        </Panel>

        <Panel title="💻 本机硬件与运行建议">
          <div className="space-y-2 text-sm">
            <p>处理器：{diagnostics?.hardware?.cpu?.processor || "检测中"}</p>
            <p>内存：{diagnostics?.hardware?.memory?.total_gib ? `${diagnostics.hardware.memory.total_gib} GiB` : "检测中"}</p>
            <p>独立显卡：{diagnostics?.hardware?.gpu?.name || "未检测到 NVIDIA 独显（不代表没有 AMD 核显）"}</p>
          </div>
          <p className="mt-3 text-xs leading-6 text-purple-300">建议把 Ollama、ComfyUI、模型权重和生成缓存统一放在数据盘；程序升级时保留资源目录，避免重复下载。核心功能不应静默切换到云端或产生费用。</p>
          <a href="/system-diagnostics" className="mt-4 inline-block rounded-lg border border-purple-300/30 px-3 py-2 text-sm">查看完整系统诊断 →</a>
        </Panel>
      </div>
    </div>
  </main>;
}
