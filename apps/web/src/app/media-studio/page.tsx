"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type MediaTask = {
  id: number;
  provider: string;
  model: string;
  status: string;
  prompt: string;
  remote_task_id: string;
  result_path: string;
  error: string;
};

async function readResponse(response: Response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data
      ? String(data.detail)
      : "";
    throw new Error(detail || `请求失败（HTTP ${response.status}）`);
  }
  return data;
}

export default function MediaStudioPage() {
  const [videoProvider, setVideoProvider] = useState("openai");
  const [speechProvider, setSpeechProvider] = useState("openai");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [videoPromptMode, setVideoPromptMode] = useState("cinematic");
  const [duration, setDuration] = useState(10);
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [confirmSpeechPaid, setConfirmSpeechPaid] = useState(false);
  const [tasks, setTasks] = useState<MediaTask[]>([]);
  const [speechText, setSpeechText] = useState("");
  const [speechModel, setSpeechModel] = useState("");
  const [voice, setVoice] = useState("alloy");
  const [forceRegenerate, setForceRegenerate] = useState(false);
  const [message, setMessage] = useState("");
  const [taskListError, setTaskListError] = useState("");
  const [taskLoading, setTaskLoading] = useState(false);
  const [videoSubmitting, setVideoSubmitting] = useState(false);
  const [speechSubmitting, setSpeechSubmitting] = useState(false);
  const [refreshingTask, setRefreshingTask] = useState<number | null>(null);

  async function loadTasks() {
    setTaskLoading(true);
    setTaskListError("");
    try {
      const response = await fetch(`${API}/api/media/video/tasks`, { cache: "no-store" });
      const data = await readResponse(response);
      if (!Array.isArray(data)) throw new Error("任务列表返回格式异常，请刷新或检查 API 服务");
      setTasks(data as MediaTask[]);
    } catch (error) {
      setTaskListError(error instanceof Error ? error.message : "任务列表加载失败，请检查 API 服务");
    } finally {
      setTaskLoading(false);
    }
  }

  useEffect(() => { void loadTasks(); }, []);

  async function createVideo() {
    if (!videoProvider.trim()) {
      setMessage("请填写视频服务商 ID。");
      return;
    }
    if (!prompt.trim()) {
      setMessage("请填写视频内容描述；软件会自动组合镜头、风格和技术要求，不需要先单独生成提示词。");
      return;
    }
    if (!Number.isInteger(duration) || duration < 1 || duration > 600) {
      setMessage("视频时长必须是 1–600 秒之间的整数。");
      return;
    }

    const styleInstruction = videoPromptMode === "animation"
      ? "采用高质量动画电影视觉表现，动作自然流畅，角色造型稳定，色彩统一。"
      : videoPromptMode === "realistic"
      ? "采用真人电影写实视觉，真实光影、自然运动、可信材质和摄影机语言。"
      : "采用电影级镜头语言，强调构图、景深、光影、情绪节奏和连续性。";
    const effectivePrompt = `视频内容与动作：${prompt.trim()}。\n\n生成要求：严格围绕上述内容，不擅自改变人物身份、服装、场景和关键动作；镜头运动平滑，动作前后连贯，避免画面闪烁、肢体畸形、主体漂移和无关文字水印。\n视觉方案：${styleInstruction}\n时长：${duration} 秒。请输出完整、可执行的视频生成结果。`;
    setVideoSubmitting(true);
    setMessage("正在自动组合视频提示词并提交真实视频生成请求……");
    try {
      const response = await fetch(`${API}/api/media/video/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: videoProvider.trim(), model: model.trim(), prompt: effectivePrompt, duration,
          confirm_paid: confirmPaid
        })
      });
      const data = await readResponse(response);
      setMessage(`视频任务已提交，远程任务编号：${data.remote_task_id || "等待服务端返回"}`);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "视频任务提交失败");
    } finally {
      setVideoSubmitting(false);
    }
  }

  async function refreshTask(id: number) {
    setRefreshingTask(id);
    try {
      const response = await fetch(`${API}/api/media/video/tasks/${id}/refresh`, {
        method: "POST"
      });
      const data = await readResponse(response);
      setMessage(`任务状态：${data.status || "已刷新"}`);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "查询失败");
    } finally {
      setRefreshingTask(null);
    }
  }

  async function generateSpeech() {
    if (!speechProvider.trim()) {
      setMessage("请填写配音服务商 ID。");
      return;
    }
    if (!speechText.trim()) {
      setMessage("请先输入需要生成语音的台词或旁白。");
      return;
    }
    if (!voice.trim()) {
      setMessage("请填写声音 ID。");
      return;
    }

    setSpeechSubmitting(true);
    setMessage("正在准备并提交语音生成请求……");
    try {
      const response = await fetch(`${API}/api/media/speech/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: speechProvider.trim(), model: speechModel.trim(), text: speechText.trim(),
          voice: voice.trim(), confirm_paid: confirmSpeechPaid, force_regenerate: forceRegenerate
        })
      });
      const data = await readResponse(response);
      setMessage(`配音完成：任务 #${data.task_id ?? "—"} · ${data.file || "结果已返回"}${data.reused ? "（复用已有配音，未重复调用 AI）" : ""}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "配音失败");
    } finally {
      setSpeechSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen p-8 text-white">
      <div className="mx-auto max-w-6xl space-y-8">
        <header>
          <h1 className="text-3xl font-bold">AI 视频与音频生产中心</h1>
          <p className="mt-2 text-white/60">视频生成、AI 配音、任务查询与媒体合成</p>
          <a href="/tasks" className="mt-3 inline-flex rounded-lg border border-purple-300/25 px-3 py-2 text-sm text-purple-200 hover:bg-purple-950/40">
            查看统一任务中心（含配音任务）
          </a>
        </header>

        {message && <div role="status" aria-live="polite" className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4">{message}</div>}

        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
            <h2 className="mb-5 text-xl font-semibold">真实 AI 视频生成</h2>
            <label className="mb-2 block text-sm" htmlFor="video-provider">服务商 ID</label>
            <input id="video-provider" value={videoProvider} onChange={e => setVideoProvider(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm" htmlFor="video-model">视频模型名称</label>
            <input id="video-model" value={model} onChange={e => setModel(e.target.value)}
              placeholder="填写服务商提供的视频模型 ID"
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm" htmlFor="video-prompt">视频内容描述</label>
            <textarea id="video-prompt" value={prompt} onChange={e => setPrompt(e.target.value)}
              rows={5} className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3"
              placeholder="描述人物动作、场景、镜头内容；软件会自动将描述组合成完整视频提示词。" />
            <label className="mb-2 block text-sm" htmlFor="video-style">提示词方案（自动应用）</label>
            <select id="video-style" value={videoPromptMode} onChange={e => setVideoPromptMode(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3">
              <option value="cinematic">电影级镜头语言（推荐）</option>
              <option value="realistic">真人电影写实</option>
              <option value="animation">动画电影风格</option>
            </select>
            <label className="mb-2 block text-sm" htmlFor="video-duration">视频时长（秒，1–600）</label>
            <input id="video-duration" type="number" min={1} max={600} step={1} value={duration}
              onChange={e => setDuration(e.target.value === "" ? 0 : Number(e.target.value))}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-4 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={confirmPaid}
                onChange={e => setConfirmPaid(e.target.checked)} />
              我已确认本次视频请求可能产生 API 费用
            </label>
            <button onClick={() => void createVideo()} disabled={videoSubmitting || speechSubmitting}
              className="rounded-xl bg-purple-600 px-6 py-3 font-semibold hover:bg-purple-500 disabled:cursor-not-allowed disabled:opacity-50">
              {videoSubmitting ? "正在提交…" : "提交视频生成"}
            </button>
          </div>

          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
            <h2 className="mb-5 text-xl font-semibold">AI 配音</h2>
            <label className="mb-2 block text-sm" htmlFor="speech-provider">服务商 ID</label>
            <input id="speech-provider" value={speechProvider} onChange={e => setSpeechProvider(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm" htmlFor="speech-model">语音模型名称</label>
            <input id="speech-model" value={speechModel} onChange={e => setSpeechModel(e.target.value)}
              placeholder="填写已开通的语音模型 ID"
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm" htmlFor="speech-voice">声音 ID</label>
            <input id="speech-voice" value={voice} onChange={e => setVoice(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm" htmlFor="speech-text">配音文本</label>
            <textarea id="speech-text" value={speechText} onChange={e => setSpeechText(e.target.value)}
              rows={5} className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3"
              placeholder="输入需要生成语音的台词或旁白" />
            <label className="mb-3 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={confirmSpeechPaid}
                onChange={e => setConfirmSpeechPaid(e.target.checked)} />
              我已确认本次配音请求可能产生 API 费用
            </label>
            <label className="mb-4 flex items-center gap-2 text-sm text-amber-100/80">
              <input type="checkbox" checked={forceRegenerate}
                onChange={e => setForceRegenerate(e.target.checked)} />
              忽略已有相同配音并重新生成（可能再次收费）
            </label>
            <button onClick={() => void generateSpeech()} disabled={speechSubmitting || videoSubmitting}
              className="rounded-xl bg-indigo-600 px-6 py-3 font-semibold hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50">
              {speechSubmitting ? "正在提交…" : "生成配音"}
            </button>
          </div>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
          <div className="mb-5 flex items-center justify-between gap-3">
            <h2 className="text-xl font-semibold">视频任务队列</h2>
            <button onClick={() => void loadTasks()} disabled={taskLoading}
              className="rounded-lg border border-white/20 px-4 py-2 disabled:cursor-not-allowed disabled:opacity-50">
              {taskLoading ? "刷新中…" : "刷新列表"}
            </button>
          </div>
          {taskListError && (
            <div role="alert" className="mb-4 rounded-xl border border-red-300/25 bg-red-950/25 p-3 text-sm text-red-200">
              任务列表读取失败：{taskListError}
              <button onClick={() => void loadTasks()} disabled={taskLoading} className="ml-3 underline disabled:opacity-50">重试</button>
            </div>
          )}
          <div className="space-y-3">
            {tasks.map(task => (
              <div key={task.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold">任务 #{task.id} · {task.provider}</div>
                    <div className="mt-1 text-sm text-white/60">{task.model} · {task.status}</div>
                  </div>
                  <button onClick={() => void refreshTask(task.id)} disabled={refreshingTask === task.id}
                    className="rounded-lg bg-purple-700 px-4 py-2 disabled:cursor-not-allowed disabled:opacity-50">
                    {refreshingTask === task.id ? "查询中…" : "查询任务"}
                  </button>
                </div>
                <p className="mt-3 text-sm text-white/70">{task.prompt}</p>
                {task.result_path && <p className="mt-2 break-all text-xs text-green-300">{task.result_path}</p>}
                {task.error && <p className="mt-2 text-sm text-red-300">{task.error}</p>}
              </div>
            ))}
            {tasks.length === 0 && !taskListError && (
              <p className="text-white/50">{taskLoading ? "正在读取任务列表…" : "暂无视频任务"}</p>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
